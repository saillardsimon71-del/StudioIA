import copy
import json

import pytest
from fastapi import HTTPException

from calliope.studio import production, series_planner, service
from tests.test_studio_midroll import persisted_episode, story


def test_end_cta_preserves_the_full_story_and_sound_cues():
    plan = story()
    original = copy.deepcopy(plan["scenes"][0]["shots"])
    context = {"entities": [], "language": "fr", "directing_profile": {"cta_placement": "end"}}
    result = series_planner.insert_midroll(plan, context)
    shots = result["scenes"][0]["shots"]
    assert shots[:-1] == original
    assert shots[-1]["purpose"] == "midroll"
    assert result["audio"]["sfx"][0]["shot_index"] == 4
    assert result["audio"]["holds"][-1]["shot_index"] == 6
    frozen = copy.deepcopy(result)
    assert series_planner.insert_midroll(result, context) == frozen


def test_series_persists_the_end_cta_choice(client):
    response = client.post("/api/studio/series", json={
        "title": "Dinner", "concept": "A family argument.",
        "directing_profile": {"cta_placement": "end"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["directing_profile"]["cta_placement"] == "end"


def test_resume_keeps_the_cta_after_the_story(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, story())
    with service.database() as conn:
        frozen = json.loads(conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project,),
        ).fetchone()[0])
        frozen["directing_profile"]["cta_placement"] = "end"
        conn.execute("UPDATE studio_episodes SET context_json=? WHERE project_id=?",
                     (json.dumps(frozen), project))
        conn.commit()
    production.ensure_midroll(project)
    before = service.board(project)
    shots = [shot for scene in before["scenes"] for shot in scene["shots"]]
    assert shots[-1]["purpose"] == "midroll"
    assert shots[-2]["description"] == "Event 5"
    production.ensure_midroll(project)
    assert service.board(project)["project"]["revision"] == before["project"]["revision"]


@pytest.mark.asyncio
async def test_export_checks_the_series_cta_position(client, monkeypatch):
    project = persisted_episode(client, monkeypatch)
    with service.database() as conn:
        frozen = json.loads(conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project,),
        ).fetchone()[0])
        frozen["directing_profile"]["cta_placement"] = "end"
        conn.execute("UPDATE studio_episodes SET context_json=? WHERE project_id=?",
                     (json.dumps(frozen), project))
        conn.commit()
    with pytest.raises(HTTPException, match="incitation"):
        await service.enqueue_export(project, "middle-is-wrong-for-end")
    production.ensure_midroll(project)
    with pytest.raises(HTTPException) as missing_media:
        await service.enqueue_export(project, "end-is-valid-but-media-missing")
    assert "incitation" not in missing_media.value.detail.lower()


def test_prepared_continuous_end_cta_keeps_the_operator_camera_and_listeners(client, monkeypatch):
    from tests.test_studio_native_contract import native_plan

    project = persisted_episode(client, monkeypatch, native_plan())
    with service.database() as conn:
        frozen = json.loads(conn.execute("SELECT context_json FROM studio_episodes WHERE project_id=?", (project,)).fetchone()[0])
        frozen["directing_profile"].update(cta_placement="end", camera="static")
        conn.execute("UPDATE studio_episodes SET context_json=? WHERE project_id=?", (json.dumps(frozen), project))
        conn.commit()
    production.ensure_midroll(project)
    shots = [s for scene in service.board(project)["scenes"] for s in scene["shots"]]
    cta = shots[-1]
    service.update_shot(project, cta["id"], {
        "continue_from_previous": True, "camera": "Locked shared framing throughout.",
        "framing": "Keep the speaker and the listening character in the same frame.",
        "prompt": "The speaker finishes the aside while the listener remains seated.",
    })
    before = service.board(project)
    production.ensure_midroll(project)
    after = service.board(project)
    actual = [s for scene in after["scenes"] for s in scene["shots"]][-1]
    assert actual["continue_from_previous"]
    assert actual["camera"] == "Locked shared framing throughout."
    assert actual["framing"] == "Keep the speaker and the listening character in the same frame."
    assert before["project"]["revision"] == after["project"]["revision"]


def test_continuous_end_cta_still_repairs_wrong_required_dialogue(client, monkeypatch):
    from tests.test_studio_native_contract import native_plan

    project = persisted_episode(client, monkeypatch, native_plan())
    with service.database() as conn:
        frozen = json.loads(conn.execute("SELECT context_json FROM studio_episodes WHERE project_id=?", (project,)).fetchone()[0])
        frozen["directing_profile"].update(cta_placement="end", camera="static")
        conn.execute("UPDATE studio_episodes SET context_json=? WHERE project_id=?", (json.dumps(frozen), project))
        conn.commit()
    production.ensure_midroll(project)
    cta = service.board(project)["scenes"][0]["shots"][-1]
    service.update_shot(project, cta["id"], {"continue_from_previous": True, "dialogue": "Wrong words."})
    production.ensure_midroll(project)
    actual = service.board(project)["scenes"][0]["shots"][-1]
    assert actual["dialogue"] != "Wrong words."
