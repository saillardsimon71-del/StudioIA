from __future__ import annotations

import base64
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app
from core.config import V2Settings
from core.ltx_prompt import compile_shot_prompt
from core.scenario import Project
from core.storage import StudioRepository
from engines.video import LtxModalEngine, RenderExecutionError
from test_v2_pipeline import _make_real_test_mp4
from test_metaai_image_generation import _MetaAIService
from test_modal_migration import _load_worker


@pytest.fixture
def production(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_V2_COSTS_MD", str(tmp_path / "costs.md"))
    settings = V2Settings(data_dir=tmp_path / "data", ocr_enabled=False, whisper_model_or_path="")
    settings.ensure_dirs()
    project = Project.model_validate_json((ROOT / "tests/fixtures/apres_anniversaire.json").read_text(encoding="utf-8"))
    image = settings.media_dir / "initial.png"
    Image.new("RGB", (64, 112)).save(image)
    project.shots[0].image_ref = str(image)
    project.shots[0].image_validated = True
    clip = _make_real_test_mp4(settings.takes_dir / "fixture.mp4", duration_s=8.041667)
    result = {"video_base64": base64.b64encode(clip.read_bytes()).decode("ascii"),
              "metadata": {"generation_seconds": 1.0}}
    return settings, project, result


def test_two_speakers_compile_without_losing_direction_or_identity(production):
    _, project, _ = production
    prompt = compile_shot_prompt(project, project.shots[0])
    shot = project.shots[0]
    for value in (shot.action, shot.initial_state, shot.final_state, shot.camera_setup):
        assert value in prompt
    for char in project.characters:
        assert char.description in prompt
    assert 'Lucas says in a low controlled French voice: "Tu comptais me le dire quand ?"' in prompt
    assert 'Emma replies in a soft hesitant French voice: "Après ton anniversaire."' in prompt


@pytest.mark.parametrize("cached", ["", "An obsolete prompt without the new dialogue."])
def test_direct_render_compiles_current_story_without_preflight(production, cached):
    settings, project, result = production
    project.shots[0].ltx_prompt = cached
    sent = []
    app = create_app(settings=settings, modal_runner=lambda payload: (sent.append(payload), result)[1])
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={"authorized_by_human": True})
    assert response.status_code == 200, response.text
    expected = compile_shot_prompt(project, project.shots[0])
    assert sent[0]["prompt"] == expected
    assert response.json()["take"]["ltx_prompt"] == expected
    assert response.json()["project"]["shots"][0]["ltx_prompt"] == expected


def test_save_invalidates_changed_shot_and_continuations_but_preserves_files(production):
    settings, project, _ = production
    first = project.shots[0]
    first.rendered_video_ref = str(settings.takes_dir / "fixture.mp4")
    first.human_validated = True
    second = first.model_copy(deep=True, update={"id": "shot_2", "continue_from_previous": True,
                                                "initial_state": first.final_state})
    third = first.model_copy(deep=True, update={"id": "shot_3"})
    project.shots.extend([second, third])
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    edited = project.model_dump(mode="json")
    edited["shots"][0]["dialogue"][0]["text"] = "Pourquoi maintenant ?"
    with TestClient(app) as client:
        response = client.put(f"/api/projects/{project.id}", json=edited)
    assert response.status_code == 200, response.text
    shots = response.json()["shots"]
    assert shots[0]["rendered_video_ref"] is None
    assert shots[1]["rendered_video_ref"] is None
    assert shots[2]["rendered_video_ref"] == first.rendered_video_ref
    assert shots[0]["image_ref"] == first.image_ref
    assert shots[0]["image_validated"] is True
    assert Path(first.rendered_video_ref).is_file()


def test_render_does_not_overwrite_concurrent_project_edits(production):
    settings, project, result = production
    project.shots[0].ltx_prompt = "A legacy prompt."
    app = None

    def runner(payload):
        current = app.state.repo.get_project(project.id)
        current.title = "Titre modifie pendant le rendu"
        app.state.repo.save_project(current)
        return result

    app = create_app(settings=settings, modal_runner=runner)
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={"authorized_by_human": True})
    assert response.status_code == 200, response.text
    assert response.json()["project"]["title"] == "Titre modifie pendant le rendu"


