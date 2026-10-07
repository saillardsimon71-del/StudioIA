import io
import subprocess
import uuid
from contextlib import closing

import pytest
from PIL import Image

from calliope.config import settings
from calliope.db import get_db
from calliope.queue.manager import queue_manager


@pytest.fixture(autouse=True)
def pause_studio_queue():
    previous = queue_manager.paused
    old_key = settings.llm_api_key
    old_image_key = settings.studio_image_api_key
    old_profiles = [dict(p) for p in settings.llm_profiles]
    old_active_id = settings.llm_active_id
    queue_manager.paused = True
    yield
    queue_manager.paused = previous
    settings.llm_api_key = old_key
    settings.studio_image_api_key = old_image_key
    settings.llm_profiles = old_profiles
    settings.llm_active_id = old_active_id


def new_board(client):
    response = client.post(
        "/api/studio/projects",
        json={
            "title": "Test généraliste",
            "idea": "Une porte ouvre sur la mer.",
            "aspect_ratio": "16:9",
        },
    )
    assert response.status_code == 200, response.text
    project_id = response.json()["id"]
    response = client.post(
        f"/api/studio/projects/{project_id}/plan",
        json={
            "text": "EXT. MER - JOUR\nUne porte ouvre sur la mer.\n\n"
            "INT. ATELIER - NUIT\nUne lampe s'allume.",
            "mode": "script",
            "use_llm": False,
        },
    )
    assert response.status_code == 200, response.text
    return project_id, response.json()


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (128, 72), "green").save(buffer, "PNG")
    return buffer.getvalue()


def video_bytes(tmp_path, audio=False):
    path = tmp_path / f"{uuid.uuid4().hex}.mp4"
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=128x72:r=24:d=0.6",
    ]
    if audio:
        command += [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.6",
            "-c:a",
            "aac",
            "-shortest",
        ]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True, capture_output=True)
    return path.read_bytes()


