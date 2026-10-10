import json
import sqlite3

import pytest

from calliope.config import settings
from calliope.studio import production, service
from calliope.studio.schema import migrate_studio
from tests.test_studio_series import (
    episode,
    planned_episode,
    series,
)
from tests.test_studio_series import (
    isolated_production as isolated_production,
)


def stopped_episode(client, series_id):
    result = episode(client, series_id)
    result.raise_for_status()
    project_id = result.json()["project_id"]
    production.save_episode_plan(project_id, planned_episode({}))
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_productions SET status='paused' WHERE project_id=?", (project_id,)
        )
        conn.execute("UPDATE jobs SET status='done' WHERE project_id=?", (project_id,))
        conn.commit()
    return service.board(project_id)


def lifecycle(client, series_id, action):
    url = f"/api/studio/series/{series_id}"
    if action == "archive":
        return client.patch(url, json={"archived": True})
    return client.delete(url)


def test_archive_and_restore_preserve_series_episode_and_plan(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    original = client.get(f"/api/studio/series/{item['id']}").json()
    result = lifecycle(client, item["id"], "archive")
    assert result.status_code == 200, result.text
    assert result.json()["archived"] is True
    assert result.json()["episodes"] == original["episodes"]
    assert result.json()["entities"] == original["entities"]
    assert client.get("/api/studio/series").json()["series"] == []
    archived = client.get("/api/studio/series?archived=true").json()["series"]
    assert [entry["id"] for entry in archived] == [item["id"]]
    assert client.get(f"/api/studio/projects/{board['project']['id']}").json() == board
    restored = client.patch(f"/api/studio/series/{item['id']}", json={"archived": False})
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived"] is False
    assert [entry["id"] for entry in client.get("/api/studio/series").json()["series"]] == [
        item["id"]
    ]
    assert client.get("/api/studio/series?archived=true").json()["series"] == []


def test_archived_series_cannot_start_jobs_until_restored(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    lifecycle(client, item["id"], "archive").raise_for_status()
    project_id = board["project"]["id"]
    with service.database() as conn:
        jobs_before = conn.execute("SELECT * FROM jobs").fetchall()
    assert episode(client, item["id"], request="next").status_code == 409
    assert client.post(f"/api/studio/projects/{project_id}/production/resume").status_code == 409
    shot_id = board["scenes"][0]["shots"][0]["id"]
    result = client.post(
        f"/api/studio/shots/{shot_id}/generate",
        json={"kind": "image", "request_id": "archived-image", "n_takes": 1},
    )
    assert result.status_code == 409, result.text
    result = client.post(
        f"/api/studio/projects/{project_id}/export", json={"request_id": "archived-export"}
    )
    assert result.status_code == 409, result.text
    with service.database() as conn:
        assert conn.execute("SELECT * FROM jobs").fetchall() == jobs_before


@pytest.mark.parametrize("action", ["archive", "delete"])
@pytest.mark.parametrize("status", ["pending", "running"])
def test_active_production_refuses_lifecycle_without_mutating_data(client, action, status):
    item = series(client)
    project_id = episode(client, item["id"]).json()["project_id"]
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_productions SET status=? WHERE project_id=?", (status, project_id)
        )
        conn.commit()
    result = lifecycle(client, item["id"], action)
    assert result.status_code == 409, result.text
    current = client.get(f"/api/studio/series/{item['id']}").json()
    assert current["archived"] is False
    assert current["episodes"][0]["status"] == status


@pytest.mark.parametrize("action", ["archive", "delete"])
def test_unfinished_remote_call_keeps_series_and_take_diagnostics(client, action):
    item = series(client)
    board = stopped_episode(client, item["id"])
    shot_id = board["scenes"][0]["shots"][0]["id"]
    queued = service.enqueue_takes(shot_id, {"kind": "image", "request_id": "remote", "n_takes": 1})
    take_id = queued["takes"][0]["id"]
    with service.database() as conn:
        conn.execute("UPDATE jobs SET status='failed'")
        conn.execute(
            "UPDATE studio_takes SET modal_call_id='fc-test',status='failed' WHERE id=?", (take_id,)
        )
        conn.commit()
    result = lifecycle(client, item["id"], action)
    assert result.status_code == 409, result.text
    with service.database() as conn:
        assert (
            conn.execute(
                "SELECT modal_call_id FROM studio_takes WHERE id=?", (take_id,)
            ).fetchone()[0]
            == "fc-test"
        )
        conn.execute(
            "UPDATE studio_takes SET render_state_json=? WHERE id=?",
            (json.dumps({"remote_terminal": True}), take_id),
        )
        conn.commit()
    assert lifecycle(client, item["id"], action).status_code == 200


@pytest.mark.parametrize("action", ["archive", "delete"])
def test_ambiguous_submission_cannot_lose_its_record(client, action):
    item = series(client)
    board = stopped_episode(client, item["id"])
    shot_id = board["scenes"][0]["shots"][0]["id"]
    service.enqueue_takes(shot_id, {"kind": "image", "request_id": "ambiguous", "n_takes": 1})
    with service.database() as conn:
        conn.execute("UPDATE jobs SET status='failed'")
        conn.execute("UPDATE studio_takes SET submission_state='submitting'")
        conn.commit()
    assert lifecycle(client, item["id"], action).status_code == 409


@pytest.mark.parametrize("action", ["archive", "delete"])
def test_pending_standalone_job_blocks_lifecycle(client, action):
    item = series(client)
    board = stopped_episode(client, item["id"])
    shot_id = board["scenes"][0]["shots"][0]["id"]
    service.enqueue_takes(shot_id, {"kind": "image", "request_id": "queued", "n_takes": 1})
    assert lifecycle(client, item["id"], action).status_code == 409


def test_delete_cascades_only_its_episodes_and_accepts_staged_child_of_failed_parent(client):
    item = series(client)
    other = series(client, title="Keep this series")
    board = stopped_episode(client, item["id"])
    other_board = stopped_episode(client, other["id"])
    project_id = board["project"]["id"]
    artifact = settings.assets_dir / "preserved.mp4"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"diagnostic artifact")
    with service.database() as conn:
        parent = conn.execute(
            "SELECT job_id FROM studio_productions WHERE project_id=?", (project_id,)
        ).fetchone()[0]
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (parent,))
        conn.execute(
            "INSERT INTO jobs(project_id,kind,payload_json) VALUES(?,?,?)",
            (project_id, "studio", json.dumps({"parent_job_id": parent})),
        )
        conn.commit()
    deleted = lifecycle(client, item["id"], "delete")
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/studio/series/{item['id']}").status_code == 404
    assert client.get(f"/api/studio/projects/{project_id}").status_code == 404
    assert client.get(f"/api/studio/projects/{other_board['project']['id']}").json() == other_board
    assert client.get(f"/api/studio/series/{other['id']}").status_code == 200
    with service.database() as conn:
        for table in (
            "jobs",
            "studio_takes",
            "studio_episodes",
            "studio_productions",
            "clips",
            "scenes",
        ):
            assert (
                conn.execute(
                    f"SELECT count(*) FROM {table} WHERE project_id=?", (project_id,)
                ).fetchone()[0]
                == 0
            )
        assert not conn.execute(
            "SELECT 1 FROM studio_entities WHERE series_id=?", (item["id"],)
        ).fetchone()
    assert artifact.read_bytes() == b"diagnostic artifact"


