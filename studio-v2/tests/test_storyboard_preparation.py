import json
import io
import sys
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.app import build_shot_image_prompt, create_app
from core.config import V2Settings
from core.ltx_prompt import compile_shot_prompt
from core.scenario import DialogueLine, Project
from llm.deepseek import generate_scenario_once


@pytest.fixture
def horror():
    return Project.model_validate_json((ROOT / "tests/fixtures/trois_coups.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("index,absent", [
    (0, "sits up"), (1, "hand reaching"), (2, "woman in her seventies"),
    (3, "crack spreading"), (4, "revealing old bricks"),
    (5, "ear pressed to the glass"), (7, "cheek and ear pressed"),
    (8, "back against an iron bed"),
])
def test_image_repairs_premature_action_using_initial_state(horror, index, absent):
    shot = horror.shots[index]
    before = horror.model_dump_json()
    prompt = build_shot_image_prompt(horror, shot)
    assert absent not in prompt
    assert shot.initial_state in prompt
    assert "grey t-shirt" in prompt
    assert horror.model_dump_json() == before


def test_realistic_style_applies_to_video_with_and_without_reference(horror):
    shot = horror.shots[0]
    for ref in (None, "frame.png"):
        shot.image_ref = ref
        prompt = compile_shot_prompt(horror, shot)
        assert "realistic" in prompt
        assert "stylized 3D" not in prompt


def test_realistic_project_repairs_contradictory_image_style(horror):
    shot = horror.shots[6]
    shot.image_prompt = shot.image_prompt.replace("dark realistic style", "stylized 3D cinema")
    prompt = build_shot_image_prompt(horror, shot)
    assert "stylized 3D" not in prompt
    assert "realistic" in prompt and shot.initial_state in prompt


def test_explicit_camera_id_cannot_hide_a_wide_to_close_cut(horror):
    from core.storyboard import continuation_issue

    previous, shot = horror.shots[1:3]
    previous.camera_setup = shot.camera_setup = "corridor-fixed"
    previous.image_prompt = "Wide shot of Julien facing the wallpaper."
    shot.image_prompt = "Close-up of Julien's knuckles."
    assert continuation_issue(previous, shot)


def test_voice_over_is_audio_only_and_does_not_add_narrator_to_visual_cast(horror):
    narrator = horror.characters[0].model_copy(update={
        "id": "narrateur", "name": "Narrateur", "description": "Invisible storyteller wearing a red hat",
    })
    horror.characters.append(narrator)
    shot = horror.shots[0]
    shot.dialogue = [DialogueLine(speaker_id="narrateur", text="Chaque nuit, le mur me réveillait.",
                                  start_sec=0.3, delivery="voice_over")]
    prompt = compile_shot_prompt(horror, shot)
    assert 'voice-over' in prompt.lower()
    assert "red hat" not in prompt
    assert 'Julien says' not in prompt
    assert "only the named on-screen speaker lip-syncs" in prompt.casefold()
    assert "react silently" in prompt.casefold()
    assert 'Chaque nuit, le mur me réveillait.' in prompt


def test_generation_repairs_only_prompts_and_unsafe_reference_chains_in_one_call(horror):
    before = horror.model_dump(mode="json")
    before.pop("narration_mode")
    calls = []

    def transport(body):
        calls.append(body)
        return {"choices": [{"message": {"content": json.dumps(before)}}]}

    project, meta = generate_scenario_once(settings=V2Settings(), idea=horror.idea,
                                         target_duration_s=60, transport_fn=transport)
    assert len(calls) == 1
    assert project.narration_mode == "dialogue"
    assert project.shots[0].initial_state in project.shots[0].image_prompt
    assert not project.shots[3].continue_from_previous
    assert meta["warnings"]
    for new, old in zip(project.shots, horror.shots):
        assert (new.action, new.initial_state, new.final_state, new.dialogue) == (
            old.action, old.initial_state, old.final_state, old.dialogue,
        )


def test_clock_rollback_blocks_preparation_without_mutating_saved_project(horror, tmp_path):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    app.state.repo.save_project(horror)
    before = app.state.repo.get_project(horror.id).model_dump_json()
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{horror.id}/prepare-render")
    assert response.status_code == 200, response.text
    report = next(s for s in response.json()["shots"] if s["shot_id"] == "shot_8")
    assert not report["ready_for_gpu"]
    assert "03:17" in report["blocking_reason"] and "03:18" in report["blocking_reason"]
    assert app.state.repo.get_project(horror.id).model_dump_json() == before


def test_continuation_requires_same_camera_state_and_location(horror):
    from core.storyboard import continuation_issue

    previous, shot = horror.shots[1:3]
    previous.camera_setup = shot.camera_setup = "corridor-wide-fixed"
    assert continuation_issue(previous, shot) is None
    shot.camera_setup = "wall-closeup"
    assert continuation_issue(previous, shot)
    shot.camera_setup = previous.camera_setup
    shot.decor_id = "chambre"
    assert continuation_issue(previous, shot)
    shot.decor_id = previous.decor_id
    shot.initial_state = "Julien is already on the floor."
    assert continuation_issue(previous, shot)


