import copy
import json

import pytest

from calliope.studio import series_planner, service
from calliope.studio.screen_text import ScreenText
from tests.test_studio_creative_workshop import candidates, selected_story
from tests.test_studio_director import prepared as prepared
from tests.test_studio_story_preparation import creative_story, technical_plan


@pytest.mark.asyncio
async def test_workshop_reviews_causality_and_has_the_author_rewrite_before_planning(monkeypatch):
    first = selected_story()
    revised = selected_story()
    revised["title"] = "Version corrigée par DeepSeek"
    issue = (
        "Le calcul onze contre sept est impossible ; la hausse du compteur ne possède aucune cause."
    )
    replies = iter(
        [
            {"candidates": candidates()},
            first,
            {"approved": False, "issues": [issue]},
            revised,
            {"approved": True, "issues": []},
        ]
    )
    requests = []

    async def generate(messages, **options):
        requests.append((copy.deepcopy(messages), options))
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    story, workshop = await series_planner._develop_creative_story(
        {"entities": [], "target_seconds": 15}, "Un compteur de bodycount.", "Hook et intrigue."
    )
    assert story["title"] == revised["title"]
    assert [review["approved"] for review in workshop["reviews"]] == [False, True]
    rewrite = json.loads(requests[3][0][1]["content"])
    assert rewrite["previous_story"] == first
    assert issue in rewrite["revision_note"]
    critique = json.loads(requests[2][0][1]["content"])
    assert critique["story"] == first
    assert critique["idea"] == "Un compteur de bodycount."
    assert "planner" not in [options.get("role") for _, options in requests]


@pytest.mark.asyncio
async def test_repeated_narrative_rejection_stops_without_unbounded_rewriting(monkeypatch):
    writes, reviews = [], []

    async def write(*args, **kwargs):
        writes.append(kwargs)
        return creative_story()

    async def generate(messages, **options):
        reviews.append(messages)
        return {"approved": False, "issues": ["La révélation contredit la règle du compteur."]}

    monkeypatch.setattr(series_planner, "_write_creative_story", write)
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError, match="Contrôle narratif refusé"):
        await series_planner._review_creative_story({}, "Idée", "", creative_story())
    assert len(writes) == 1
    assert len(reviews) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "report",
    [
        {"approved": "true", "issues": []},
        {"approved": True, "issues": ["Calcul impossible."]},
        {"approved": False, "issues": []},
        {"approved": True, "issues": [None]},
    ],
)
async def test_malformed_review_never_approves_story(monkeypatch, report):
    async def generate(*args, **kwargs):
        return report

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError, match="Contrôle narratif"):
        await series_planner._review_creative_story({}, "Idée", "", creative_story())


@pytest.mark.asyncio
async def test_writer_gets_a_word_budget_before_call_and_cannot_borrow_extra_seconds(monkeypatch):
    too_long = creative_story()
    too_long["beats"] = [
        too_long["beats"][0]
        | {"dialogue_turns": [{"speaker": "keeper", "text": " ".join(["mot"] * 46)}]}
    ]
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        return copy.deepcopy(too_long if len(requests) == 1 else creative_story())

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner._write_creative_story(
        {"entities": [], "target_seconds": 15, "directing_profile": {"dialogue_density": "brisk"}},
        "Une idée de 15 secondes.",
        "",
    )
    assert len(requests) == 2
    budget = json.loads(requests[0][1]["content"])["speech_budget"]
    assert budget["max_spoken_words"] == 45
    assert budget["recommended_spoken_words"] < budget["max_spoken_words"]
    assert result == creative_story()


@pytest.mark.asyncio
async def test_numeric_overlay_repairs_receive_the_actual_schema_and_keep_the_story(monkeypatch):
    draft = technical_plan()
    label = {
        "key": "counter",
        "value": 12,
        "x": 0.5,
        "y": 0.2,
        "changes": [{"at_sec": 1.2, "value": 13}],
    }
    draft["scenes"][0]["shots"][0]["screen_text"] = [label]
    requests = []

    async def develop(*args):
        return creative_story(), {}

    async def generate(messages, **options):
        request = json.loads(messages[-1]["content"])
        requests.append(request)
        if "repair_fields" not in request:
            return copy.deepcopy(draft)
        assert request["screen_text_schema"] == ScreenText.model_json_schema()
        assert "initial_value" not in request["screen_text_schema"]["properties"]
        return {
            "values": {
                field["id"]: [label | {"event": "change"}] for field in request["repair_fields"]
            }
        }

    monkeypatch.setattr(series_planner, "_develop_creative_story", develop)
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Un compteur.",
        "",
    )
    assert requests[0]["screen_text_schema"] == ScreenText.model_json_schema()
    assert len(requests) == 2
    assert result["creative_story"] == creative_story()
    actual = result["scenes"][0]["shots"][0]["screen_text"][0]
    assert actual["value"] == 12 and actual["changes"][0]["value"] == 13
    assert actual["event"] == "change"


