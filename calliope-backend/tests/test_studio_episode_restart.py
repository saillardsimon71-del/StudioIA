import asyncio
import json
import sqlite3

import pytest

from calliope.config import settings
from calliope.queue.manager import queue_manager
from calliope.studio import production, service
from tests.test_studio_series import isolated_production as isolated_production
from tests.test_studio_series import planned_episode, series
from tests.test_studio_series_lifecycle import stopped_episode


def restart(client, project_id, request="restart-first"):
    return client.post(
        f"/api/studio/projects/{project_id}/production/restart",
        json={"request_id": request},
    )


def test_restart_keeps_series_and_episode_inputs_and_archives_raw_evidence(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    shot_id = board["scenes"][0]["shots"][0]["id"]
    artifact = settings.assets_dir / "old-take.mp4"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"unchanged raw video")
    with service.database() as conn:
        entity = conn.execute("SELECT id FROM studio_entities").fetchone()[0]
        conn.execute(
            "UPDATE studio_entities SET reference_path=?,identity_json=? WHERE id=?",
            (str(artifact), json.dumps({"reference_approved": True}), entity),
        )
        old_episode = dict(conn.execute("SELECT * FROM studio_episodes").fetchone())
        old_job = conn.execute("SELECT job_id FROM studio_productions").fetchone()[0]
        conn.execute(
            "INSERT INTO studio_takes(project_id,clip_id,kind,status,prompt,engine,request_id,"
            "request_hash,modal_call_id,output_path,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, shot_id, "video", "done", "Exact H3 prompt", "modal_h3", "take", "hash",
             "fc-preserved", str(artifact), json.dumps({"speech_qc": {"status": "unknown"}})),
        )
        conn.execute(
            "INSERT INTO studio_exports(project_id,request_id,revision,aspect_ratio,snapshot_json,"
            "output_path) VALUES(?,?,?,?,?,?)",
            (project_id, "old-export", 0, "9:16", '{"clips":[]}', str(artifact)),
        )
        conn.commit()
    changed = client.patch(
        f"/api/studio/series/{item['id']}",
        json={"language": "fr", "target_seconds": 30, "bible": {"tone": "Comédie"}},
    )
    changed.raise_for_status()
    canonical = changed.json()

    result = restart(client, project_id)
    assert result.status_code == 200, result.text
    fresh = result.json()
    assert fresh["project"]["id"] == project_id
    assert fresh["episode"]["number"] == 1
    assert fresh["scenes"] == []
    assert fresh["jobs"] == []
    assert fresh["exports"] == []
    assert fresh["production"]["status"] == "pending"
    assert fresh["production"]["stage"] == "writing"
    assert fresh["production"]["progress"] == 0
    assert fresh["project"]["script_source"] == ""
    assert fresh["project"]["revision"] > board["project"]["revision"]
    retained = client.get(f"/api/studio/series/{item['id']}").json()
    for key in ("title", "concept", "bible", "entities", "directing_profile", "language",
                "target_seconds", "aspect_ratio"):
        assert retained[key] == canonical[key]
    with service.database() as conn:
        current = dict(conn.execute("SELECT * FROM studio_episodes").fetchone())
        assert current["idea"] == old_episode["idea"]
        assert current["objective"] == old_episode["objective"]
        assert current["request_id"] == old_episode["request_id"]
        assert current["plan_json"] == "{}"
        assert current["summary"] == ""
        assert current["canon_revision"] is None
        frozen = json.loads(current["context_json"])
        assert frozen["language"] == "fr"
        assert frozen["target_seconds"] == 30
        assert frozen["entities"][0]["reference_path"] == str(artifact)
        assert frozen["history"] == []
        assert not conn.execute("SELECT 1 FROM jobs WHERE id=?", (old_job,)).fetchone()
        assert not conn.execute("SELECT 1 FROM studio_takes").fetchone()
        archive = json.loads(conn.execute("SELECT snapshot_json FROM studio_episode_archives")
                             .fetchone()[0])
        assert archive["studio_episodes"][0] == old_episode
        assert archive["studio_takes"][0]["prompt"] == "Exact H3 prompt"
        assert archive["studio_takes"][0]["modal_call_id"] == "fc-preserved"
        assert json.loads(archive["studio_takes"][0]["metadata_json"])["speech_qc"] == {
            "status": "unknown"
        }
        assert archive["studio_exports"][0]["output_path"] == str(artifact)
        assert archive["studio_shot_context"][0]["clip_id"] == shot_id
        assert archive["jobs"][0]["id"] == old_job
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert artifact.read_bytes() == b"unchanged raw video"