def test_simultaneous_render_requests_never_spawn_two_paid_takes(production):
    settings, project, result = production
    project.shots[0].ltx_prompt = "A legacy prompt."
    entered, finish = Event(), Event()
    calls = []

    def runner(payload):
        calls.append(payload)
        entered.set()
        assert finish.wait(10)
        return result

    app = create_app(settings=settings, modal_runner=runner)
    app.state.repo.save_project(project)
    url = f"/api/projects/{project.id}/shots/shot_1/render"
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        first = pool.submit(client.post, url, json={"authorized_by_human": True})
        assert entered.wait(10)
        try:
            duplicate = client.post(url, json={"authorized_by_human": True})
        finally:
            finish.set()
        assert first.result().status_code == 200
    assert duplicate.status_code == 409
    assert len(calls) == 1


def test_corrupt_worker_result_is_failed_and_never_attached(production):
    settings, project, _ = production
    project.shots[0].ltx_prompt = "A legacy prompt."
    app = create_app(settings=settings, modal_runner=lambda _: {"video_base64": base64.b64encode(b"not a video").decode()})
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={"authorized_by_human": True})
    assert response.status_code == 502
    assert app.state.repo.get_project(project.id).shots[0].rendered_video_ref is None
    assert app.state.repo.list_takes(project.id)[0]["status"] == "failed"


@pytest.mark.parametrize("timeout_first", [True, False])
def test_modal_timeout_resumes_same_call_after_restart_without_spawning(production, monkeypatch, timeout_first):
    settings, project, result = production
    project.shots[0].ltx_prompt = "A reference shot."
    spawned, recovered, waits = [], [], []

    class Call:
        object_id = "fc-audit"

        def get(self, timeout):
            waits.append(timeout)
            if len(waits) == 1 and timeout_first:
                raise TimeoutError("connection timed out")
            return result

    call = Call()
    fn = SimpleNamespace(spawn=lambda payload: (spawned.append(payload), call)[1],
                         hydrate=lambda: None,
                         update_autoscaler=lambda **kwargs: None)
    modal = SimpleNamespace(Function=SimpleNamespace(from_name=lambda *args: fn),
                            FunctionCall=SimpleNamespace(from_id=lambda call_id: (recovered.append(call_id), call)[1]))
    monkeypatch.setattr("engines.video._load_modal", lambda: modal)
    repo = StudioRepository(settings.db_path)
    for index in range(2):
        engine = LtxModalEngine(settings, repo=StudioRepository(settings.db_path))
        engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=index == 0)
        if index == 0 and timeout_first:
            with pytest.raises(RenderExecutionError):
                engine.render(project.shots[0])
        else:
            assert Path(engine.render(project.shots[0])).is_file()
    assert len(spawned) == 1
    assert recovered == (["fc-audit"] if timeout_first else [])
    assert all(timeout >= 5400 for timeout in waits)
    assert len(repo.list_takes(project.id)) == 1
    assert repo.pending_render()
    project.shots[0].rendered_video_ref = repo.list_takes(project.id)[0]["video_path"]
    repo.save_project(project)
    assert repo.pending_render() is None


def test_missing_media_is_reported_without_erasing_saved_references(production):
    settings, project, _ = production
    project.shots[0].image_ref = str(settings.media_dir / "missing.png")
    project.shots[0].rendered_video_ref = str(settings.takes_dir / "missing.mp4")
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/prepare-render?recompile_prompts=false")
    assert response.status_code == 200, response.text
    report = response.json()["shots"][0]
    assert report["video_available"] is False
    assert report["ready_for_gpu"] is False
    assert response.json()["project"]["shots"][0]["image_ref"] == project.shots[0].image_ref


def test_preflight_never_reuses_legacy_prompt_even_with_false_flag(production):
    settings, project, _ = production
    project.shots[0].ltx_prompt = "An obsolete prompt."
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        report = client.post(f"/api/projects/{project.id}/prepare-render?recompile_prompts=false").json()
    assert report["shots"][0]["ready_for_gpu"]
    assert report["shots"][0]["ltx_prompt"] == compile_shot_prompt(project, project.shots[0])