def test_delete_archived_empty_series_and_missing_series(client):
    item = series(client)
    lifecycle(client, item["id"], "archive").raise_for_status()
    lifecycle(client, item["id"], "delete").raise_for_status()
    assert client.get("/api/studio/series?archived=true").json()["series"] == []
    assert lifecycle(client, item["id"], "delete").status_code == 404
    assert lifecycle(client, item["id"], "archive").status_code == 404


@pytest.mark.parametrize("action", ["archive", "delete"])
def test_running_agent_blocks_lifecycle(client, monkeypatch, action):
    from calliope.agent.harness.runner import runner

    item = series(client)
    board = stopped_episode(client, item["id"])
    with service.database() as conn:
        conn.execute("INSERT INTO agent_sessions(project_id) VALUES(?)", (board["project"]["id"],))
        conn.commit()
    monkeypatch.setattr(runner, "is_running", lambda identifier: True)
    assert lifecycle(client, item["id"], action).status_code == 409


def test_archived_episodes_are_hidden_from_the_active_project_list(client):
    item = series(client)
    board = stopped_episode(client, item["id"])
    independent = client.post("/api/studio/projects", json={"title": "Independent"}).json()
    lifecycle(client, item["id"], "archive").raise_for_status()
    assert [project["id"] for project in service.list_projects()] == [independent["id"]]
    client.patch(f"/api/studio/series/{item['id']}", json={"archived": False}).raise_for_status()
    assert {project["id"] for project in service.list_projects()} == {
        independent["id"],
        board["project"]["id"],
    }


def test_archive_migration_is_idempotent_and_preserves_legacy_series(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE studio_series (id INTEGER PRIMARY KEY, title TEXT, concept TEXT)"
        )
        conn.execute("INSERT INTO studio_series VALUES(1,'Legacy','Keep the concept')")
    migrate_studio(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT title,concept,archived FROM studio_series").fetchone() == (
            "Legacy",
            "Keep the concept",
            0,
        )
        conn.execute("UPDATE studio_series SET archived=1")
    migrate_studio(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT archived FROM studio_series").fetchone()[0] == 1