def test_dialogue_and_fruit_style_remain_default():
    project = Project(id="fruit", idea="Fruit City", characters=[{
        "id": "fraise", "name": "Fraise", "description": "A small red strawberry in blue pajamas",
    }], shots=[{
        "id": "s1", "duration_s": 5, "initial_state": "Fraise sits on her bed.",
        "action": "Fraise looks at the mirror.", "dialogue": [{"speaker_id": "fraise", "text": "Qui est là ?"}],
    }])
    assert project.narration_mode == "dialogue"
    prompt = compile_shot_prompt(project, project.shots[0])
    assert 'stylized 3D' in prompt and 'Fraise says' in prompt
    assert 'voice-over' not in prompt
    project.visual_style = "hand-drawn watercolor animation"
    assert project.visual_style in compile_shot_prompt(project, project.shots[0])


def test_timed_speech_overflow_is_reported_before_gpu(horror):
    from core.storyboard import storyboard_issues

    shot = horror.shots[0]
    shot.dialogue[0].start_sec = 6
    assert "parole" in " ".join(storyboard_issues(horror)[shot.id])


def test_explicit_ellipse_allows_a_new_night_and_numeric_fiction_is_preserved(horror):
    from core.storyboard import storyboard_issues

    horror.shots[7].time_transition = "ellipse"
    assert not any("03:17" in issue for issue in storyboard_issues(horror).get("shot_8", []))
    horror.shots[0].display_overlay.initial_text = "009"
    horror.shots[0].display_overlay.changes[0].text = "000"
    assert not any("009" in issue for issue in storyboard_issues(horror).get("shot_1", []))


def test_direct_render_cannot_bypass_clock_check_or_call_gpu(horror, tmp_path):
    def forbidden(payload):
        pytest.fail("Le GPU ne doit pas etre appele pour une horloge incoherente")

    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"), modal_runner=forbidden)
    app.state.repo.save_project(horror)
    before = app.state.repo.get_project(horror.id).model_dump_json()
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{horror.id}/shots/shot_8/render", json={
            "authorized_by_human": True, "max_budget_eur": 1,
        })
    assert response.status_code == 422
    assert "03:18" in response.text and "03:17" in response.text
    assert app.state.repo.get_project(horror.id).model_dump_json() == before


@pytest.mark.parametrize("concurrent_edit", [False, True])
def test_meta_receives_repaired_start_and_does_not_attach_to_a_changed_state(horror, tmp_path, monkeypatch, concurrent_edit):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    app.state.repo.save_project(horror)
    png = io.BytesIO()
    Image.new("RGB", (17, 29)).save(png, format="PNG")
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            prompt = body["prompt"]
            assert horror.shots[0].initial_state in prompt
            assert "sits up" not in prompt
            if concurrent_edit:
                current = app.state.repo.get_project(horror.id)
                current.shots[0].initial_state = "Julien stands at the bedroom door."
                app.state.repo.save_project(current)
            return httpx.Response(200, json={
                "success": True,
                "prompt": prompt,
                "request_id": body["request_id"],
                "conversation_id": f"thread-{body['request_id']}",
                "image_urls": ["https://scontent.xx.fbcdn.net/frame.png"],
            })
        return httpx.Response(200, content=png.getvalue())

    client_class = httpx.Client
    monkeypatch.setattr("api.app.httpx.Client", lambda **kw: client_class(transport=httpx.MockTransport(respond), **kw))
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{horror.id}/shots/shot_1/generate-image")
    assert response.status_code == 200, response.text
    saved = response.json()
    assert len(requests) == 2
    assert bool(saved["shots"][0]["image_ref"]) is (not concurrent_edit)
    assert saved["shots"][1:] == horror.model_dump(mode="json")["shots"][1:]
    assert saved["shots"][0]["image_validated"] is False


def test_repaired_starting_images_are_idempotent(horror):
    from core.storyboard import prepare_storyboard

    horror.decors[0].description = "Opposite the bed is the wall, above bare floorboards."
    prepare_storyboard(horror)
    before = [s.image_prompt for s in horror.shots]
    warnings = prepare_storyboard(horror)
    assert [s.image_prompt for s in horror.shots] == before
    assert not any("image initiale reconstruite" in w for w in warnings)


def test_image_repair_over_capacity_is_an_explicit_generation_error(horror):
    from llm.deepseek import ScenarioGenerationError

    horror.characters[0].description = "Julien " + "tired " * 280
    horror.decors[0].description = "Old bedroom " + "wallpaper " * 190
    # Each field fits its own schema, but their combined image exceeds 4000 characters.
    draft = horror.model_dump(mode="json")
    with pytest.raises(ScenarioGenerationError, match="4000"):
        generate_scenario_once(settings=V2Settings(), idea=horror.idea,
            transport_fn=lambda body: {"choices": [{"message": {"content": json.dumps(draft)}}]})


def test_legacy_fruit_continuation_without_camera_metadata_is_not_blocked(tmp_path):
    from core.storyboard import storyboard_issues

    project = Project(id="legacy-fruit", idea="Fruit City dialogue", shots=[
        {"id": "a", "duration_s": 5, "action": "Fraise leans back on the bed.",
         "final_state": "Fraise leans back on the bed."},
        {"id": "b", "duration_s": 5, "action": "Fraise looks up at the mirror.",
         "initial_state": "Fraise is seated, leaning back.", "continue_from_previous": True},
    ])
    before = project.model_dump_json()
    assert storyboard_issues(project) == {}
    assert project.model_dump_json() == before
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        report = client.post(f"/api/projects/{project.id}/prepare-render").json()["shots"][1]
    assert report["warnings"] == ["Raccord ancien sans composition declaree : a confirmer visuellement."]
    assert "composition" not in report["blocking_reason"]