def test_missing_image_can_be_regenerated_without_erasing_other_assets(production, monkeypatch):
    settings, project, _ = production
    project.shots[0].image_ref = str(settings.media_dir / "missing.png")
    project.characters[0].image_ref = str(settings.media_dir / "missing-character.png")
    monkeypatch.setattr("api.app.httpx.Client", _MetaAIService)
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        base = f"/api/projects/{project.id}"
        report = client.post(base + "/prepare-render").json()
        assert report["missing_images"] == ["character:lucas", "shot_1"]
        response = client.post(base + "/shots/shot_1/generate-image")
        assert response.status_code == 200, response.text
        generated = response.json()["shots"][0]["image_ref"]
        assert Path(generated).is_file()
        assert client.post(base + "/shots/shot_1/generate-image").json()["shots"][0]["image_ref"] == generated
        assert len(client.get("/api/assets").json()) == 1


@pytest.mark.parametrize("field,value,keep_image", [("voice_hint", "warm", True), ("description", "Man wearing a blue coat", False)])
def test_reference_edits_invalidate_only_affected_media(production, field, value, keep_image):
    settings, project, _ = production
    project.shots[0].rendered_video_ref = str(settings.takes_dir / "fixture.mp4")
    project.characters[0].image_ref = project.shots[0].image_ref
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    edited = project.model_dump(mode="json")
    edited["characters"][0][field] = value
    with TestClient(app) as client:
        saved = client.put(f"/api/projects/{project.id}", json=edited).json()
    assert saved["shots"][0]["rendered_video_ref"] is None
    assert bool(saved["shots"][0]["image_ref"]) is keep_image
    assert bool(saved["characters"][0]["image_ref"]) is keep_image
    assert Path(project.shots[0].image_ref).is_file()


def test_worker_does_not_negate_director_voice_over():
    worker = _load_worker()
    assert "voiceover" not in worker.LTX_NEGATIVE_PROMPT


def test_full_local_pipeline_from_director_to_export_and_reload(production, monkeypatch):
    settings, project, result = production
    draft = project.model_dump(mode="json", exclude={"id", "idea", "aspect_ratio", "target_duration_s"})
    draft["shots"][0]["image_ref"] = None
    draft["shots"][0]["image_validated"] = False
    calls = []
    worker = _load_worker()

    def director(request):
        calls.append("deepseek")
        assert "MISE EN SCÈNE" in request["messages"][0]["content"]
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    def renderer(payload):
        calls.append("ltx")
        worker.validate_request(payload)
        graph = worker.build_ltx_comfy_graph(payload, output_prefix="audit", uploaded_image="initial.png")
        assert graph["20"]["inputs"]["text"] == payload["prompt"]
        assert "Tu comptais me le dire quand ?" in payload["prompt"]
        assert "Après ton anniversaire." in payload["prompt"]
        assert payload["frames"] == 193 and payload["audio_mode"] == "native"
        return result

    monkeypatch.setattr("api.app.httpx.Client", _MetaAIService)
    app = create_app(settings=settings, llm_transport=director, modal_runner=renderer)
    with TestClient(app) as client:
        created = client.post("/api/projects", json={"idea": project.idea, "target_duration_s": 8}).json()
        base = f"/api/projects/{created['id']}"
        generated = client.post(base + "/generate-scenario", json={})
        assert generated.status_code == 200, generated.text
        for resource in ("characters/lucas", "characters/emma", "decors/cuisine_soir", "shots/shot_1"):
            image = client.post(base + f"/{resource}/generate-image")
            assert image.status_code == 200, image.text
        assert len(client.get("/api/assets").json()) == 4
        assert client.post(base + "/shots/shot_1/validate-image", json={"validated": True}).status_code == 200
        rendered = client.post(base + "/shots/shot_1/render", json={"authorized_by_human": True})
        assert rendered.status_code == 200, rendered.text
        exported = client.post(base + "/export")
        assert exported.status_code == 200, exported.text
        record = exported.json()
        assert record["qc"]["ok"]
        assert (record["width"], record["height"]) == (1080, 1920)
        assert abs(record["duration_s"] - 193 / 24) < 0.1
        assert record["qc"]["speech"]["status"] == "unverified"
    reloaded = create_app(settings=settings).state.repo
    assert reloaded.get_project(created["id"]).shots[0].rendered_video_ref
    assert len(reloaded.list_takes(created["id"])) == 1
    assert len(reloaded.list_exports(created["id"])) == 1
    assert calls == ["deepseek", "ltx"]


