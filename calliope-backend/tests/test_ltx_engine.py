import base64
import json

import pytest
from PIL import Image

from calliope.config import settings
from calliope.engines.h3_graph import validate_request
from calliope.engines.modal_h3 import build_request
from calliope.studio import h3_prompt


@pytest.mark.parametrize("aspect,size", [("9:16", (768, 1344)), ("16:9", (1344, 768)), ("1:1", (1024, 1024))])
def test_ltx_request_uses_its_own_frame_grid_and_first_image(tmp_path, aspect, size):
    image = tmp_path / "first.png"
    Image.new("RGB", (64, 64), "green").save(image)
    payload = build_request(prompt='A cat says in French: "Bonjour."', duration_sec=5.8,
                            aspect_ratio=aspect, seed=42, image_path=image, video_profile="ltx")
    assert (payload["width"], payload["height"]) == size
    assert payload["frames"] == 145
    assert payload["duration_sec"] >= payload["requested_duration_sec"]
    assert base64.b64decode(payload["image_base64"]) == image.read_bytes()
    validate_request(payload)
    from calliope.engines.ltx_graph import build_ltx_graph
    graph = build_ltx_graph(payload, output_prefix="studio/ltx", uploaded_image="first.png")
    assert graph["1"]["inputs"]["image"] == "first.png"
    assert graph["30"]["inputs"]["length"] == graph["32"]["inputs"]["frames_number"] == 145
    assert graph["16"]["inputs"]["video_cfg"] == 1
    assert graph["16"]["inputs"]["audio_cfg"] == 1
    assert "subtitles" in graph["21"]["inputs"]["text"]


def test_ltx_cannot_accept_h3_frames_or_steps():
    payload = build_request(prompt="A cat speaks.", duration_sec=5.8, aspect_ratio="9:16", seed=42, video_profile="ltx")
    with pytest.raises(ValueError):
        validate_request(payload | {"frames": 141})
    with pytest.raises(ValueError):
        validate_request(payload | {"steps": 25})


def test_ltx_silent_export_keeps_joint_sampling_but_omits_audio():
    payload = build_request(prompt="A cat stretches.", duration_sec=2, aspect_ratio="9:16", seed=42, audio_mode="silent", video_profile="ltx")
    from calliope.engines.ltx_graph import build_ltx_graph
    graph = build_ltx_graph(payload, output_prefix="studio/silent")
    assert graph["33"]["inputs"]["audio_latent"] == ["32", 0]
    assert "audio" not in graph["91"]["inputs"]
    assert "1" not in graph


def test_ltx_prompt_preserves_speakers_and_dialogue_without_h3_tags():
    assert hasattr(h3_prompt, "compile_video_prompt"), "A model-specific prompt compiler is required"
    context = {
        "prompt_contract_version": 2, "language": "fr", "speaker_ids": {"father": "S1", "son": "S2"},
        "render_canon": {"style": "Realistic anthropomorphic fruit family, deep detailed room.",
                         "characters": {"father": {"name": "Papa", "appearance": "A large pear in a blue jacket.", "voice": "Energetic masculine French voice."},
                                        "son": {"name": "Fiston", "appearance": "A banana in a red hoodie.", "voice": "Young adult masculine French voice."}},
                         "locations": {}, "props": {}},
    }
    shot = {"character_keys": ["father", "son"], "audio_mode": "native", "camera": "Fixed wide shot.",
            "framing": "Wide shared framing keeps both full bodies and the deep room visible.",
            "action": "The father points at the suitcase.", "duration_sec": 6,
            "dialogue_turns": [{"speaker": "father", "text": "Fais tes valises !"},
                               {"speaker": "son", "text": "Et puis quoi encore ?"}]}
    prompt = h3_prompt.compile_video_prompt(shot, context, first_frame=True, video_profile="ltx")
    assert 'Papa' in prompt and 'Fiston' in prompt
    assert '"Fais tes valises !"' in prompt and '"Et puis quoi encore ?"' in prompt
    assert prompt.index('"Fais tes valises !"') < prompt.index('"Et puis quoi encore ?"')
    assert "French" in prompt and "Fixed wide shot." in prompt
    assert shot["framing"] in prompt
    assert not any(tag in prompt for tag in ("<d>", "<Picture", "integrated_multimodal_description", "[Shot 1]"))


