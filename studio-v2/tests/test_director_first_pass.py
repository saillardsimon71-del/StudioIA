import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import V2Settings
from core.ltx_prompt import compile_shot_prompt
from core.scenario import Project
from core.storyboard import build_shot_image_prompt
from llm import deepseek


@pytest.fixture
def draft():
    return {
        "title": "La tasse", "visual_style": "Stylized cinematic 3D",
        "characters": [
            {"id": "benny", "name": "Benny", "description": "Yellow banana with long arms."},
            {"id": "frida", "name": "Frida", "description": "Red strawberry with green leaves."},
        ],
        "decors": [{"id": "counter", "name": "Comptoir", "description": "Marble counter, white cup at left."}],
        "shots": [{
            "id": "shot_1", "duration_s": 2,
            "initial_state": "Benny stands beside the upright cup, hands at his sides.",
            "action": "Benny grips the handle and tips the cup. A ceramic clack rings out.",
            "final_state": "Benny holds the tilted cup by its handle.",
            "camera_setup": "Medium shot, cup left, Benny right", "decor_id": "counter", "dialogue": [],
        }],
    }


def test_new_start_image_is_materialized_from_the_director_state_without_another_call(draft):
    calls = []

    def respond(request):
        calls.append(request)
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    project, meta = deepseek.generate_scenario_once(
        settings=V2Settings(), idea="Un gag sans paroles.", target_duration_s=2, transport_fn=respond,
    )
    shot = project.shots[0]
    assert len(calls) == 1
    assert shot.initial_state in shot.image_prompt
    assert shot.camera_setup in shot.image_prompt
    assert "Yellow banana with long arms" in shot.image_prompt
    assert "Marble counter, white cup at left" in shot.image_prompt
    assert "Red strawberry with green leaves" not in shot.image_prompt
    assert "tips the cup" not in shot.image_prompt
    assert draft["shots"][0]["action"] in shot.ltx_prompt
    assert meta["warnings"] == []


@pytest.mark.parametrize("idea,voice_over", [
    ("Sans dialogue, aucune voix off.", False),
    ("No dialogue and no voice-over.", False),
    ("Une histoire racontée par un narrateur invisible.", True),
])
def test_missing_legacy_narration_mode_uses_actual_delivery_not_negated_brief_words(draft, idea, voice_over):
    if voice_over:
        draft["shots"][0]["dialogue"] = [{
            "speaker_id": "benny", "text": "Tout allait changer.", "delivery": "voice_over", "start_sec": 0,
        }]
    project, _ = deepseek.generate_scenario_once(
        settings=V2Settings(), idea=idea, target_duration_s=2,
        transport_fn=lambda request: {"choices": [{"message": {"content": json.dumps(draft)}}]},
    )
    assert project.narration_mode == ("voice_over" if voice_over else "dialogue")
    assert len(project.shots[0].dialogue) == int(voice_over)


@pytest.mark.parametrize("initial,action,final", [
    ("Pure black, featureless frame.", "Hold on pure black in silence.", "Pure black, featureless frame."),
    ("Blank timer face fills the frame.", "The timer pulses red as two electronic beeps sound.", "The blank screen still fills the frame."),
])
def test_inserts_without_visible_characters_do_not_inherit_the_global_cast(draft, initial, action, final):
    shot = draft["shots"][0]
    shot.update(initial_state=initial, action=action, final_state=final, camera_setup="Static insert")
    project = Project(id="insert", idea="Une histoire avec deux fruits et un insert.", **draft)
    prompt = compile_shot_prompt(project, project.shots[0])
    assert action in prompt and initial in prompt and final in prompt
    assert "Benny" not in prompt and "Frida" not in prompt
    assert "Yellow banana" not in prompt and "Red strawberry" not in prompt
    assert "expressive physical reactions" not in prompt


def test_later_camera_reveal_is_not_used_as_the_starting_image_composition(draft):
    shot = draft["shots"][0]
    shot["action"] = "Benny turns the cup, then the camera moves to an extreme close-up inside the bowl."
    project = Project(id="camera", idea="Une révélation change le cadrage.", **draft)
    prompt = build_shot_image_prompt(project, project.shots[0])
    assert "Medium shot, cup left, Benny right" in prompt
    assert "extreme close-up" not in prompt
    assert "inside the bowl" not in prompt


def test_empty_start_frame_does_not_reintroduce_actors_from_an_earlier_state(draft):
    initial = "Only a blank timer screen fills the frame."
    draft["shots"][0]["final_state"] = initial
    draft["shots"].append({
        "id": "shot_2", "duration_s": 2, "initial_state": initial,
        "action": "The screen glows red.", "final_state": initial,
        "camera_setup": "Extreme close-up on the display", "decor_id": "counter",
    })
    project = Project(id="insert-camera", idea="La caméra quitte les personnages pour le compteur.", **draft)
    prompt = build_shot_image_prompt(project, project.shots[1])
    assert initial in prompt
    assert "Benny" not in prompt and "Yellow banana" not in prompt


@pytest.mark.parametrize("timeout_s,read_timeout_s", [(90, 300), (600, 600)])
def test_official_director_authors_one_start_state_and_no_parallel_image_story(monkeypatch, draft, timeout_s, read_timeout_s):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == read_timeout_s
        assert request.extensions["timeout"]["connect"] == timeout_s
        body = json.loads(request.content)
        schema = body["text"]["format"]["schema"]
        assert schema["properties"]["visual_style"]["maxLength"] == 160
        shot = schema["$defs"]["Shot"]
        assert "image_prompt" not in shot["properties"]
        assert {"initial_state", "final_state", "camera_setup"} <= set(shot["required"])
        assert "narration_mode" in schema["required"]
        return httpx.Response(200, json={
            "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(draft)}]}],
        })

    client_class = httpx.Client
    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kw: client_class(transport=httpx.MockTransport(respond), **kw))
    project, _ = deepseek.generate_scenario_once(
        settings=V2Settings(deepseek_api_key="test", deepseek_timeout_s=timeout_s),
        idea="Un gag sans paroles.", target_duration_s=2,
    )
    assert len(requests) == 1
    assert project.shots[0].initial_state in project.shots[0].image_prompt