def upload(client, shot_id, content, kind="image"):
    response = client.post(
        f"/api/studio/shots/{shot_id}/upload",
        data={"kind": kind},
        files={
            "file": (
                "ref.png" if kind == "image" else "clip.mp4",
                content,
                "image/png" if kind == "image" else "video/mp4",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_script_becomes_persistent_scenes_and_shots(client):
    project_id, board = new_board(client)
    assert len(board["scenes"]) == 2
    assert all(scene["shots"] for scene in board["scenes"])
    assert board["project"]["aspect_ratio"] == "16:9"
    assert client.get(f"/api/studio/projects/{project_id}").json() == board


@pytest.mark.parametrize("audio_mode", ["native", "silent"])
def test_video_request_preserves_dialogue_without_spoken_instructions(client, audio_mode):
    import json

    project_id, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    visual_prompt = "A pear in a dark interrogation room."
    dialogue = "Who am I going to kill?"
    client.patch(
        f"/api/studio/projects/{project_id}/shots/{shot_id}",
        json={"prompt": visual_prompt, "dialogue": dialogue, "audio_mode": audio_mode},
    ).raise_for_status()
    result = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={"kind": "video", "request_id": str(uuid.uuid4()), "n_takes": 1},
    ).json()
    take = result["takes"][0]
    prompt = take["prompt"]
    assert prompt.startswith(visual_prompt)
    assert "say exactly" not in prompt
    assert "Spoken dialogue" not in prompt
    assert (f"<d>{dialogue}</d>" in prompt) is (audio_mode == "native")
    with closing(get_db(settings.db_path)) as conn:
        saved = conn.execute(
            "SELECT request_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()[0]
    assert json.loads(saved)["prompt"] == prompt


def test_rerender_keeps_selected_video_and_request_is_idempotent(client, tmp_path):
    _, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    take = upload(client, shot["id"], video_bytes(tmp_path), "video")
    client.post(f"/api/studio/takes/{take['id']}/select").raise_for_status()
    client.post(
        f"/api/studio/shots/{shot['id']}/approve", json={"approved": True}
    ).raise_for_status()
    request = {"kind": "video", "request_id": str(uuid.uuid4()), "n_takes": 2}
    first = client.post(f"/api/studio/shots/{shot['id']}/generate", json=request)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/studio/shots/{shot['id']}/generate", json=request)
    assert second.json() == first.json()
    with closing(get_db(settings.db_path)) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM studio_takes WHERE clip_id=?", (shot["id"],)
            ).fetchone()[0]
            == 3
        )
    current = client.get(f"/api/studio/projects/{board['project']['id']}").json()
    current_shot = current["scenes"][0]["shots"][0]
    assert current_shot["selected_video_id"] == take["id"]
    assert current_shot["approved"] is True


def test_changed_reference_invalidates_approval_without_deleting_takes(client, tmp_path):
    project_id, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    image = upload(client, shot_id, png_bytes())
    client.post(f"/api/studio/takes/{image['id']}/select").raise_for_status()
    video = upload(client, shot_id, video_bytes(tmp_path), "video")
    client.post(f"/api/studio/takes/{video['id']}/select").raise_for_status()
    client.post(f"/api/studio/shots/{shot_id}/approve", json={"approved": True}).raise_for_status()
    replacement = upload(client, shot_id, png_bytes())
    response = client.post(f"/api/studio/takes/{replacement['id']}/select")
    assert response.status_code == 200, response.text
    assert response.json()["approved"] is False
    assert response.json()["selected_video_id"] is None
    assert len(response.json()["takes"]) == 3
    assert (
        client.post(
            f"/api/studio/projects/{project_id}/export", json={"request_id": str(uuid.uuid4())}
        ).status_code
        == 409
    )


def test_export_requires_every_shot_approved_and_freezes_selection(client, tmp_path):
    project_id, board = new_board(client)
    request_id = str(uuid.uuid4())
    assert (
        client.post(
            f"/api/studio/projects/{project_id}/export", json={"request_id": request_id}
        ).status_code
        == 409
    )
    ids = []
    for scene in board["scenes"]:
        shot_id = scene["shots"][0]["id"]
        take = upload(client, shot_id, video_bytes(tmp_path, audio=True), "video")
        ids.append(take["id"])
        client.post(f"/api/studio/takes/{take['id']}/select").raise_for_status()
        client.post(
            f"/api/studio/shots/{shot_id}/approve", json={"approved": True}
        ).raise_for_status()
    response = client.post(
        f"/api/studio/projects/{project_id}/export", json={"request_id": request_id}
    )
    assert response.status_code == 200, response.text
    import json

    from calliope.queue.manager import queue_manager

    snapshot = json.loads(queue_manager.get_job(response.json()["job_id"])["payload_json"])
    assert [row["take_id"] for row in snapshot["clips"]] == ids
    assert snapshot["aspect_ratio"] == "16:9"


def test_plan_does_not_replace_an_existing_board(client):
    project_id, board = new_board(client)
    response = client.post(
        f"/api/studio/projects/{project_id}/plan",
        json={
            "text": "Autre script",
            "mode": "script",
            "use_llm": False,
        },
    )
    assert response.status_code == 409
    assert client.get(f"/api/studio/projects/{project_id}").json() == board


def test_config_never_returns_credentials(client):
    response = client.patch(
        "/api/studio/config",
        json={
            "llm_api_key": "text-test-secret",
            "image_api_key": "image-test-secret",
        },
    )
    assert response.status_code == 200, response.text
    response = client.get("/api/studio/config")
    assert "text-test-secret" not in response.text
    assert "image-test-secret" not in response.text
    assert response.json()["llm_configured"] is True


def test_malformed_video_is_rejected_without_a_take(client):
    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    response = client.post(
        f"/api/studio/shots/{shot_id}/upload",
        data={"kind": "video"},
        files={"file": ("bad.mp4", b"not video", "video/mp4")},
    )
    assert response.status_code == 422
    current = client.get(f"/api/studio/projects/{board['project']['id']}").json()
    assert current["scenes"][0]["shots"][0]["takes"] == []


def test_job_resume_reuses_remote_call_id(client):
    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    response = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={
            "kind": "video",
            "request_id": str(uuid.uuid4()),
            "n_takes": 1,
        },
    )
    assert response.status_code == 200, response.text
    take_id = response.json()["takes"][0]["id"]
    job_id = response.json()["jobs"][0]["id"]
    with closing(get_db(settings.db_path)) as conn:
        conn.execute("UPDATE studio_takes SET modal_call_id=? WHERE id=?", ("fc-saved", take_id))
        conn.execute(
            "UPDATE jobs SET status='failed',error='Connexion perdue' WHERE id=?", (job_id,)
        )
        conn.commit()
    response = client.post(f"/api/studio/jobs/{job_id}/resume")
    assert response.status_code == 200, response.text
    with closing(get_db(settings.db_path)) as conn:
        assert (
            conn.execute(
                "SELECT modal_call_id FROM studio_takes WHERE id=?", (take_id,)
            ).fetchone()[0]
            == "fc-saved"
        )


