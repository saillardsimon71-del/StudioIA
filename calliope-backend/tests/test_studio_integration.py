import asyncio
import json
import threading
from contextlib import closing

import pytest

from calliope.config import settings
from calliope.db import get_db
from calliope.queue.manager import queue_manager
from calliope.studio import service
from tests.test_studio import mock_studio_telemetry as mock_studio_telemetry
from tests.test_studio import new_board
from tests.test_studio import pause_studio_queue as pause_studio_queue


def queued(client):
    _, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    request = {"kind": "video", "request_id": "integration", "n_takes": 1}
    result = service.enqueue_takes(shot["id"], request)
    return shot, request, result["takes"][0], queue_manager.get_job(result["jobs"][0]["id"])


def test_trace_is_unique_and_request_stays_idempotent(client):
    shot, request, take, _ = queued(client)
    again = service.enqueue_takes(shot["id"], request)["takes"][0]
    assert take["render_state"]["trace_id"] == again["render_state"]["trace_id"]
    assert len(take["render_state"]["trace_id"]) == 32
    assert take["render_state"]["phase"] == "QUEUED"


def test_cancel_is_terminal_and_generic_retry_rejects_active(client):
    _, _, _, job = queued(client)
    assert queue_manager.retry(job["id"]) is None
    queue_manager.cancel(job["id"])
    queue_manager.mark_done(job["id"], ["late.mp4"])
    queue_manager.mark_failed(job["id"], "late failure")
    assert queue_manager.get_job(job["id"])["error"] == "cancelled"
    assert queue_manager.retry(job["id"]) is None


@pytest.mark.asyncio
async def test_resume_observes_remote_events_without_rebuilding(client, monkeypatch):
    from calliope.engines import modal_h3

    _, _, take, job = queued(client)
    with closing(get_db(settings.db_path)) as conn:
        conn.execute("UPDATE studio_takes SET modal_call_id='fc-old' WHERE id=?", (take["id"],))
        conn.commit()

    def forbidden(**kwargs):
        pytest.fail("resume must not rebuild references")

    monkeypatch.setattr(modal_h3, "build_request", forbidden)
    monkeypatch.setattr(
        modal_h3.ModalH3Engine,
        "progress",
        lambda self, trace: {
            "phase": "GENERATING",
            "container_id": "ta-worker",
            "events": [
                {"phase": "GENERATING", "at": "2026-10-08T10:00:00+00:00", "detail": "sampler"}
            ],
        },
    )
    stop = {"value": False}

    def poll(self, call):
        assert call == "fc-old"
        stop["value"] = True
        return None

    monkeypatch.setattr(modal_h3.ModalH3Engine, "poll", poll)
    with pytest.raises(asyncio.CancelledError):
        await service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: stop["value"])
    current = service.board(job["project_id"])["scenes"][0]["shots"][0]["takes"][0]
    assert current["render_state"]["phase"] == "GENERATING"
    assert current["render_state"]["container_id"] == "ta-worker"
    assert current["render_state"]["events"][-1]["at"] == "2026-10-08T10:00:00+00:00"


def test_ambiguous_submission_cannot_resume(client):
    _, _, take, job = queued(client)
    with closing(get_db(settings.db_path)) as conn:
        conn.execute(
            "UPDATE studio_takes SET submission_state='submitting' WHERE id=?", (take["id"],)
        )
        conn.execute("UPDATE jobs SET status='failed',error='interrupted' WHERE id=?", (job["id"],))
        conn.commit()
    assert client.post(f"/api/studio/jobs/{job['id']}/resume").status_code == 409


