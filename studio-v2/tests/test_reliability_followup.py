from pathlib import Path
from types import SimpleNamespace
import base64
import subprocess

import pytest
from fastapi.testclient import TestClient

from test_production_reliability import production
from api.app import create_app
from core.ltx_prompt import compile_shot_prompt
from core.storage import StudioRepository
from engines.video import LtxModalEngine, RenderExecutionError
from qc.media import ffmpeg_exe


@pytest.fixture
def remote_call(production, monkeypatch):
    settings, project, result = production
    calls = {"spawn": 0, "get": 0}

    class Call:
        object_id = "fc-second-pass"

        def get(self, timeout):
            calls["get"] += 1
            return result

    def spawn(payload):
        calls["spawn"] += 1
        return Call()

    function = SimpleNamespace(spawn=spawn, hydrate=lambda: None, update_autoscaler=lambda **kwargs: None)
    modal = SimpleNamespace(Function=SimpleNamespace(from_name=lambda *args: function),
                            FunctionCall=SimpleNamespace(from_id=lambda call_id: Call()))
    monkeypatch.setattr("engines.video._load_modal", lambda: modal)
    project.shots[0].ltx_prompt = compile_shot_prompt(project, project.shots[0])
    repo = StudioRepository(settings.db_path)
    repo.save_project(project)
    return settings, project, repo, calls


def test_preflight_does_not_overwrite_results_saved_during_payload_check(production, monkeypatch):
    settings, project, _ = production
    app = create_app(settings=settings)
    app.state.repo.save_project(project)
    original = LtxModalEngine.prepare_payload

    def concurrent_update(engine, shot, **kwargs):
        current = app.state.repo.get_project(project.id)
        current.title = "Titre enregistre pendant le preflight"
        current.shots[0].rendered_video_ref = str(settings.takes_dir / "fixture.mp4")
        app.state.repo.save_project(current)
        return original(engine, shot, **kwargs)

    monkeypatch.setattr(LtxModalEngine, "prepare_payload", concurrent_update)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/prepare-render")
    assert response.status_code == 200, response.text
    saved = app.state.repo.get_project(project.id)
    assert saved.title == "Titre enregistre pendant le preflight"
    assert saved.shots[0].rendered_video_ref


@pytest.mark.parametrize("field,value", [("seed", 71), ("duration_s", 9)])
def test_recovery_never_attaches_a_take_to_modified_render_inputs(remote_call, field, value):
    settings, project, repo, calls = remote_call
    engine = LtxModalEngine(settings, repo=repo)
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    engine.render(project.shots[0])
    changed = repo.get_project(project.id)
    setattr(changed.shots[0], field, value)
    repo.save_project(changed)
    with TestClient(create_app(settings=settings)) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={})
    assert response.status_code == 409, response.text
    assert repo.get_project(project.id).shots[0].rendered_video_ref is None
    assert repo.list_takes(project.id)[0]["status"] == "completed"
    assert repo.pending_render() is None
    assert calls["spawn"] == 1


def test_local_write_failure_recovers_the_existing_modal_result(remote_call, monkeypatch):
    settings, project, repo, calls = remote_call
    original_write = Path.write_bytes
    fail_write = True

    def write(path, data):
        if fail_write and path.parent == settings.takes_dir and path.name.startswith("take-"):
            raise OSError("Simulated local disk failure")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    engine = LtxModalEngine(settings, repo=repo)
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    with pytest.raises(RenderExecutionError):
        engine.render(project.shots[0])
    assert repo.pending_render(), "Local storage failure must not release the paid job"
    assert repo.list_takes(project.id) == []
    fail_write = False
    resumed = LtxModalEngine(settings, repo=repo)
    resumed.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=False)
    assert Path(resumed.render(project.shots[0])).is_file()
    assert calls == {"spawn": 1, "get": 2}
    assert len(repo.list_takes(project.id)) == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_cached_take_is_downloaded_from_same_call(remote_call, damage):
    settings, project, repo, calls = remote_call
    engine = LtxModalEngine(settings, repo=repo)
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    output = Path(engine.render(project.shots[0]))
    if damage == "missing":
        output.unlink()
    else:
        output.write_bytes(b"corrupt cached MP4")
    resumed = LtxModalEngine(settings, repo=repo)
    resumed.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=False)
    recovered = Path(resumed.render(project.shots[0]))
    assert recovered.is_file()
    assert recovered.read_bytes() != b"corrupt cached MP4"
    assert calls == {"spawn": 1, "get": 2}
    assert len(repo.list_takes(project.id)) == 1


