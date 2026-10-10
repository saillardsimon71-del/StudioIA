import copy
import json

import pytest

from calliope.studio.h3_prompt import compile_h3_prompt, directing_profile
from calliope.studio.series_planner import MIDROLL_DIALOGUES, insert_midroll
from tests.test_studio_midroll import story


def test_midroll_uses_the_story_location_and_keeps_the_exact_series_language():
    plan = story()
    for shot in plan["scenes"][0]["shots"]:
        shot["location_key"] = "street"
    context = {"language": "fr", "entities": [{
        "key": "street", "name": "Neon street", "kind": "location",
        "description": "A colorful neon street.",
    }]}
    shot = insert_midroll(plan, context)["scenes"][0]["shots"][3]
    assert shot["location_key"] == "street"
    assert "clean softly lit studio" not in shot["prompt"]
    assert "zoom" in shot["camera"]
    assert shot["dialogue"] == MIDROLL_DIALOGUES["fr"]
    assert " ".join(turn["text"] for turn in shot["dialogue_turns"]) == shot["dialogue"]
    assert all(turn["speaker"] == shot["speaker"] for turn in shot["dialogue_turns"])


def test_midroll_delivery_is_clear_and_each_line_is_spoken_once():
    plan = insert_midroll(story(), {"language": "fr"})
    shot = plan["scenes"][0]["shots"][3]
    result = compile_h3_prompt(shot, {
        "language": "fr", "entities": plan["entities"],
        "directing_profile": {"preset": "fast_dialogue_social_fiction"},
    })
    assert "brisk conversational" not in result
    assert result.count("<d>") == 1
    assert "Each tagged line is spoken exactly once" in result
    assert "commente GO !</d>" in result


def test_legacy_manual_profile_and_new_automatic_profile_are_distinct():
    assert directing_profile()["production_mode"] == "automatic"
    assert directing_profile({"require_reference_approval": True})["production_mode"] == "manual"
    assert not directing_profile({"production_mode": "automatic"})["require_reference_approval"]


@pytest.fixture
def episode(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import series

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(series, "require_services", lambda: None)
    created = client.post("/api/studio/series", json={
        "title": "Test", "concept": "A misunderstanding", "language": "fr",
        "directing_profile": {"require_reference_approval": True},
    }).json()
    run = client.post(f"/api/studio/series/{created['id']}/episodes", json={
        "idea": "A discussion", "request_id": "automatic-mode",
    }).json()
    client.post(f"/api/studio/projects/{run['project_id']}/production/pause").raise_for_status()
    from calliope.studio import service
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='paused' WHERE project_id=?",
                     (run["project_id"],))
        conn.commit()
    return run | {"series_id": created["id"]}


def test_automatic_mode_preserves_episode_and_does_not_start_a_job(client, episode):
    from calliope.studio import service

    with service.database() as conn:
        before = dict(conn.execute("SELECT * FROM studio_episodes WHERE project_id=?",
                                   (episode["project_id"],)).fetchone())
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    response = client.patch(
        f"/api/studio/projects/{episode['project_id']}/production/mode",
        json={"mode": "automatic"},
    )
    response.raise_for_status()
    assert response.json()["episode"]["production_mode"] == "automatic"
    with service.database() as conn:
        after = dict(conn.execute("SELECT * FROM studio_episodes WHERE project_id=?",
                                  (episode["project_id"],)).fetchone())
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == count
        frozen = json.loads(after.pop("context_json"))
        original = json.loads(before.pop("context_json"))
        assert after == before
        assert frozen["directing_profile"]["require_reference_approval"] is False
        frozen.pop("directing_profile")
        original.pop("directing_profile")
        assert frozen == original
    current = client.get(f"/api/studio/series/{episode['series_id']}").json()
    assert current["directing_profile"]["production_mode"] == "automatic"


def test_production_mode_cannot_change_during_rendering(client, episode):
    from calliope.studio import service

    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='running' WHERE project_id=?",
                     (episode["project_id"],))
        conn.commit()
    response = client.patch(
        f"/api/studio/projects/{episode['project_id']}/production/mode",
        json={"mode": "automatic"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("second_status", ["ok", "extra", "unknown"])
async def test_automatic_uncertain_native_speech_gets_one_retry(
    client, episode, monkeypatch, second_status
):
    from calliope.queue.manager import queue_manager
    from calliope.studio import production, service

    client.patch(f"/api/studio/projects/{episode['project_id']}/production/mode",
                 json={"mode": "automatic"}).raise_for_status()
    shot = {"id": 700, "approved": False, "prompt": "", "dialogue": "Hello.",
            "audio_mode": "native", "selected_video_id": 10,
            "selected_image_id": None, "continue_from_previous": False,
            "takes": [{"id": 10, "engine": "MiniMax H3", "prompt": ""}]}
    monkeypatch.setattr(production, "load_shot", lambda _: copy.deepcopy(shot))
    monkeypatch.setattr(service, "shot_context", lambda *args: {
        "directing_profile": {"production_mode": "automatic"}, "shot": {},
    })
    monkeypatch.setattr(production, "selected_video", lambda value: value["takes"][0])
    monkeypatch.setattr(production, "trim_take", lambda *args: None)
    monkeypatch.setattr(queue_manager, "is_cancelled", lambda _: False)
    from calliope.studio import h3_prompt
    monkeypatch.setattr(h3_prompt, "compile_h3_prompt", lambda *args, **kwargs: "")
    assessments = iter([
        {"status": "unknown", "reason": "Uncertain."},
        {"status": second_status, "reason": "Second assessment."},
    ])
    retries, approved = [], []

    async def assess(_):
        return next(assessments)

    async def generate(*args, **kwargs):
        retries.append(kwargs["attempt"])
        return copy.deepcopy(shot)

    monkeypatch.setattr(production, "assess_take", assess)
    monkeypatch.setattr(production, "generate_take", generate)
    monkeypatch.setattr(service, "approve_shot", lambda *args: approved.append(args))
    if second_status == "ok":
        await production.render_shot(queue_manager.get_job(episode["job_id"]), shot, lambda: False)
        assert approved[-1] == (700, True)
    else:
        with pytest.raises(ValueError, match="après une relance"):
            await production.render_shot(queue_manager.get_job(episode["job_id"]), shot, lambda: False)
        assert (700, True) not in approved
    assert retries == [1]