@pytest.mark.asyncio
async def test_retained_take_keeps_qc_without_rerender_or_blocking_approval(monkeypatch):
    from calliope.studio import production

    shot = {
        "id": 9,
        "selected_video_id": 2,
        "approved": True,
        "takes": [{"id": 2, "engine": "import"}],
        "continue_from_previous": False,
    }
    checked, attempts, approved = [], [], []
    monkeypatch.setattr(queue_manager, "is_cancelled", lambda identifier: False)
    monkeypatch.setattr(production, "load_shot", lambda identifier: shot)

    async def qc(value):
        checked.append(value["id"])
        return {"status": "missing", "reason": "silence"}

    async def generate(job, value, kind, stop, attempt=0):
        attempts.append(attempt)
        return shot

    monkeypatch.setattr(production, "assess_take", qc)
    monkeypatch.setattr(production, "generate_take", generate)
    monkeypatch.setattr(service, "approve_shot", lambda *args: approved.append(args))
    await production.render_shot({"id": 1}, shot, lambda: False)
    assert checked == [9] and attempts == [] and approved == [(9, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_requested", [False, True])
async def test_shutdown_during_spawn_waits_for_confirmation(client, monkeypatch, cancel_requested):
    from calliope.engines import modal_h3

    _, _, take, job = queued(client)
    entered, release = threading.Event(), threading.Event()

    def spawn(self, request):
        entered.set()
        assert release.wait(3)
        return "fc-confirmed"

    monkeypatch.setattr(modal_h3.ModalH3Engine, "spawn", spawn)
    cancellations = []
    monkeypatch.setattr(
        modal_h3.ModalH3Engine, "cancel", lambda self, call: cancellations.append(call)
    )
    task = asyncio.create_task(
        service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: False)
    )
    await asyncio.to_thread(entered.wait, 3)
    assert entered.is_set()
    task.cancel()
    if cancel_requested:
        queue_manager.cancel(job["id"])
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(
        service.StudioCancelledError if cancel_requested else asyncio.CancelledError
    ):
        await task
    with service.database() as conn:
        current = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take["id"],)).fetchone()
    assert current["modal_call_id"] == "fc-confirmed"
    assert current["submission_state"] == "submitted"
    assert cancellations == (["fc-confirmed"] if cancel_requested else [])


@pytest.mark.asyncio
async def test_deadline_survives_restart_until_explicit_resume(client, monkeypatch):
    from calliope.engines import modal_h3

    _, _, take, job = queued(client)
    old = "2000-01-01T00:00:00+00:00"
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_takes SET modal_call_id='fc-old',render_state_json=? WHERE id=?",
            (json.dumps({"started_at": old}), take["id"]),
        )
        conn.commit()
    polls = []
    monkeypatch.setattr(modal_h3.ModalH3Engine, "poll", lambda *args: polls.append(args))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="Délai global"):
            await service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: False)
    assert not polls
    queue_manager.mark_failed(job["id"], "timeout")
    client.post(f"/api/studio/jobs/{job['id']}/resume").raise_for_status()
    stop = {"value": False}

    def poll(self, call):
        polls.append(call)
        stop["value"] = True
        return None

    monkeypatch.setattr(modal_h3.ModalH3Engine, "poll", poll)
    with pytest.raises(asyncio.CancelledError):
        await service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: stop["value"])
    with service.database() as conn:
        row = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take["id"],)).fetchone()
    assert polls == ["fc-old"] and row["modal_call_id"] == "fc-old"
    state = json.loads(row["render_state_json"])
    assert state["started_at"] == old and state["resume_started_at"] != old


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_video_pool_bounds_independents_and_waits_for_parent(monkeypatch, concurrency):
    from calliope.studio import production

    monkeypatch.setattr(settings, "studio_video_concurrency", concurrency)
    active, maximum, completed, starts = 0, 0, [], []

    async def checkpoint(*args):
        pass

    async def render(job, shot, stop):
        nonlocal active, maximum
        if shot["id"] == 2:
            assert 1 in completed
        starts.append(shot["id"])
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        completed.append(shot["id"])
        active -= 1

    monkeypatch.setattr(production, "checkpoint", checkpoint)
    monkeypatch.setattr(production, "render_shot", render)
    shots = [
        {"id": 1, "continue_from_previous": False},
        {"id": 2, "continue_from_previous": True},
        {"id": 3, "continue_from_previous": False},
    ]
    await production.render_videos({"id": 1}, shots, lambda: False)
    assert maximum == concurrency and set(starts) == {1, 2, 3}