@pytest.mark.parametrize("remove", ["shot", "project"])
def test_removed_target_does_not_leave_all_gpu_work_blocked(production, remove):
    settings, project, result = production
    app = None

    def renderer(payload):
        if remove == "project":
            app.state.repo.delete_project(project.id)
        else:
            changed = app.state.repo.get_project(project.id)
            changed.shots = []
            app.state.repo.save_project(changed)
        return result

    app = create_app(settings=settings, modal_runner=renderer)
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={"authorized_by_human": True})
    assert response.status_code == 409, response.text
    assert app.state.repo.pending_render() is None
    take = app.state.repo.list_takes(project.id)[0]
    assert take["status"] == "completed" and Path(take["video_path"]).is_file()


def test_camera_word_does_not_add_an_absent_character(production):
    _, project, _ = production
    absent = project.characters[0].model_copy(update={"id": "cam", "name": "Camille",
                                                    "description": "Woman wearing a purple jacket"})
    project.characters.append(absent)
    project.shots[0].action += " The camera slowly moves closer."
    prompt = compile_shot_prompt(project, project.shots[0])
    assert absent.description not in prompt


@pytest.mark.parametrize("remove", ["shot", "project"])
def test_recovery_after_restart_can_finish_an_orphaned_job(remote_call, remove):
    settings, project, repo, calls = remote_call
    engine = LtxModalEngine(settings, repo=repo)
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    engine.render(project.shots[0])
    if remove == "project":
        repo.delete_project(project.id)
    else:
        changed = repo.get_project(project.id)
        changed.shots = []
        repo.save_project(changed)
    with TestClient(create_app(settings=settings)) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={})
    assert response.status_code == 409, response.text[:300]
    assert repo.pending_render() is None
    assert calls["spawn"] == 1


def test_expired_remote_output_does_not_duplicate_an_existing_take(remote_call, monkeypatch):
    from modal.exception import OutputExpiredError

    settings, project, repo, calls = remote_call
    engine = LtxModalEngine(settings, repo=repo)
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    output = Path(engine.render(project.shots[0]))
    output.unlink()

    def expired(*args, **kwargs):
        raise OutputExpiredError("Stored output expired")

    monkeypatch.setattr(LtxModalEngine, "_call_modal_once", expired)
    resumed = LtxModalEngine(settings, repo=repo)
    resumed.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=False)
    with pytest.raises(RenderExecutionError):
        resumed.render(project.shots[0])
    assert repo.pending_render() is None
    assert len(repo.list_takes(project.id)) == 1
    assert calls["spawn"] == 1


def test_failure_to_persist_call_id_never_releases_a_submitted_job(remote_call, monkeypatch):
    import sqlite3

    settings, project, repo, calls = remote_call
    update = repo.update_render

    def fail_id_write(take_id, **kwargs):
        if kwargs.get("call_id"):
            raise sqlite3.OperationalError("Simulated SQLite write failure")
        return update(take_id, **kwargs)

    monkeypatch.setattr(repo, "update_render", fail_id_write)
    engine = LtxModalEngine(settings, repo=repo)
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    with pytest.raises(RenderExecutionError) as failure:
        engine.render(project.shots[0])
    assert "fc-second-pass" in str(failure.value)
    assert repo.pending_render()
    assert repo.list_takes(project.id) == []
    assert calls == {"spawn": 1, "get": 0}
    engine.arm_single_take(project_id=project.id, shot_id="shot_1", authorized_by_human=True)
    with pytest.raises(RenderExecutionError):
        engine.render(project.shots[0])
    assert calls["spawn"] == 1


def test_wrong_frame_rate_is_rejected_even_with_expected_frame_count(production):
    settings, project, _ = production
    wrong_rate = settings.takes_dir / "wrong-rate.mp4"
    duration = 193 / 12
    subprocess.run([
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=navy:s=768x1344:r=12:d={duration}", "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(wrong_rate),
    ], check=True, capture_output=True, timeout=30)
    app = create_app(settings=settings, modal_runner=lambda _: {
        "video_base64": base64.b64encode(wrong_rate.read_bytes()).decode(),
    })
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/shots/shot_1/render", json={"authorized_by_human": True})
    assert response.status_code == 502, response.text[:300]
    assert app.state.repo.get_project(project.id).shots[0].rendered_video_ref is None
