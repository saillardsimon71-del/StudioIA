import copy
import json

import pytest

from calliope.studio import series_planner
from calliope.studio.h3_prompt import compile_h3_prompt
from calliope.studio.visual_identity import compile_image_request
from tests.test_studio_native_contract import native_plan


@pytest.fixture(autouse=True)
def direct_story_writer(monkeypatch):
    async def develop(context, idea, objective, trace=None):
        return await series_planner._write_creative_story(context, idea, objective, trace), {}
    monkeypatch.setattr(series_planner, '_develop_creative_story', develop)


def creative_story():
    plan = native_plan()
    return {
        "title": "Le coffre fuit",
        "narrative_outline": plan["narrative_outline"],
        "beats": [
            {"action": f"Le gardien réagit à l'événement {index}.",
             "dialogue_turns": [{"speaker": "keeper", "text": f"Line {index}."}]}
            for index in range(6)
        ],
    }


def technical_plan():
    plan = native_plan()
    for index, shot in enumerate(plan["scenes"][0]["shots"]):
        shot["state_changes"] = []
        shot.pop("end_state")
        shot["continue_from_previous"] = index > 0
        if index:
            shot.pop("start_state")
    return plan


def test_continuous_states_are_derived_without_repeated_prose():
    draft = technical_plan()
    original = copy.deepcopy(draft)
    shots = draft["scenes"][0]["shots"]
    shots[0]["action"] = "Keeper opens the brass case."
    shots[0]["state_changes"] = [{
        "key": "case", "from": "Closed on the desk.", "to": "Open on the desk.",
        "action": shots[0]["action"],
    }]
    result = series_planner.normalize_plan(draft, {"entities": []})
    prepared = result["scenes"][0]["shots"]
    assert prepared[0]["end_state"]["case"] == "Open on the desk."
    assert prepared[1]["start_state"] == prepared[0]["end_state"]
    assert prepared[-1]["end_state"]["case"] == "Open on the desk."
    assert "end_state" not in original["scenes"][0]["shots"][0]
    assert "start_state" not in draft["scenes"][0]["shots"][1]


def test_short_native_story_is_flagged_for_review_without_padding():
    draft = technical_plan()
    original = copy.deepcopy(draft)
    draft["editorial_review"] = {"risks": "Vérifier la lisibilité du coffre."}
    context = {"entities": [], "target_seconds": 31.5}
    result = series_planner.normalize_plan(draft, context)
    shots = result["scenes"][0]["shots"]
    assert [shot["duration_sec"] for shot in shots] == [4] * 6
    assert "24 s" in result["editorial_review"]["risks"]
    assert "31.5 s" in result["editorial_review"]["risks"]
    assert "Vérifier la lisibilité du coffre." in result["editorial_review"]["risks"]
    again = series_planner.normalize_plan(copy.deepcopy(result), context)
    assert again["editorial_review"] == result["editorial_review"]
    assert original["scenes"] == draft["scenes"]


def test_native_story_that_exceeds_available_time_still_stops():
    with pytest.raises(ValueError, match="dépasse"):
        series_planner.normalize_plan(technical_plan(), {"entities": [], "target_seconds": 10})