@pytest.mark.asyncio
async def test_failed_parent_does_not_launch_dependent_and_drains_running_tasks(monkeypatch):
    from calliope.studio import production

    monkeypatch.setattr(settings, "studio_video_concurrency", 2)
    started, completed = [], []

    async def checkpoint(*args):
        pass

    async def render(job, shot, stop):
        started.append(shot["id"])
        if shot["id"] == 1:
            await asyncio.sleep(0.01)
            raise ValueError("QC failure")
        await asyncio.sleep(0.02)
        completed.append(shot["id"])

    monkeypatch.setattr(production, "checkpoint", checkpoint)
    monkeypatch.setattr(production, "render_shot", render)
    with pytest.raises(ValueError, match="QC failure"):
        await production.render_videos(
            {"id": 1},
            [
                {"id": 1, "continue_from_previous": False},
                {"id": 2, "continue_from_previous": True},
                {"id": 3, "continue_from_previous": False},
            ],
            lambda: False,
        )
    assert started == [1, 3] and completed == [3]


def test_parent_owned_children_are_not_claimed_and_cancel_propagates(client, monkeypatch):
    _, _, _, child = queued(client)
    parent = queue_manager.enqueue(
        project_id=child["project_id"],
        kind="production",
        payload={"studio_production_id": child["project_id"]},
    )
    with service.database() as conn:
        conn.execute(
            "UPDATE jobs SET payload_json=? WHERE id=?",
            (json.dumps({"parent_job_id": parent["id"]}), child["id"]),
        )
        conn.commit()
    monkeypatch.setattr(queue_manager, "paused", False)
    claimed = queue_manager.claim_next()
    assert claimed["id"] == parent["id"] and queue_manager.claim_next() is None
    queue_manager.cancel(parent["id"])
    assert queue_manager.get_job(child["id"])["error"] == "cancelled"


def test_studio_settings_api_bounds_and_persistence(client, monkeypatch):
    monkeypatch.setattr(settings, "studio_video_concurrency", 1)
    monkeypatch.setattr(settings, "studio_render_timeout_sec", 5400)
    monkeypatch.setattr(settings, "studio_asr_checkpoint", "")
    assert client.get("/api/settings").json()["studio_video_concurrency"] == 1
    assert client.post("/api/settings", json={"studio_video_concurrency": 3}).status_code == 422
    response = client.post(
        "/api/settings",
        json={
            "studio_video_concurrency": 2,
            "studio_render_timeout_sec": 6000,
            "studio_asr_checkpoint": "local.pt",
        },
    )
    assert response.json()["studio_video_concurrency"] == 2
    assert response.json()["studio_render_timeout_sec"] == 6000
    assert response.json()["studio_asr_checkpoint"] == "local.pt"


def test_studio_settings_survive_file_reload(tmp_path, monkeypatch):
    import calliope.config as config

    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    value = config.Settings(
        studio_video_concurrency=2, studio_render_timeout_sec=6000, studio_asr_checkpoint="local.pt"
    )
    value.save_config_file()
    fresh = config.Settings()
    fresh.load_config_file()
    assert fresh.studio_video_concurrency == 2
    assert fresh.studio_render_timeout_sec == 6000
    assert fresh.studio_asr_checkpoint == "local.pt"