@pytest.mark.asyncio
async def test_worker_restart_does_not_submit_a_second_gpu_call(client, tmp_path, monkeypatch):
    import asyncio
    import base64

    from calliope.engines import modal_h3
    from calliope.studio.service import run_studio_job

    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    response = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={
            "kind": "video",
            "request_id": str(uuid.uuid4()),
        },
    ).json()
    job = queue_manager.get_job(response["jobs"][0]["id"])
    take_id = response["takes"][0]["id"]
    state = {"spawn": 0, "polled": []}
    content = video_bytes(tmp_path, audio=True)

    class Engine:
        def __init__(self, *args):
            pass

        def spawn(self, request):
            state["spawn"] += 1
            return "fc-persistent"

        def poll(self, call_id):
            state["polled"].append(call_id)
            return {"video_base64": base64.b64encode(content).decode(), "metadata": {}}

    monkeypatch.setattr(modal_h3, "ModalH3Engine", Engine)
    with pytest.raises(asyncio.CancelledError):
        await run_studio_job(job, {"studio_take_id": take_id}, lambda: True)
    outputs = await run_studio_job(job, {"studio_take_id": take_id}, lambda: False)
    assert state == {"spawn": 1, "polled": ["fc-persistent"]}
    assert outputs and outputs[0].endswith("video.mp4")
    with closing(get_db(settings.db_path)) as conn:
        assert (
            conn.execute(
                "SELECT selected_video_id FROM studio_shots WHERE clip_id=?", (shot_id,)
            ).fetchone()[0]
            is None
        )


@pytest.mark.asyncio
async def test_unknown_submission_state_does_not_spend_again(client, monkeypatch):
    from calliope.engines import modal_h3
    from calliope.studio.service import run_studio_job

    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    response = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={
            "kind": "video",
            "request_id": str(uuid.uuid4()),
        },
    ).json()
    take_id = response["takes"][0]["id"]
    job = queue_manager.get_job(response["jobs"][0]["id"])
    with closing(get_db(settings.db_path)) as conn:
        conn.execute("UPDATE studio_takes SET submission_state='submitting' WHERE id=?", (take_id,))
        conn.commit()

    def forbidden(*args):
        pytest.fail("Une soumission incertaine ne doit pas appeler Modal.")

    monkeypatch.setattr(modal_h3.ModalH3Engine, "spawn", forbidden)
    with pytest.raises(RuntimeError, match="avant sa confirmation"):
        await run_studio_job(job, {"studio_take_id": take_id}, lambda: False)


@pytest.mark.asyncio
async def test_cancelled_running_take_stops_original_call(client, monkeypatch):
    from calliope.engines import modal_h3
    from calliope.studio.service import StudioCancelledError, run_studio_job

    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    response = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={
            "kind": "video",
            "request_id": str(uuid.uuid4()),
        },
    ).json()
    take_id = response["takes"][0]["id"]
    job_id = response["jobs"][0]["id"]
    with closing(get_db(settings.db_path)) as conn:
        conn.execute("UPDATE studio_takes SET modal_call_id=? WHERE id=?", ("fc-cancel", take_id))
        conn.commit()
    cancelled = []
    monkeypatch.setattr(
        modal_h3.ModalH3Engine, "cancel", lambda self, identifier: cancelled.append(identifier)
    )
    job = queue_manager.get_job(job_id)
    queue_manager.cancel(job_id)
    with pytest.raises(StudioCancelledError):
        await run_studio_job(job, {"studio_take_id": take_id}, lambda: False)
    assert cancelled == ["fc-cancel"]