def test_native_fragmentation_is_rejected_before_board_approval_and_gpu(client, prepared):
    project, _ = prepared
    state = client.get(f"/api/studio/projects/{project}/director").json()
    with service.database() as conn:
        episode = conn.execute(
            "SELECT context_json,plan_json FROM studio_episodes WHERE project_id=?", (project,)
        ).fetchone()
        context, plan = json.loads(episode["context_json"]), json.loads(episode["plan_json"])
        context["target_seconds"] = sum(shot["contract"]["duration_sec"] for shot in state["shots"])
        plan["prompt_contract_version"] = 2
        conn.execute(
            "UPDATE studio_episodes SET context_json=?,plan_json=? WHERE project_id=?",
            (json.dumps(context), json.dumps(plan), project),
        )
        conn.commit()
    response = client.post(
        f"/api/studio/projects/{project}/director/approve",
        json={"expected_hash": state["storyboard_hash"]},
    )
    assert response.status_code == 422
    assert "Trop de coupes" in response.json()["detail"]
    preflight = client.get(f"/api/studio/projects/{project}/director/preflight").json()
    assert not preflight["ok"]
    assert any("Trop de coupes" in error for error in preflight["errors"])


@pytest.mark.asyncio
async def test_director_planner_regroups_fragmented_takes_without_rewriting_lines(monkeypatch):
    draft = technical_plan()
    shots = draft["scenes"][0]["shots"]
    for shot in shots:
        shot["dialogue_turns"] = [{"speaker": shot["speaker"], "text": shot["dialogue"]}]
    grouped = []
    for start in (0, 3):
        part = copy.deepcopy(shots[start])
        part["duration_sec"] = sum(shot["duration_sec"] for shot in shots[start : start + 3])
        part["dialogue_turns"] = [
            turn for shot in shots[start : start + 3] for turn in shot["dialogue_turns"]
        ]
        part["dialogue"] = "\n".join(turn["text"] for turn in part["dialogue_turns"])
        grouped.append(part)
    requests = []

    async def develop(*args):
        return creative_story(), {}

    async def generate(messages, **options):
        request = json.loads(messages[-1]["content"])
        requests.append(request)
        if "repair_fields" not in request:
            return copy.deepcopy(draft)
        assert "Trop de coupes" in request["validation_error"]
        return {
            "values": {field["id"]: copy.deepcopy(grouped) for field in request["repair_fields"]}
        }

    monkeypatch.setattr(series_planner, "_develop_creative_story", develop)
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {
            "entities": [],
            "prompt_contract_version": 2,
            "studio_director": True,
            "target_seconds": 24,
            "language": "fr",
            "directing_profile": {"cta_placement": "none"},
        },
        "Un dialogue continu.",
        "",
    )
    assert len(requests) == 2
    assert len(result["scenes"][0]["shots"]) == 2
    assert result["creative_story"] == creative_story()
    assert [
        turn["text"] for shot in result["scenes"][0]["shots"] for turn in shot["dialogue_turns"]
    ] == [turn["text"] for shot in shots for turn in shot["dialogue_turns"]]


@pytest.mark.asyncio
async def test_director_planner_compiles_motion_before_accepting_the_storyboard(monkeypatch):
    draft = technical_plan()
    shots = draft["scenes"][0]["shots"]
    grouped = []
    for start in (0, 3):
        part = copy.deepcopy(shots[start])
        part["duration_sec"] = 12.0
        part["dialogue_turns"] = [
            {"speaker": shot["speaker"], "text": shot["dialogue"]}
            for shot in shots[start : start + 3]
        ]
        part["dialogue"] = "\n".join(turn["text"] for turn in part["dialogue_turns"])
        grouped.append(part)
    draft["scenes"][0]["shots"] = grouped
    grouped[0]["action"] = " ".join(f"The keeper makes movement number {i}." for i in range(45))
    requests = []

    async def develop(*args):
        return creative_story(), {}

    async def generate(messages, **options):
        request = json.loads(messages[-1]["content"])
        requests.append(request)
        if "repair_fields" not in request:
            return copy.deepcopy(draft)
        assert "Prompt LTX trop long" in request["validation_error"]
        assert all(
            field["path"][-1] in ("action", "reaction") for field in request["repair_fields"]
        )
        return {
            "values": {
                field["id"]: "The keeper checks the case." for field in request["repair_fields"]
            }
        }

    monkeypatch.setattr(series_planner.settings, "studio_video_profile", "ltx")
    monkeypatch.setattr(series_planner, "_develop_creative_story", develop)
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {
            "entities": [],
            "prompt_contract_version": 2,
            "studio_director": True,
            "target_seconds": 24,
            "language": "fr",
            "directing_profile": {"cta_placement": "none"},
        },
        "Une idée.",
        "",
    )
    assert len(requests) == 2
    assert result["creative_story"] == creative_story()
    assert [
        turn["text"] for shot in result["scenes"][0]["shots"] for turn in shot["dialogue_turns"]
    ] == [turn["text"] for shot in grouped for turn in shot["dialogue_turns"]]