def test_paused_done_parent_resume_is_specific(client, monkeypatch):
    from calliope.studio import series
    from tests.test_studio_series import episode
    from tests.test_studio_series import series as create_series

    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    monkeypatch.setattr(settings, "studio_image_base_url", "http://localhost:9002/v1")
    created = episode(client, create_series(client)["id"]).json()
    job_id = created["job_id"]
    queue_manager.mark_done(job_id, [])
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_productions SET status='paused' WHERE project_id=?",
            (created["project_id"],),
        )
        conn.commit()
    assert queue_manager.retry(job_id) is None
    series.resume(created["project_id"])
    assert queue_manager.get_job(job_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_imported_take_qc_is_cached_and_unknown_is_visible(client, tmp_path, monkeypatch):
    from calliope.studio import pacing, production, transcription
    from tests.test_studio import video_bytes

    _, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    service.update_shot(shot["project_id"], shot["id"], {"dialogue": "Bonjour"})
    imported = service.store_upload(shot["id"], video_bytes(tmp_path, audio=True), "video")
    shot = service.select_take(imported["id"])
    calls = []
    monkeypatch.setattr(
        pacing, "detect_voice", lambda *args: {"intervals": [[0, 0.6]], "max_db": -10}
    )
    monkeypatch.setattr(
        transcription,
        "transcribe_dialogue",
        lambda *args: (
            calls.append(args)
            or {"status": "unknown", "reason": "checkpoint non configuré", "speech_intervals": []}
        ),
    )
    for _ in range(2):
        shot = production.load_shot(shot["id"])
        verdict = await production.assess_take(shot)
        assert verdict["status"] == "unknown" and verdict["voice"] is None
    assert len(calls) == 1
    production.trim_take(shot, verdict["voice"])
    shot = production.load_shot(shot["id"])
    assert shot["trim_end_sec"] is None
    assert production.selected_video(shot)["metadata"]["dialogue_qc"]["status"] == "unknown"


def test_continuation_uses_frame_immediately_before_useful_end(tmp_path):
    import base64
    import io
    import subprocess

    from PIL import Image

    from calliope.engines.modal_h3 import build_request

    path = tmp_path / "two-colors.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=128x72:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=128x72:r=24:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    request = build_request(
        prompt="A room.",
        duration_sec=2,
        aspect_ratio="16:9",
        seed=1,
        previous_video_path=path,
        previous_video_time=1.0,
    )
    with Image.open(io.BytesIO(base64.b64decode(request["image_base64"]))) as frame:
        red, green, blue = frame.getpixel((64, 36))
    assert blue > 200 and red < 30 and green < 30


def test_poll_classification_preserves_terminal_function_errors():
    from calliope.engines.modal_h3 import _load_modal, is_poll_network_error

    _load_modal()
    from modal.exception import FunctionTimeoutError, NotFoundError, RemoteError, ServiceError

    assert is_poll_network_error(ServiceError("connection unavailable"))
    assert not is_poll_network_error(FunctionTimeoutError("render deadline"))
    assert not is_poll_network_error(RemoteError("render failed"))
    assert not is_poll_network_error(NotFoundError("expired result"))


@pytest.fixture(autouse=True)
def forbid_comfy_cancel(monkeypatch):
    from calliope.comfyui.client import ComfyUIClient

    async def forbidden(*args):
        pytest.fail('Studio cancellation must not contact Comfy')

    monkeypatch.setattr(ComfyUIClient, 'interrupt', forbidden)


def failed_remote_take(client):
    shot, _, take, job = queued(client)
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_takes SET modal_call_id='fc-live',"
            "submission_state='submitted' WHERE id=?",
            (take["id"],),
        )
        conn.commit()
    queue_manager.mark_failed(job["id"], "Délai global dépassé")
    return shot, take, job


def test_owned_failed_take_requires_production_resume(client, monkeypatch):
    from calliope.studio import production, series
    from tests.test_studio_series import episode, planned_episode
    from tests.test_studio_series import series as create_series

    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    monkeypatch.setattr(settings, "studio_image_base_url", "http://localhost:9002/v1")
    created = episode(client, create_series(client)["id"]).json()
    project_id, parent_id = created["project_id"], created["job_id"]
    production.save_episode_plan(project_id, planned_episode({}))
    shot = service.board(project_id)["scenes"][0]["shots"][0]
    result = service.enqueue_takes(
        shot["id"],
        {
            "kind": "video",
            "request_id": "owned-resume",
            "n_takes": 1,
            "parent_job_id": parent_id,
        },
    )
    child_id, take_id = result["jobs"][0]["id"], result["takes"][0]["id"]
    with service.database() as conn:
        conn.execute("UPDATE studio_takes SET modal_call_id='fc-owned' WHERE id=?", (take_id,))
        conn.execute(
            "UPDATE studio_productions SET status='failed' WHERE project_id=?", (project_id,)
        )
        conn.commit()
    queue_manager.mark_failed(child_id, "deadline")
    queue_manager.mark_failed(parent_id, "deadline")
    assert not service.public_job(queue_manager.get_job(child_id))["can_resume"]
    response = client.post(f"/api/studio/jobs/{child_id}/resume")
    assert response.status_code == 409 and "production" in response.json()["detail"].lower()
    assert queue_manager.get_job(child_id)["status"] == "failed"
    series.resume(project_id)
    monkeypatch.setattr(queue_manager, "paused", False)
    assert queue_manager.claim_next()["id"] == parent_id
    assert queue_manager.claim_next() is None
    with service.database() as conn:
        take = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone()
    assert take["modal_call_id"] == "fc-owned"
    assert json.loads(take["render_state_json"])["resume_started_at"]