@pytest.mark.asyncio
async def test_changed_image_endpoint_never_receives_new_credentials(monkeypatch):
    from calliope.studio import service

    monkeypatch.setattr(settings, "studio_image_base_url", "https://current.example/v1")
    monkeypatch.setattr(settings, "studio_image_api_key", "new-endpoint-secret")

    def forbidden(**kwargs):
        pytest.fail("Aucune connexion ne doit être créée pour l'ancien service.")

    monkeypatch.setattr(service.httpx, "AsyncClient", forbidden)
    with pytest.raises(ValueError, match="a changé"):
        await service._generate_image({"image_base_url": "https://previous.example/v1"})


def test_cancel_pending_restored_job_also_stops_modal(client, monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    result = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={
            "kind": "video",
            "request_id": str(uuid.uuid4()),
        },
    ).json()
    take_id = result["takes"][0]["id"]
    job_id = result["jobs"][0]["id"]
    with closing(get_db(settings.db_path)) as conn:
        conn.execute("UPDATE studio_takes SET modal_call_id=? WHERE id=?", ("fc-restored", take_id))
        conn.commit()
    stopped = []
    monkeypatch.setattr(ModalH3Engine, "cancel", lambda self, call_id: stopped.append(call_id))
    response = client.post(f"/api/studio/jobs/{job_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert stopped == ["fc-restored"]


@pytest.mark.asyncio
async def test_cancel_during_reference_preparation_does_not_spawn(client, monkeypatch):
    from calliope.engines import modal_h3
    from calliope.studio.service import StudioCancelledError, run_studio_job

    _, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    result = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={
            "kind": "video",
            "request_id": str(uuid.uuid4()),
        },
    ).json()
    take_id = result["takes"][0]["id"]
    job_id = result["jobs"][0]["id"]

    def prepare(**kwargs):
        queue_manager.cancel(job_id)
        return {}

    def forbidden(*args):
        pytest.fail("Le GPU ne doit pas démarrer après cette annulation.")

    monkeypatch.setattr(modal_h3, "build_request", prepare)
    monkeypatch.setattr(modal_h3.ModalH3Engine, "spawn", forbidden)
    with pytest.raises(StudioCancelledError):
        await run_studio_job(
            queue_manager.get_job(job_id), {"studio_take_id": take_id}, lambda: False
        )


def ready_export(client, tmp_path):
    import json

    project_id, board = new_board(client)
    for scene in board["scenes"]:
        shot_id = scene["shots"][0]["id"]
        take = upload(client, shot_id, video_bytes(tmp_path, audio=True), "video")
        client.post(f"/api/studio/takes/{take['id']}/select").raise_for_status()
        client.post(
            f"/api/studio/shots/{shot_id}/approve", json={"approved": True}
        ).raise_for_status()
    response = client.post(
        f"/api/studio/projects/{project_id}/export", json={"request_id": str(uuid.uuid4())}
    ).json()
    job = queue_manager.get_job(response["job_id"])
    return project_id, board, job, json.loads(job["payload_json"])


@pytest.mark.asyncio
async def test_export_cancellation_stays_cancelled_in_queue(client, tmp_path, monkeypatch):
    from calliope.events.bus import event_bus
    from calliope.queue.worker import QueueWorker, _CancelledByUser

    _, _, job, _ = ready_export(client, tmp_path)

    async def progress(event, data):
        if event == "job.progress" and data.get("progress", 0) > 0:
            queue_manager.cancel(job["id"])

    monkeypatch.setattr(event_bus, "publish", progress)
    with pytest.raises(_CancelledByUser):
        await QueueWorker()._run_job(job)
    assert queue_manager.get_job(job["id"])["error"] == "cancelled"
    assert client.post(f"/api/studio/jobs/{job['id']}/resume").status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_export_completion_respects_snapshot_revision(client, tmp_path, changed):
    from calliope.queue.worker import QueueWorker

    project_id, board, job, _ = ready_export(client, tmp_path)
    if changed:
        shot_id = board["scenes"][0]["shots"][0]["id"]
        client.patch(
            f"/api/studio/projects/{project_id}/shots/{shot_id}",
            json={"prompt": "Nouveau mouvement de caméra"},
        ).raise_for_status()
    output = await QueueWorker()._run_job(job)
    assert output and output[0].endswith(".mp4")
    current = client.get(f"/api/studio/projects/{project_id}").json()
    assert current["project"]["status"] == ("draft" if changed else "completed")
    assert current["exports"][0]["stale"] is changed