def test_ltx_profile_is_saved_in_queued_video_requests(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import service
    monkeypatch.setattr(queue_manager, "paused", True)
    response = client.patch("/api/studio/config", json={"video_profile": "ltx"})
    assert response.status_code == 200, response.text
    project = client.post("/api/studio/projects", json={"title": "LTX test", "idea": "A cat speaks."}).json()["id"]
    board = client.post(f"/api/studio/projects/{project}/plan", json={"text": "INT. ROOM - DAY\nA cat speaks.", "mode": "script", "use_llm": False}).json()
    shot = board["scenes"][0]["shots"][0]["id"]
    response = client.post(f"/api/studio/shots/{shot}/generate", json={"kind": "video", "request_id": "ltx-test"})
    assert response.status_code == 200, response.text
    with service.database() as conn:
        row = conn.execute("SELECT request_json FROM studio_takes WHERE clip_id=?", (shot,)).fetchone()
    monkeypatch.setattr(settings, "studio_video_profile", "fast")
    request = json.loads(row[0])
    assert request["video_profile"] == "ltx"
    assert "<d>" not in request["prompt"]


def test_ltx_image_dialogue_uses_the_image_style_and_one_voice_cue_per_speaker():
    from calliope.studio.ltx_prompt import compile_ltx_prompt

    context = {"language": "fr", "render_canon": {
        "style": "Flat watercolor painting.", "characters": {
            "father": {"name": "Papa", "appearance": "Pear head, blue jacket.",
                       "voice": "Deep masculine voice."},
            "son": {"name": "Fiston", "appearance": "Banana head, red hoodie.",
                    "voice": "Bright young adult voice."},
            "listener": {"name": "Maman", "appearance": "Apple head, green dress.",
                         "voice": "Warm feminine voice."},
        }, "locations": {}, "props": {}}}
    shot = {"character_keys": ["father", "son", "listener"],
            "camera": "Fixed wide shot.", "framing": "Both speakers remain visible.",
            "dialogue_turns": [
                {"speaker": "father", "text": "Fais tes valises !", "action": "Papa points at the suitcase."},
                {"speaker": "son", "text": "Et puis quoi encore ?"},
                {"speaker": "father", "text": "Tu pars ce soir.", "reaction": "Fiston recoils."},
            ]}
    prompt = compile_ltx_prompt(shot, context, first_frame=True)
    assert "Flat watercolor painting" not in prompt
    assert prompt.count("Deep masculine voice") == 1
    assert prompt.count("Bright young adult voice") == 1
    assert "Warm feminine voice" not in prompt
    assert "Maman" in prompt and "green dress" in prompt
    assert prompt.index('"Fais tes valises !"') < prompt.index('"Et puis quoi encore ?"') < prompt.index('"Tu pars ce soir."')
    assert prompt.index("Papa points at the suitcase") < prompt.index('"Fais tes valises !"')
    assert prompt.index("Fiston recoils") > prompt.index('"Tu pars ce soir."')
    assert "Fixed wide shot" in prompt and shot["framing"] in prompt
    assert "Flat watercolor painting" in compile_ltx_prompt(shot, context, first_frame=False)


def test_ltx_director_keeps_one_action_and_one_exact_line_in_foreground():
    action = 'Momo points at his tray while speaking.'
    line = 'Ce plateau est à moi.'
    sound = 'Audible ventilation hum and distant metal dishes beneath the voice.'
    context = {'language': 'fr', 'director_contract': {
        'action': action, 'positions': {'momo': 'left of the counter'},
        'eyelines': {'momo': 'towards the cook'},
        'chronology': [{'at_sec': .2, 'action': action}],
        'dialogue': [{'speaker': 'momo', 'text': line, 'start_sec': .2, 'end_sec': 1.8}],
    }}
    shot = {'character_keys': ['momo'], 'audio_mode': 'native', 'prompt': action,
            'action': action, 'soundscape': sound,
            'dialogue_turns': [{'speaker': 'momo', 'text': line}]}
    prompt = h3_prompt.compile_video_prompt(shot, context, first_frame=True, video_profile='ltx')
    assert prompt.count(action) == 1
    assert 'At 0.2 seconds' in prompt
    assert prompt.count(f'"{line}"') == 1
    assert sound in prompt
    assert 'foreground' in prompt
    assert 'exactly once' in prompt


def test_ltx_director_renders_each_owned_event_once_even_with_source_annotations():
    actions = ['Keeper raises the radio.', 'Keeper lowers the radio.']
    line = 'Je vous écoute.'
    context = {'language': 'fr', 'director_contract': {
        'action': ' '.join(actions), 'positions': {'keeper': 'left'},
        'eyelines': {'keeper': 'towards the radio'},
        'chronology': [{'at_sec': .2 + i, 'action': action}
                       for i, action in enumerate(actions)],
        'dialogue': [{'speaker': 'keeper', 'text': line, 'start_sec': .2, 'end_sec': 1.5}],
    }}
    shot = {'character_keys': ['keeper'], 'audio_mode': 'native',
            'prompt': ' '.join(actions), 'action': ' '.join(actions), 'reaction': actions[-1],
            'dialogue_turns': [{'speaker': 'keeper', 'text': line,
                                'action': actions[0], 'reaction': actions[-1]}]}
    prompt = h3_prompt.compile_video_prompt(shot, context, True, 'ltx')
    assert all(prompt.count(action) == 1 for action in actions)
    assert prompt.count(f'"{line}"') == 1


def test_ltx_brisk_exchange_uses_concurrent_reactions_and_brief_gaps():
    context = {'language': 'fr', 'directing_profile': {'dialogue_density': 'brisk', 'silences': 'brief'}}
    shot = {'character_keys': ['momo', 'potiron'], 'audio_mode': 'native',
            'dialogue_turns': [{'speaker': 'momo', 'text': 'À moi.'},
                               {'speaker': 'potiron', 'text': "D'accord !"}]}
    prompt = h3_prompt.compile_video_prompt(shot, context, first_frame=True, video_profile='ltx')
    assert 'previous line ends' in prompt
    assert 'while speaking' in prompt
    assert 'facial expressions' in prompt
    assert prompt.index('"À moi."') < prompt.index('"D\'accord !"')