@pytest.mark.parametrize("cancel_parent", [False, True])
def test_failed_remote_calls_are_cancelled_without_local_task(client, monkeypatch, cancel_parent):
    from calliope.engines.modal_h3 import ModalH3Engine

    _, take, job = failed_remote_take(client)
    parent = queue_manager.enqueue(
        project_id=job["project_id"],
        kind="production",
        payload={"studio_production_id": job["project_id"]},
    )
    with service.database() as conn:
        conn.execute(
            "UPDATE jobs SET payload_json=? WHERE id=?",
            (json.dumps({"studio_take_id": take["id"], "parent_job_id": parent["id"]}), job["id"]),
        )
        conn.commit()
    queue_manager.mark_failed(parent["id"], "deadline")
    calls = []
    monkeypatch.setattr(ModalH3Engine, "cancel", lambda self, call: calls.append(call))
    assert service.public_job(queue_manager.get_job(job["id"]))["can_cancel"]
    target = parent["id"] if cancel_parent else job["id"]
    route = "/api/jobs" if cancel_parent else "/api/studio/jobs"
    response = client.post(f"{route}/{target}/cancel")
    assert response.status_code == 200, response.text
    assert calls == ["fc-live"]
    assert queue_manager.get_job(job["id"])["error"] == "cancelled"
    assert not service.public_job(queue_manager.get_job(job["id"]))["can_cancel"]
    with service.database() as conn:
        state = conn.execute(
            "SELECT render_state_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()
    assert json.loads(state[0])["remote_terminal"] is True


@pytest.mark.parametrize("cancel_parent", [False, True])
def test_cancel_rpc_failure_is_reported_and_can_be_retried(client, monkeypatch, cancel_parent):
    from calliope.engines.modal_h3 import ModalH3Engine

    _, take, job = failed_remote_take(client)
    target = job["id"]
    route = "/api/studio/jobs"
    if cancel_parent:
        parent = queue_manager.enqueue(
            project_id=job["project_id"],
            kind="production",
            payload={"studio_production_id": job["project_id"]},
        )
        with service.database() as conn:
            conn.execute(
                "UPDATE jobs SET payload_json=? WHERE id=?",
                (
                    json.dumps({"studio_take_id": take["id"], "parent_job_id": parent["id"]}),
                    job["id"],
                ),
            )
            conn.commit()
        queue_manager.mark_failed(parent["id"], "deadline")
        target, route = parent["id"], "/api/jobs"

    def fail(self, call):
        raise ConnectionError("RPC unavailable")

    monkeypatch.setattr(ModalH3Engine, "cancel", fail)
    response = client.post(f"{route}/{target}/cancel")
    assert response.status_code == 503 and "non confirmé" in response.json()["detail"]
    current = service.public_job(queue_manager.get_job(job["id"]))
    assert current["can_cancel"] and not current["render_state"].get("remote_terminal")
    monkeypatch.setattr(ModalH3Engine, "cancel", lambda *args: None)
    assert client.post(f"{route}/{target}/cancel").status_code == 200
    assert not service.public_job(queue_manager.get_job(job["id"]))["can_cancel"]


@pytest.mark.parametrize("terminal", ["result_received", "legacy_done"])
def test_cancel_parent_preserves_finished_calls(client, monkeypatch, terminal):
    from calliope.engines.modal_h3 import ModalH3Engine

    _, take, job = failed_remote_take(client)
    parent = queue_manager.enqueue(
        project_id=job["project_id"],
        kind="production",
        payload={"studio_production_id": job["project_id"]},
    )
    with service.database() as conn:
        conn.execute(
            "UPDATE jobs SET payload_json=? WHERE id=?",
            (json.dumps({"studio_take_id": take["id"], "parent_job_id": parent["id"]}), job["id"]),
        )
        conn.commit()
    if terminal == "result_received":
        service.render_event(take["id"], remote_terminal=True)
    else:
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_takes SET status='done',output_path='old.mp4' WHERE id=?",
                (take["id"],),
            )
            conn.commit()
    monkeypatch.setattr(
        ModalH3Engine, "cancel", lambda *args: pytest.fail("finished call cancelled")
    )
    assert client.post(f"/api/jobs/{parent['id']}/cancel").status_code == 200
    assert not service.public_job(queue_manager.get_job(job["id"]))["can_cancel"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_name,terminal",
    [
        ("NotFoundError", False),
        ("AuthError", False),
        ("RemoteError", True),
        ("FunctionTimeoutError", True),
        ("OutputExpiredError", True),
        ("RemoteUserError", True),
        ("LocalValueError", False),
    ],
)
async def test_sdk_terminal_evidence_excludes_lookup_and_auth_errors(
    client, monkeypatch, error_name, terminal
):
    from types import SimpleNamespace

    from calliope.engines import modal_h3

    modal_h3._load_modal()
    from modal import exception

    _, take, job = failed_remote_take(client)
    queue_manager.retry(job["id"])
    if error_name in ('RemoteUserError', 'LocalValueError'):
        error = ValueError('SDK failure')
        if error_name == 'RemoteUserError':
            error.__line_cache__ = {}
    else:
        error = getattr(exception, error_name)("SDK failure")
    stop = {'value': False}

    def get(**kwargs):
        stop['value'] = True
        raise error

    monkeypatch.setattr(
        modal_h3,
        "_load_modal",
        lambda: SimpleNamespace(
            FunctionCall=SimpleNamespace(from_id=lambda call: SimpleNamespace(get=get))
        ),
    )
    with pytest.raises(RuntimeError, match="SDK failure"):
        await service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: stop['value'])
    current = service.public_job(queue_manager.get_job(job["id"]))
    assert bool(current["render_state"].get("remote_terminal")) is terminal