def test_duplicate_restart_does_not_reset_a_second_time(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    first = restart(client, project_id)
    first.raise_for_status()
    assert restart(client, project_id).json() == first.json()
    assert restart(client, project_id, "another-click").status_code == 409
    with service.database() as conn:
        assert conn.execute("SELECT count(*) FROM studio_episode_archives").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.parametrize("busy", ["pending", "running", "remote", "ambiguous", "standalone"])
def test_restart_refuses_active_or_ambiguous_work_without_changing_episode(client, busy):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    if busy in ("pending", "running"):
        with service.database() as conn:
            conn.execute("UPDATE studio_productions SET status=?", (busy,))
            conn.commit()
    else:
        shot_id = board["scenes"][0]["shots"][0]["id"]
        service.enqueue_takes(shot_id, {"kind": "image", "request_id": "busy", "n_takes": 1})
        with service.database() as conn:
            if busy != "standalone":
                conn.execute("UPDATE jobs SET status='failed'")
                if busy == "remote":
                    conn.execute(
                        "UPDATE studio_takes SET modal_call_id='fc-active',status='failed'"
                    )
                else:
                    conn.execute("UPDATE studio_takes SET submission_state='submitting'")
            conn.commit()
    before = service.board(project_id)
    result = restart(client, project_id)
    assert result.status_code == 409, result.text
    assert service.board(project_id) == before


def test_restart_refuses_completed_archived_or_non_series_episodes(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='done'")
        conn.commit()
    assert restart(client, project_id).status_code == 409
    client.patch(f"/api/studio/series/{item['id']}", json={"archived": True}).raise_for_status()
    assert restart(client, project_id).status_code == 409
    independent = client.post("/api/studio/projects", json={"title": "Independent"}).json()
    assert restart(client, independent["id"]).status_code == 404


def test_restart_missing_services_preserves_existing_plan(client, monkeypatch):
    item = series(client)
    board = stopped_episode(client, item["id"])
    monkeypatch.setattr(settings, "dry_run", True)
    assert restart(client, board["project"]["id"]).status_code == 422
    assert service.board(board["project"]["id"]) == board


def test_restart_is_atomic_if_creating_the_replacement_job_fails(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    with service.database() as conn:
        conn.execute("CREATE TRIGGER reject_new_job BEFORE INSERT ON jobs "
                     "BEGIN SELECT RAISE(ABORT,'cannot create new job'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="cannot create new job"):
        restart(client, project_id)
    assert service.board(project_id) == board
    with service.database() as conn:
        assert conn.execute("SELECT count(*) FROM studio_episode_archives").fetchone()[0] == 0


def test_restart_cannot_invalidate_an_episode_already_used_for_continuity(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    with service.database() as conn:
        conn.execute("UPDATE studio_episodes SET canon_revision=0")
        conn.commit()
    before = service.board(project_id)
    assert restart(client, project_id).status_code == 409
    assert service.board(project_id) == before


def test_restart_a_failed_episode_before_planning_and_validates_request_id(client):
    item = series(client)
    created = client.post(f"/api/studio/series/{item['id']}/episodes", json={
        "idea": "Original idea before any plan", "request_id": "unplanned",
    }).json()
    project_id = created["project_id"]
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='failed',error='planner failed'")
        conn.execute("UPDATE jobs SET status='failed'")
        conn.commit()
    before = service.board(project_id)
    assert restart(client, project_id, "").status_code == 422
    assert service.board(project_id) == before
    result = restart(client, project_id)
    result.raise_for_status()
    assert result.json()["scenes"] == []
    assert result.json()["production"]["error"] is None
    assert result.json()["production"]["status"] == "pending"


def test_restart_replans_and_keeps_audio_preferences_without_old_shot_cues(client, monkeypatch):
    item = series(client)
    board = stopped_episode(client, item["id"])
    project_id = board["project"]["id"]
    with service.database() as conn:
        cursor = conn.execute(
            "INSERT INTO studio_audio_assets(project_id,kind,name,path,duration_sec) "
            "VALUES(?,?,?,?,?)",
            (project_id, "music", "My music", "/test/music.wav", 30),
        )
        asset_id = cursor.lastrowid
        conn.commit()
    client.patch(f"/api/studio/projects/{project_id}/audio", json={
        "music": "playful", "music_gain_db": -31, "music_asset_id": asset_id,
    }).raise_for_status()
    restart(client, project_id).raise_for_status()
    captured = []

    async def plan(context, idea, objective):
        captured.append((context, idea, objective))
        fresh = planned_episode(context)
        fresh["title"] = "A newly directed exchange"
        return fresh

    async def stop_before_images(job, should_stop):
        raise production.ProductionPausedError()

    monkeypatch.setattr(production, "plan_episode", plan)
    monkeypatch.setattr(production, "ensure_entities", stop_before_images)
    with service.database() as conn:
        job_id = conn.execute("SELECT job_id FROM studio_productions").fetchone()[0]
    job = queue_manager.get_job(job_id)
    asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
    fresh = service.board(project_id)
    assert len(captured) == 1
    assert captured[0][1] == "The signal names the keeper."
    assert captured[0][0]["entities"][0]["key"] == "keeper"
    assert fresh["project"]["title"] == "A newly directed exchange"
    fresh_shot = fresh["scenes"][0]["shots"][0]
    assert fresh_shot["id"] != board["scenes"][0]["shots"][0]["id"]
    assert fresh["audio"]["music"] == "playful"
    assert fresh["audio"]["music_gain_db"] == -31
    assert fresh["audio"]["music_asset_id"] == asset_id
    assert fresh["audio"]["sfx"][0]["shot_id"] == fresh_shot["id"]
    assert len(fresh["audio"]["assets"]) == 1
    assert fresh_shot["takes"] == []