def test_health_identifies_running_source_and_detects_changes(production, monkeypatch, tmp_path):
    settings, _, _ = production
    (tmp_path / "run.py").write_text("before", encoding="utf-8")
    monkeypatch.setattr("api.app.STUDIO_V2_ROOT", tmp_path)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        before = client.get("/api/health").json()
        assert before["process_id"] > 0 and before["started_at"]
        assert not before["restart_required"]
        (tmp_path / "run.py").write_text("after", encoding="utf-8")
        after = client.get("/api/health").json()
    assert after["source_hash"] == before["source_hash"]
    assert after["restart_required"]


def test_relative_data_directory_is_independent_of_launch_directory(tmp_path, monkeypatch):
    from core.config import load_settings

    config = tmp_path / "config.json"
    config.write_text('{"data_dir": "data"}', encoding="utf-8")
    monkeypatch.setenv("STUDIO_V2_CONFIG", str(config))
    monkeypatch.delenv("STUDIO_V2_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert load_settings().db_path.resolve() == (ROOT / "data/v2.db").resolve()


def test_stale_page_save_preserves_results_completed_since_page_load(production):
    settings, project, _ = production
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    stale = project.model_dump(mode="json")
    current = project.model_copy(deep=True)
    current.shots[0].rendered_video_ref = str(settings.takes_dir / "fixture.mp4")
    current.shots[0].human_validated = True
    current.characters[0].image_ref = project.shots[0].image_ref
    app.state.repo.save_project(current)
    stale["title"] = "Titre saisi dans l'ancien onglet"
    with TestClient(app) as client:
        response = client.put(f"/api/projects/{project.id}", json=stale)
    assert response.status_code == 200, response.text
    assert response.json()["shots"][0]["rendered_video_ref"] == current.shots[0].rendered_video_ref
    assert response.json()["shots"][0]["human_validated"]
    assert response.json()["characters"][0]["image_ref"] == current.characters[0].image_ref
    assert response.json()["title"] == stale["title"]


def test_repeated_exports_preserve_previous_file(production):
    from core.export import export_project_mp4

    settings, project, _ = production
    project.shots[0].rendered_video_ref = str(settings.takes_dir / "fixture.mp4")
    first, _ = export_project_mp4(project, settings)
    first_bytes = first.read_bytes()
    second, _ = export_project_mp4(project, settings)
    assert first != second
    assert first.read_bytes() == first_bytes


def test_start_refuses_occupied_port_without_loading_a_server(tmp_path):
    import socket
    import subprocess

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.run([sys.executable, str(ROOT / "run.py"), "--port", str(port)],
                                 cwd=tmp_path, capture_output=True, timeout=15)
    assert process.returncode != 0
    assert b"Demarrage refuse" in process.stderr


@pytest.mark.parametrize("content", [None, "", {"unexpected": True}])
def test_invalid_director_content_is_an_explicit_scenario_error(content):
    from llm.deepseek import ScenarioGenerationError, generate_scenario_once

    with pytest.raises(ScenarioGenerationError):
        generate_scenario_once(settings=V2Settings(), idea="Un dialogue", transport_fn=lambda _: {
            "choices": [{"message": {"content": content}}],
        })


@pytest.mark.parametrize("base_url,expected", [("https://api.deepseek.com", "deepseek-flash"),
                                              ("http://local-proxy.test", "deepseek-chat")])
def test_legacy_official_model_is_resolved_without_rewriting_config(tmp_path, monkeypatch, base_url, expected):
    from core.config import load_settings

    config = tmp_path / "config.json"
    original = json.dumps({"deepseek_base_url": base_url, "deepseek_model": "deepseek-chat",
                           "data_dir": str(tmp_path / "data")})
    config.write_text(original, encoding="utf-8")
    monkeypatch.setenv("STUDIO_V2_CONFIG", str(config))
    settings = load_settings()
    assert settings.deepseek_model == expected
    assert config.read_text(encoding="utf-8") == original