@pytest.mark.asyncio
async def test_artifact_ready_deadline_does_not_confirm_remote_terminal(client, monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    _, take, job = failed_remote_take(client)
    queue_manager.retry(job["id"])
    service.render_event(take["id"], started_at="2000-01-01T00:00:00+00:00")
    monkeypatch.setattr(ModalH3Engine, "progress", lambda *args: {"phase": "ARTIFACT_READY"})
    with pytest.raises(RuntimeError, match="Délai global"):
        await service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: False)
    state = service.public_job(queue_manager.get_job(job["id"]))["render_state"]
    assert not state.get("remote_terminal")


@pytest.mark.asyncio
async def test_retrieved_invalid_result_is_terminal_before_validation(client, monkeypatch):
    from types import SimpleNamespace

    from calliope.engines import modal_h3

    _, take, job = failed_remote_take(client)
    queue_manager.retry(job["id"])
    monkeypatch.setattr(
        modal_h3,
        "_load_modal",
        lambda: SimpleNamespace(
            FunctionCall=SimpleNamespace(from_id=lambda call: SimpleNamespace(get=lambda **kw: {}))
        ),
    )
    with pytest.raises(RuntimeError, match="résultat invalide"):
        await service.run_studio_job(job, {"studio_take_id": take["id"]}, lambda: False)
    assert service.public_job(queue_manager.get_job(job["id"]))["render_state"]["remote_terminal"]