@pytest.mark.asyncio
async def test_excessive_speech_is_shortened_before_technical_realization(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        result = creative_story()
        if len(requests) == 1:
            result["beats"][0]["dialogue_turns"][0]["text"] = " ".join(["Encore"] * 100)
        return result

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner._write_creative_story({"target_seconds": 31.5}, "Une fuite.", "")
    assert len(requests) == 2
    assert "parole" in requests[1][-1]["content"]
    assert "31.5" in requests[1][-1]["content"]
    assert result == creative_story()


def test_new_object_requires_an_explicit_initial_state_and_carries_old_objects():
    plan = technical_plan()
    plan["render_canon"]["props"]["fish"] = "A small red toy fish."
    shot = plan["scenes"][0]["shots"][1]
    shot.update(prop_keys=["fish"], start_state={"fish": "In Keeper's hand."})
    prepared = series_planner.normalize_plan(plan, {"entities": []})["scenes"][0]["shots"]
    assert prepared[1]["start_state"] == {
        "case": "Closed on the desk.", "fish": "In Keeper's hand."
    }
    assert prepared[2]["start_state"] == prepared[1]["end_state"]
    del shot["start_state"]
    with pytest.raises(ValueError, match="fish"):
        series_planner.normalize_plan(plan, {"entities": []})


@pytest.mark.parametrize("field,value", [
    ("from", "In Keeper's hand."), ("action", ""),
    ("action", "Keeper does something never described."), ("key", "unknown"),
])
def test_unmotivated_or_impossible_state_change_is_rejected(field, value):
    plan = technical_plan()
    shot = plan["scenes"][0]["shots"][1]
    shot["action"] = "Keeper opens the case."
    change = {"key": "case", "from": "Closed on the desk.",
              "to": "Open on the desk.", "action": shot["action"]}
    change[field] = value
    shot["state_changes"] = [change]
    with pytest.raises(ValueError, match="state_changes"):
        series_planner.normalize_plan(plan, {"entities": []})


@pytest.mark.parametrize("field", ["start_state", "end_state"])
def test_explicit_contradiction_is_rejected_instead_of_overwritten(field):
    plan = technical_plan()
    plan["scenes"][0]["shots"][1][field] = {"case": "On the floor."}
    with pytest.raises(ValueError, match=rf"scenes\[0\]\.shots\[1\]\.{field}"):
        series_planner.normalize_plan(plan, {"entities": []})


def test_independent_take_requires_a_motivated_cut_and_an_initial_state():
    plan = technical_plan()
    shot = plan["scenes"][0]["shots"][1]
    shot.update(continue_from_previous=False, cut_reason="Plus tard, après déplacement.",
                start_state={"case": "On the floor."})
    result = series_planner.normalize_plan(plan, {"entities": []})
    assert result["scenes"][0]["shots"][2]["start_state"] == {"case": "On the floor."}
    shot["cut_reason"] = ""
    with pytest.raises(ValueError, match="cut_reason"):
        series_planner.normalize_plan(plan, {"entities": []})


@pytest.mark.asyncio
async def test_creative_story_precedes_realization_and_is_preserved(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        return creative_story() if len(requests) == 1 else technical_plan()

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 2
    assert "CREATIVE STORY" in requests[0][0]["content"]
    assert "render_canon" not in requests[0][0]["content"]
    assert json.loads(requests[1][1]["content"])["creative_story"] == creative_story()
    assert result["creative_story"] == creative_story()
    assert sum(s.get("purpose") == "midroll" for scene in result["scenes"] for s in scene["shots"]) == 1


@pytest.mark.asyncio
async def test_realization_cannot_silently_drop_or_reassign_a_line(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        plan = technical_plan()
        if len(requests) == 2:
            plan["scenes"][0]["shots"][0]["dialogue"] = "A replacement line."
            return plan
        return {"values": {"field_1": "Line 0."}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 3
    assert "creative_story" in requests[2][0]["content"]
    assert result["scenes"][0]["shots"][0]["dialogue"] == "Line 0."


def test_computed_start_state_reaches_image_and_video_requests():
    plan = series_planner.normalize_plan(technical_plan(), {"entities": []})
    shot = plan["scenes"][0]["shots"][1]
    context = plan | {"language": "fr", "shot": shot}
    for prompt in (compile_image_request(shot, context)["prompt"], compile_h3_prompt(shot, context)):
        assert "case: Closed on the desk." in prompt
        assert prompt.count("case: Closed on the desk.") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("dialogue_turns", 1), ("prop_keys", [{}])])
async def test_malformed_realization_reaches_targeted_repair(monkeypatch, field, value):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        if len(requests) == 2:
            plan = technical_plan()
            plan["scenes"][0]["shots"][0][field] = value
            return plan
        return {"values": {"field_1": [] if field == "dialogue_turns" else ["case"]}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 3
    assert field in requests[2][-1]["content"]


@pytest.mark.asyncio
async def test_new_realization_cannot_opt_out_of_motivated_changes(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        if len(requests) == 2:
            return native_plan()
        from tests.test_studio_repair_values import closed_case_state_values
        return closed_case_state_values(messages)

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 8
    assert "changes" in requests[2][0]["content"]


@pytest.mark.asyncio
async def test_creative_speech_rejects_stage_directions_before_realization(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) <= 2:
            result = creative_story()
            if len(requests) == 1:
                result["beats"][0]["dialogue_turns"][0]["text"] = "(en criant) Line 0."
            return result
        return technical_plan()

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 3
    assert "creative_story" in requests[1][-1]["content"]
    assert result["creative_story"]["beats"][0]["dialogue_turns"][0]["text"] == "Line 0."


def test_change_can_reference_its_action_without_copying_the_prose():
    plan = technical_plan()
    shot = plan["scenes"][0]["shots"][1]
    shot["action"] = "Keeper opens the brass case."
    shot["state_changes"] = [{"key": "case", "from": "Closed on the desk.",
                              "to": "Open on the desk.", "action_ref": "action"}]
    prepared = series_planner.normalize_plan(plan, {"entities": []})
    assert prepared["scenes"][0]["shots"][2]["start_state"] == {"case": "Open on the desk."}


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("events", "bad"), ("summary", []), ("ongoing_plot", {}), ("title", 3)])
async def test_malformed_creative_metadata_is_repaired_before_insertion(monkeypatch, field, value):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) <= 2:
            result = creative_story()
            if len(requests) == 1:
                result[field] = value
            return result
        return technical_plan()

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 3
    assert field in requests[1][-1]["content"]
