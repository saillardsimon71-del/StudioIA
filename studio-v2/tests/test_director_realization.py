"""Mise en scène des dialogues et transmission du prompt, sans LLM réel ni GPU."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from core.config import V2Settings  # noqa: E402
from core.ltx_prompt import compile_shot_prompt, validate_ltx_prompt  # noqa: E402
from core.scenario import Project  # noqa: E402
from engines.video import LtxModalEngine  # noqa: E402
from llm import deepseek  # noqa: E402
from llm.deepseek import generate_scenario_once  # noqa: E402


SCENES = (
    {
        "id": "farewell-cafe",
        "idea": "In a station cafe, Maya hides the pain of an impending farewell from her friend.",
        "characters": [
            {
                "id": "maya",
                "name": "Maya",
                "description": "Woman in her thirties with dark wavy hair, rain-dark coat, tired eyes.",
                "voice_hint": "soft controlled",
            },
            {
                "id": "noe",
                "name": "Noé",
                "description": "Man in his thirties with close-cropped hair, holding a folded train ticket.",
                "voice_hint": "warm low",
            },
        ],
        "duration_s": 6.0,
        "camera_setup": "Over-the-shoulder two-shot in rain-streaked cafe",
        "initial_state": "Maya holds a folded letter across the booth; Noé watches her.",
        "final_state": "Maya covers the letter; Noé's hand falls between them.",
        "weak_action": "Maya says she is fine. Noé watches her.",
        "directed_action": (
            "Maya covers the damp letter, forces a steady smile, then looks to the rain; "
            "Noé stops reaching."
        ),
        "speaker_id": "maya",
        "dialogue": "Je vais très bien.",
        "start_sec": 2.0,
        "cues": ("forces a steady smile", "Noé stops reaching"),
    },
    {
        "id": "ferry-crossing",
        "idea": "Elias claims he can face a dangerous crossing alone, hoping his friend will stay.",
        "characters": [
            {
                "id": "elias",
                "name": "Elias",
                "description": "Young man with close-cropped hair, a wet canvas jacket, trembling hands.",
                "voice_hint": "quiet firm",
            },
            {
                "id": "salome",
                "name": "Salomé",
                "description": "Woman in her twenties with short curls, a yellow scarf, rain on her sleeves.",
                "voice_hint": "steady low",
            },
        ],
        "duration_s": 6.0,
        "camera_setup": "Wide two-shot through wet ferry glass",
        "initial_state": "Elias faces the ferry gate, key in hand; Salomé waits beside him.",
        "final_state": "Elias turns back; Salomé holds his sleeve, key still in his palm.",
        "weak_action": "Elias says he can go alone. Salomé watches him.",
        "directed_action": (
            "The camera drifts closer as Elias offers a trembling key; Salomé catches his sleeve, "
            "and he turns back."
        ),
        "speaker_id": "elias",
        "dialogue": "Je peux y aller seul.",
        "start_sec": 2.0,
        "cues": ("camera drifts closer", "catches his sleeve", "turns back"),
    },
    {
        "id": "missing-tart",
        "idea": "Inès denies stealing a tart, but a tiny clue gives her away in the kitchen.",
        "characters": [
            {
                "id": "ines",
                "name": "Inès",
                "description": "Young woman in a red sweater, cinnamon sugar on one cheek.",
                "voice_hint": "bright casual",
            },
            {
                "id": "yann",
                "name": "Yann",
                "description": "Man in a flour-dusted apron, calm eyes, holding an empty plate.",
                "voice_hint": "dry gentle",
            },
        ],
        "duration_s": 5.0,
        "camera_setup": "Kitchen two-shot with tart tin in frame",
        "initial_state": "Inès stands beside the empty tart tin; Yann faces her across the counter.",
        "final_state": "Inès freezes after wiping her cheek; Yann points at the empty tin.",
        "weak_action": "Inès says she touched nothing. Yann looks at her.",
        "directed_action": (
            "Camera pans from the empty tin to Inès's sugared cheek; she wipes, smearing it wider, "
            "as Yann points to the tin."
        ),
        "speaker_id": "ines",
        "dialogue": "J'ai rien touché.",
        "start_sec": 1.4,
        "cues": ("Camera pans", "smearing it wider", "Yann points"),
    },
)


def _project(scene: dict, action_key: str) -> Project:
    shot_id = "shot_1"
    return Project(
        id=scene["id"],
        title=scene["id"],
        idea=scene["idea"],
        visual_style="Naturalistic cinematic realism, muted colors and practical light",
        characters=scene["characters"],
        shots=[{
            "id": shot_id,
            "duration_s": scene["duration_s"],
            "camera_setup": scene["camera_setup"],
            "initial_state": scene["initial_state"],
            "action": scene[action_key],
            "final_state": scene["final_state"],
            "dialogue": [{
                "speaker_id": scene["speaker_id"],
                "text": scene["dialogue"],
                "start_sec": scene["start_sec"],
            }],
        }],
    )


def test_director_brief_and_contrasting_examples_are_in_existing_guidance():
    system = deepseek.SYSTEM_PROMPT.casefold()
    guide, _ = deepseek.load_creative_guides()
    guide = guide.casefold()

    for idea in ("veut obtenir", "ressent ou dissimule", "réaction", "camera_setup", "start_sec"):
        assert idea.casefold() in system
    assert "caméra immobile" in system
    assert "faible" in guide and "mieux" in guide
    assert "ressent ou cache" in guide
    assert "gros plan dramatique automatique" in guide


@pytest.mark.parametrize("scene", SCENES, ids=lambda scene: scene["id"])
def test_same_dialogue_and_start_frame_get_more_playable_direction(scene, tmp_path):
    weak = _project(scene, "weak_action")
    weak_prompt = compile_shot_prompt(weak, weak.shots[0])
    draft = {
        "title": scene["id"],
        "visual_style": "Naturalistic cinematic realism, muted colors and practical light",
        "characters": scene["characters"],
        "shots": [{
            "id": "shot_1",
            "duration_s": scene["duration_s"],
            "camera_setup": scene["camera_setup"],
            "initial_state": scene["initial_state"],
            "action": scene["directed_action"],
            "final_state": scene["final_state"],
            "dialogue": [{
                "speaker_id": scene["speaker_id"],
                "text": scene["dialogue"],
                "start_sec": scene["start_sec"],
            }],
        }],
    }
    requests = []

    def fake_deepseek(request):
        requests.append(request)
        return {"choices": [{"message": {"content": json.dumps(draft, ensure_ascii=False)}}]}

    directed, meta = generate_scenario_once(
        settings=V2Settings(),
        idea=scene["idea"],
        target_duration_s=scene["duration_s"],
        project_id=scene["id"],
        transport_fn=fake_deepseek,
    )
    assert len(requests) == 1
    assert "MISE EN SCÈNE" in requests[0]["messages"][0]["content"]
    assert meta["warnings"] == []

    frame = tmp_path / f"{scene['id']}.png"
    Image.new("RGB", (32, 56), color=(28, 35, 45)).save(frame)
    shot = directed.shots[0]
    shot.image_ref = str(frame)
    shot.image_validated = True
    shot.ltx_prompt = compile_shot_prompt(directed, shot)
    prompt = validate_ltx_prompt(shot.ltx_prompt, shot_id=shot.id)

    # Same story beat, spoken line, duration, start frame and end state; only staging improves.
    assert weak.shots[0].duration_s == shot.duration_s
    assert weak.shots[0].initial_state == shot.initial_state
    assert weak.shots[0].final_state == shot.final_state
    assert weak.shots[0].dialogue == shot.dialogue
    assert scene["dialogue"] in weak_prompt and scene["dialogue"] in prompt["prompt"]
    assert prompt["prompt"].index(f'"{scene["dialogue"]}"') < prompt["prompt"].index("End state:")
    assert scene["weak_action"] in weak_prompt
    for cue in scene["cues"]:
        assert cue.casefold() in prompt["prompt"].casefold()
    assert f"Starting composition: {scene['camera_setup']}" in prompt["prompt"]
    assert "Only the named on-screen speaker lip-syncs" in prompt["prompt"]
    assert prompt["word_count"] <= 150

    # Exercise the exact local payload preparation path; this does not call Modal/GPU.
    payload = LtxModalEngine(V2Settings(data_dir=tmp_path / "data")).prepare_payload(
        shot, aspect_ratio=directed.aspect_ratio,
    )
    assert payload["prompt"] == shot.ltx_prompt
    assert payload["continuity_mode"] == "image"
    assert payload["frames"] <= 361