@pytest.mark.parametrize("audio,expected", [(False, 409), (True, 200)])
@pytest.mark.parametrize("qc_status", ["unknown", "missing", "extra"])
def test_export_checks_legacy_approved_takes_without_generating(
    client, tmp_path, monkeypatch, audio, expected, qc_status
):
    from calliope.engines.modal_h3 import ModalH3Engine
    from calliope.studio import pacing, transcription
    from tests.test_studio import video_bytes

    project, board = new_board(client)
    monkeypatch.setattr(transcription, "assess_transcript", lambda *args, **kwargs: {
        "status": qc_status, "reason": "Diagnostic de parole à vérifier.",
    })
    monkeypatch.setattr(
        ModalH3Engine, "spawn", lambda *args: pytest.fail("export generated a take")
    )
    calls = []
    monkeypatch.setattr(
        pacing, "detect_voice", lambda *args: {"intervals": [[0, 0.6]], "max_db": -10}
    )
    monkeypatch.setattr(
        transcription,
        "transcribe_dialogue",
        lambda *args: (
            calls.append(args)
            or {
                "status": "unknown",
                "reason": "checkpoint absent",
                "speech_intervals": [],
            }
        ),
    )
    take_ids = []
    for scene in board["scenes"]:
        for shot in scene["shots"]:
            service.update_shot(
                project, shot["id"], {"dialogue": "Bonjour", "audio_mode": "native"}
            )
            take = service.store_upload(shot["id"], video_bytes(tmp_path, audio=audio), "video")
            take_ids.append(take["id"])
            service.select_take(take["id"])
            with service.database() as conn:
                conn.execute("UPDATE studio_shots SET approved=1 WHERE clip_id=?", (shot["id"],))
                conn.commit()
    for request in ["legacy-qc-1", "legacy-qc-2"]:
        response = client.post(
            f"/api/studio/projects/{project}/export", json={"request_id": request}
        )
        assert response.status_code == expected, response.text
        if audio:
            payload = json.loads(queue_manager.get_job(response.json()["job_id"])["payload_json"])
            assert all(clip["transcript"] is None for clip in payload["clips"])
            for scene in board["scenes"]:
                for shot in scene["shots"]:
                    service.approve_shot(shot["id"], False)
                    assert service.approve_shot(shot["id"], True)["approved"] is True
    with service.database() as conn:
        metadata = [
            json.loads(
                conn.execute(
                    "SELECT metadata_json FROM studio_takes WHERE id=?", (take,)
                ).fetchone()[0]
            )
            for take in take_ids
        ]
        count = conn.execute(
            "SELECT count(*) FROM studio_takes WHERE project_id=?", (project,)
        ).fetchone()[0]
        exports = conn.execute(
            "SELECT count(*) FROM studio_exports WHERE project_id=?", (project,)
        ).fetchone()[0]
    assert count == len(take_ids) and exports == (2 if audio else 0)
    assert all(
        item["dialogue_qc"]["status"] == (qc_status if audio else "inaudible") for item in metadata
    )
    assert len(calls) == (len(take_ids) if audio else 0)


@pytest.mark.parametrize('owned', [True, False])
def test_generic_retry_respects_production_owner(client, monkeypatch, owned):
    _, take, child = failed_remote_take(client)
    if owned:
        parent = queue_manager.enqueue(
            project_id=child['project_id'], kind='production',
            payload={'studio_production_id': child['project_id']},
        )
        with service.database() as conn:
            conn.execute(
                'UPDATE jobs SET payload_json=? WHERE id=?',
                (json.dumps({'studio_take_id': take['id'], 'parent_job_id': parent['id']}),
                 child['id']),
            )
            conn.commit()
        queue_manager.mark_failed(parent['id'], 'deadline')
        job = queue_manager.get_job(child['id'])
    else:
        job = queue_manager.enqueue(project_id=child['project_id'], kind='video', payload={})
        queue_manager.mark_failed(job['id'], 'ordinary failure')
        job = queue_manager.get_job(job['id'])
    response = client.post(f"/api/jobs/{job['id']}/retry")
    monkeypatch.setattr(queue_manager, 'paused', False)
    if owned:
        assert response.status_code == 409, response.text
        assert 'production' in response.json()['detail'].lower()
        assert queue_manager.get_job(job['id']) == job
        assert queue_manager.get_job(parent['id'])['status'] == 'failed'
        assert queue_manager.claim_next() is None
        with service.database() as conn:
            current = conn.execute(
                'SELECT modal_call_id FROM studio_takes WHERE id=?', (take['id'],),
            ).fetchone()
        assert current['modal_call_id'] == 'fc-live'
    else:
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'pending'
        assert queue_manager.claim_next()['id'] == job['id']
