import copy
import json

import pytest

from calliope.studio import series_planner
from tests.test_studio_story_preparation import creative_story, direct_story_writer, technical_plan


def split_scene_plan():
    plan = technical_plan()
    scene = plan["scenes"][0]
    plan["scenes"] = [scene | {"shots": [shot]} for shot in scene["shots"]]
    return plan


def closed_case_state_values(messages):
    request = json.loads(messages[-1]["content"])
    if "required_initial_keys" in request:
        return {"initial": {"case": "Closed on the desk."} if request["required_initial_keys"] else {},
                "changes": [], "new_props": {}}
    fields = json.loads(messages[-1]["content"])["repair_fields"]
    return {"values": {field["id"]: [] if field["path"][-1] == "state_changes"
                       else {"case": "Closed on the desk."} for field in fields}}


@pytest.mark.asyncio
async def test_state_compiler_receives_still_for_independent_takes_only(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(json.loads(messages[-1]["content"]))
        return closed_case_state_values(messages)

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    draft = technical_plan()
    await series_planner.prepare_native_states(draft)
    assert requests[0]["initial_image"] == draft["scenes"][0]["shots"][0]["image_prompt"]
    assert "initial_image" not in requests[1]
    assert requests[1]["inherited_state"] == {"case": "Closed on the desk."}


@pytest.mark.parametrize("bad_field,bad_value", [("render_canon", None), ("dialogue_turns", 1)])
def test_structural_defects_are_reported_before_missing_states(bad_field, bad_value):
    draft = split_scene_plan()
    draft["scenes"][0]["shots"][0]["start_state"] = {}
    if bad_field == "render_canon":
        draft[bad_field] = bad_value
    else:
        draft["scenes"][0]["shots"][0][bad_field] = bad_value
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(draft, {"entities": []})
    assert error.value.paths == ([[bad_field]] if bad_field == "render_canon" else [["scenes", 0, "shots", 0, bad_field]])


@pytest.mark.parametrize("entry", ["Keeper in a coat.", {"appearance": "A keeper.", "voice": "Warm."}])
def test_character_identity_defects_are_reported_before_missing_states(entry):
    draft = split_scene_plan()
    draft["scenes"][0]["shots"][0]["start_state"] = {}
    draft["render_canon"]["characters"]["keeper"] = entry
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(draft, {"entities": []})
    assert error.value.paths == [["render_canon", "characters", "keeper"]]


@pytest.mark.parametrize("scene,path", [
    ({"heading": "Broken"}, ["scenes", 0]),
    ({"shots": [None]}, ["scenes", 0, "shots", 0]),
])
def test_scene_structure_defects_are_not_hidden_by_later_missing_states(scene, path):
    draft = split_scene_plan()
    draft["scenes"][0] = scene
    draft["scenes"][1]["shots"][0].update(continue_from_previous=False, start_state={})
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(draft, {"entities": []})
    assert error.value.paths == [path]


def test_location_declared_in_visual_canon_requests_its_missing_entity():
    draft = technical_plan()
    draft["render_canon"]["locations"]["new_room"] = "A round white room."
    for shot in draft["scenes"][0]["shots"]:
        shot["location_key"] = "new_room"
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(draft, {"entities": []})
    fields = series_planner._native_repair_fields(draft, error.value.paths)
    assert [field["path"] for field in fields] == [["entities"]]


@pytest.mark.asyncio
async def test_state_compiler_declares_new_plot_object_and_computes_from(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    draft["scenes"][0]["shots"][0]["action"] = "Keeper opens the fridge and takes the case out."

    async def generate(*args, **kwargs):
        return {"initial": {"case": "Inside the fridge.", "fridge": "Closed and powered."},
                "new_props": {"fridge": "A tall white refrigerator."},
                "changes": [{"key": "fridge", "to": "Open and powered.", "action_ref": "action"},
                            {"key": "case", "to": "In Keeper's hand.", "action_ref": "action"}]}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    shot = result["scenes"][0]["shots"][0]
    assert result["render_canon"]["props"]["fridge"] == "A tall white refrigerator."
    assert shot["prop_keys"] == ["case", "fridge"]
    assert shot["state_changes"][0]["from"] == "Closed and powered."
    assert shot["end_state"] == {"case": "In Keeper's hand.", "fridge": "Open and powered."}


@pytest.mark.asyncio
async def test_state_compiler_keeps_known_prop_identity_and_handles_context_entities(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    del draft["entities"]
    original = copy.deepcopy(draft)

    async def generate(*args, **kwargs):
        return {"initial": {"case": "Closed on the desk."}, "changes": [],
                "new_props": {"case": "A different pink plastic suitcase."}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    assert result["render_canon"] == original["render_canon"]
    assert result["scenes"][0]["shots"][0]["end_state"] == {"case": "Closed on the desk."}
    assert draft == original


@pytest.mark.asyncio
async def test_missing_new_object_repair_keeps_valid_manifest_and_exact_action_reference(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    draft["scenes"][0]["shots"][0]["action"] = "Keeper opens the fridge."
    calls = 0

    async def generate(messages, **options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"initial": {"case": "Closed on the desk."}, "changes": [],
                    "new_props": {"fridge": "A tall white refrigerator."}}
        request = json.loads(messages[-1]["content"])
        assert request["required_initial_keys"] == ["case", "fridge"]
        assert request["prop_descriptions"]["fridge"] == "A tall white refrigerator."
        return {"initial": {"case": "Closed on the desk.", "fridge": "Closed and powered."},
                "changes": [{"key": "fridge", "to": "Open and powered.", "action_ref": "Keeper opens the fridge."}]}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    shot = result["scenes"][0]["shots"][0]
    assert shot["end_state"]["fridge"] == "Open and powered."
    assert shot["state_changes"][0]["action_ref"] == "action"
    assert "fridge" not in draft["render_canon"]["props"]


@pytest.mark.asyncio
async def test_state_compiler_orders_changes_by_existing_actions_before_computing_from(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    shot = draft["scenes"][0]["shots"][0]
    shot.update(action="Keeper opens the case.", reaction="Keeper closes the case.")

    async def generate(*args, **kwargs):
        return {"initial": {"case": "Closed on the desk."}, "changes": [
            {"key": "case", "to": "Closed on the desk.", "action_ref": "reaction"},
            {"key": "case", "to": "Open on the desk.", "action_ref": "action"}]}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    changes = result["scenes"][0]["shots"][0]["state_changes"]
    assert [change["action_ref"] for change in changes] == ["action", "reaction"]
    assert changes[1]["from"] == "Open on the desk."
    assert result["scenes"][0]["shots"][0]["end_state"]["case"] == "Closed on the desk."


@pytest.mark.asyncio
async def test_state_compiler_feedback_names_undeclared_initial_objects(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    calls = 0

    async def generate(messages, **options):
        nonlocal calls
        calls += 1
        request = json.loads(messages[-1]["content"])
        assert "repair_fields" not in request
        assert "dialogue_turns" not in request["take"]
        assert options["json_mode"] is True
        if calls == 1:
            return {"initial": {"case": "Closed on the desk.", "fridge": "Open."}, "changes": []}
        assert "fridge" in request["validation_error"]
        assert "new_props" in request["validation_error"]
        return {"initial": {"case": "Closed on the desk."}, "changes": []}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    assert result["scenes"][0]["shots"][0]["end_state"] == {"case": "Closed on the desk."}


@pytest.mark.parametrize("reference", [[], {}])
@pytest.mark.asyncio
async def test_state_compiler_rejects_nontext_action_reference_without_crashing(monkeypatch, reference):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]

    async def generate(*args, **kwargs):
        return {"initial": {"case": "Closed on the desk."}, "changes": [
            {"key": "case", "to": "Open on the desk.", "action_ref": reference}]}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError, match="action_ref"):
        await series_planner.prepare_native_states(draft)


@pytest.mark.asyncio
async def test_state_compiler_reports_all_bad_values_in_one_local_repair(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    calls = 0

    async def generate(messages, **options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"initial": {"case": "Closed on the desk.", "keeper_state": "Standing."}, "changes": [
                {"key": "invented_object", "to": "Open.", "action_ref": "invented_event"}]}
        error = json.loads(messages[-1]["content"])["validation_error"]
        assert all(value in error for value in ("keeper_state", "invented_object", "invented_event"))
        return {"initial": {"case": "Closed on the desk."}, "changes": []}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    assert result["scenes"][0]["shots"][0]["end_state"] == {"case": "Closed on the desk."}


@pytest.mark.asyncio
async def test_state_compiler_resolves_unique_literal_sentence_inside_existing_action(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:1]
    draft["scenes"][0]["shots"][0]["action"] = "Keeper steps forward. Keeper opens the case."

    async def generate(*args, **options):
        return {"initial": {"case": "Closed on the desk."}, "changes": [
            {"key": "case", "to": "Open on the desk.", "action_ref": "Keeper opens the case."}]}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    assert result["scenes"][0]["shots"][0]["state_changes"][0]["action_ref"] == "action"


@pytest.mark.asyncio
async def test_state_preparation_does_not_consume_targeted_story_repair_attempts(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"][0]["shots"][0]["start_state"] = {}
    draft["render_canon"]["style"] = ""
    last = draft["scenes"][-1]["shots"][0]
    last["dialogue_turns"] = [
        {"speaker": "keeper", "text": "Line 5."},
        {"speaker": "keeper", "text": "", "action": "Keeper closes the window."}]
    requests = []

    async def generate(messages, **options):
        request = json.loads(messages[-1]["content"])
        requests.append(request)
        if len(requests) == 1:
            return creative_story()
        if len(requests) == 2:
            return draft
        if "required_initial_keys" in request:
            return closed_case_state_values(messages)
        values = {}
        for field in request["repair_fields"]:
            name = field["path"][-1]
            if name == "style":
                values[field["id"]] = "Ink animation."
            elif name == "dialogue_turns":
                values[field["id"]] = [{"speaker": "keeper", "text": "Line 5."}]
            elif name == "action":
                values[field["id"]] = last.get("action", "")
            elif name == "reaction":
                values[field["id"]] = "Keeper closes the window."
            else:
                pytest.fail(f"Unexpected repair: {field}")
        return {"values": values}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "")
    narrative = [shot for scene in result["scenes"] for shot in scene["shots"] if shot.get("purpose") != "midroll"]
    assert narrative[-1]["dialogue"] == "Line 5."
    assert narrative[-1]["reaction"] == "Keeper closes the window."
    assert [request["repair_fields"][0]["path"][-1] for request in requests if "repair_fields" in request] == [
        "style", "dialogue_turns"]


def test_missing_state_exposes_software_owned_target():
    plan = split_scene_plan()
    plan["scenes"][0]["shots"][0]["start_state"] = {}
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [["scenes", 0, "shots", 0, "start_state", "case"]]


def test_all_missing_initial_states_in_a_shot_are_requested_together():
    plan = split_scene_plan()
    plan["render_canon"]["props"]["stool"] = "A small folding stool."
    plan["scenes"][0]["shots"][0].update(prop_keys=["case", "stool"], start_state={})
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [
        ["scenes", 0, "shots", 0, "start_state", "case"],
        ["scenes", 0, "shots", 0, "start_state", "stool"],
    ]


def test_missing_initial_states_across_all_scenes_are_reported_once_per_chain():
    plan = split_scene_plan()
    plan["render_canon"]["props"].update(cup="A glass cup.", lamp="A brass lamp.")
    shots = [scene["shots"][0] for scene in plan["scenes"]]
    shots[0]["start_state"] = {}
    shots[2]["prop_keys"] = ["cup"]
    shots[4]["prop_keys"] = ["lamp", "cup"]
    original = copy.deepcopy(plan)
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [
        ["scenes", 0, "shots", 0, "start_state", "case"],
        ["scenes", 2, "shots", 0, "start_state", "cup"],
        ["scenes", 4, "shots", 0, "start_state", "lamp"],
    ]
    assert plan == original


def test_missing_states_for_an_independent_take_are_not_treated_as_inherited():
    plan = split_scene_plan()
    plan["scenes"][0]["shots"][0]["start_state"] = {}
    plan["scenes"][3]["shots"][0].update(continue_from_previous=False, cut_reason="Plus tard.")
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [
        ["scenes", 0, "shots", 0, "start_state", "case"],
        ["scenes", 3, "shots", 0, "start_state", "case"],
    ]


@pytest.mark.asyncio
async def test_state_pass_repairs_whole_chronology_without_rewriting_story(monkeypatch):
    draft = split_scene_plan()
    shots = [scene["shots"][0] for scene in draft["scenes"]]
    shots[0]["start_state"] = {}
    shots[1]["action"] = "Keeper opens the brass case."
    shots[3].update(start_state={"case": "Open on the desk."})
    original = copy.deepcopy(draft)
    calls = 0

    async def generate(messages, **options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return creative_story()
        if calls == 2:
            return draft
        si = json.loads(messages[-1]["content"])["take_index"][0]
        return {"initial": {"case": "Closed on the desk."} if si == 0 else {},
                "changes": [{"key": "case", "to": "Open on the desk.", "action_ref": "action"}] if si == 1 else [],
                "new_props": {}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    narrative = [shot for scene in result["scenes"] for shot in scene["shots"] if shot.get("purpose") != "midroll"]
    assert narrative[-1]["start_state"] == {"case": "Open on the desk."}
    assert [shot["dialogue"] for shot in narrative] == [f"Line {index}." for index in range(6)]
    assert narrative[1]["action"] == "Keeper opens the brass case."
    assert draft == original
    assert calls == 8


def test_undeclared_prop_is_repaired_before_requesting_its_initial_state():
    plan = split_scene_plan()
    plan["scenes"][0]["shots"][0]["prop_keys"] = ["unknown"]
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [["scenes", 0, "shots", 0, "prop_keys"]]


@pytest.mark.asyncio
async def test_state_compiler_transmits_validated_end_and_preserves_nonstate_fields(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:2]
    first, second = [scene["shots"][0] for scene in draft["scenes"]]
    first.update(action="Keeper opens the brass case.", end_state={"case": "Stale state."})
    second.update(start_state={"case": "Wrong state."}, end_state={"case": "Stale state."})
    original = copy.deepcopy(draft)
    requests = []

    async def generate(messages, **options):
        request = json.loads(messages[-1]["content"])
        requests.append(request)
        if len(requests) == 1:
            assert request["required_initial_keys"] == ["case"]
            return {"initial": {"case": "Closed on the desk."}, "changes": [
                {"key": "case", "to": "Open on the desk.", "action_ref": "action"}], "new_props": {}}
        assert request["inherited_state"] == {"case": "Open on the desk."}
        assert request["required_initial_keys"] == []
        return {"initial": {"case": "Wrong state."}, "changes": [], "new_props": {}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.prepare_native_states(draft)
    assert result["scenes"][1]["shots"][0]["start_state"] == {"case": "Open on the desk."}
    assert result["scenes"][1]["shots"][0]["end_state"] == {"case": "Open on the desk."}
    for si in range(2):
        assert {k:v for k,v in result["scenes"][si]["shots"][0].items() if k not in ("start_state", "end_state", "state_changes")} == {
            k:v for k,v in original["scenes"][si]["shots"][0].items() if k not in ("start_state", "end_state", "state_changes")}
    assert draft == original


@pytest.mark.asyncio
async def test_state_compiler_retries_only_failed_take_and_does_not_mutate_input(monkeypatch):
    draft = split_scene_plan()
    draft["scenes"] = draft["scenes"][:2]
    original = copy.deepcopy(draft)
    requests = []

    async def generate(messages, **options):
        request = json.loads(messages[-1]["content"])
        requests.append(request)
        if len(requests) == 1:
            return {"initial": {"case": "Closed on the desk."}, "changes": [], "new_props": {}}
        return {"initial": {}, "changes": [{"key": "case", "to": "Open on the desk.", "action_ref": "shot"}], "new_props": {}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError, match="state_changes"):
        await series_planner.prepare_native_states(draft)
    assert len(requests) == 3
    assert requests[1]["take_index"] == requests[2]["take_index"] == [1, 0]
    assert "validation_error" in requests[2]
    assert draft == original


@pytest.mark.asyncio
async def test_production_compiles_even_structurally_valid_new_states_before_saving(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import production, series, service

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(series, "require_services", lambda **kwargs: None)
    series_id = client.post("/api/studio/series", json={"title": "Tower", "concept": "A keeper.",
                          "language": "fr", "target_seconds": 32}).json()["id"]
    run = series.create_episode(series_id, {"idea": "An opening.", "request_id": "compile-states", "prepare_only": True})
    calls = []

    async def plan(*args):
        draft = technical_plan()
        draft["scenes"][0]["shots"][0]["action"] = "Keeper opens the brass case."
        return series_planner.insert_midroll(series_planner.normalize_plan(draft, {"entities": []}),
                                            {"target_seconds": 32.5, "language": "fr"})

    async def generate(messages, **options):
        calls.append(messages)
        if len(calls) == 1:
            return {"initial": {"case": "Closed on the desk."}, "changes": [
                {"key": "case", "to": "Open on the desk.", "action_ref": "action"}], "new_props": {}}
        return {"initial": {}, "changes": [], "new_props": {}}

    async def media(*args):
        pytest.fail("Preparation must not generate media.")

    monkeypatch.setattr(production, "plan_episode", plan)
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    monkeypatch.setattr(production, "ensure_entities", media)
    job = queue_manager.get_job(run["job_id"])
    payload = json.loads(job["payload_json"])
    await production.run_production(job, payload, lambda: False)
    await production.run_production(job, payload, lambda: False)
    board = service.board(run["project_id"])
    assert board["production"]["stage"] == "review"
    narrative = [shot for scene in board["scenes"] for shot in scene["shots"] if shot.get("purpose") != "midroll"]
    assert narrative[-1]["end_state"] == {"case": "Open on the desk."}
    assert len(calls) == 6
    assert all(not shot["takes"] for scene in board["scenes"] for shot in scene["shots"])


def test_legacy_first_shot_continuation_remains_a_local_validation_error():
    from tests.test_studio_native_contract import native_plan

    plan = native_plan()
    plan["scenes"][0]["shots"][0]["continue_from_previous"] = True
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [["scenes", 0, "shots", 0, "continue_from_previous"]]


def test_malformed_legacy_end_state_remains_a_validation_error():
    from tests.test_studio_native_contract import native_plan

    plan = native_plan()
    plan["scenes"][0]["shots"][0]["end_state"] = 1
    with pytest.raises(ValueError, match="end_state"):
        series_planner.normalize_plan(plan, {"entities": []})


def test_values_apply_only_to_software_targets_without_changing_the_draft():
    plan = split_scene_plan()
    original = copy.deepcopy(plan)
    paths = [["scenes", 4, "shots", 0, "start_state"]]
    value = {"case": "Closed on the desk."}
    result = series_planner._apply_native_values(plan, paths, {"values": {"field_1": value}})
    expected = copy.deepcopy(original)
    expected["scenes"][4]["shots"][0]["start_state"] = value
    assert result == expected
    assert plan == original


@pytest.mark.parametrize("response", [
    {"values": {}},
    {"values": {"field_1": {}, "field_2": {}}},
    {"values": [{"path": ["scenes", 99], "value": {}}]},
    {"values": {"field_1": {}}, "updates": []},
])
def test_foreign_or_incomplete_repair_values_are_rejected_atomically(response):
    plan = split_scene_plan()
    original = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="values"):
        series_planner._apply_native_values(plan, [["scenes", 0, "shots", 0, "start_state"]], response)
    assert plan == original


def test_target_uses_the_missing_parent_field_instead_of_an_invalid_path():
    plan = split_scene_plan()
    del plan["scenes"][0]["shots"][0]["start_state"]
    paths = [["scenes", 0, "shots", 0, "start_state", "case"]]
    fields = series_planner._native_repair_fields(plan, paths)
    assert fields == [{"id": "field_1", "path": paths[0][:-1], "present": False, "current_value": None}]


@pytest.mark.parametrize("value", [None, [], "bad"])
def test_malformed_canon_targets_its_root_instead_of_a_child(value):
    plan = split_scene_plan()
    replacement = copy.deepcopy(plan["render_canon"])
    plan["render_canon"] = value
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    fields = series_planner._native_repair_fields(plan, error.value.paths)
    assert fields[0]["path"] == ["render_canon"]
    repaired = series_planner._apply_native_values(plan, [fields[0]["path"]], {"values": {"field_1": replacement}})
    series_planner.normalize_plan(repaired, {"entities": []})


@pytest.mark.parametrize("invalid_key", [True, False])
def test_invalid_prop_key_targets_dictionary_but_invalid_description_targets_value(invalid_key):
    plan = split_scene_plan()
    if invalid_key:
        plan["render_canon"]["props"]["bad key"] = "A prop."
    else:
        plan["render_canon"]["props"]["case"] = ""
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    path = ["render_canon", "props"] + ([] if invalid_key else ["case"])
    assert error.value.paths == [path]
    value = {"case": "A brass case."} if invalid_key else "A brass case."
    repaired = series_planner._apply_native_values(plan, [path], {"values": {"field_1": value}})
    series_planner.normalize_plan(repaired, {"entities": []})


def test_all_invalid_prop_descriptions_are_reported_before_state_compilation():
    plan = technical_plan()
    plan["render_canon"]["props"] = {
        "case": {"name": "Case", "description": "A brass case."},
        "fridge": {"name": "Fridge", "description": "A white fridge."},
        "cord": None,
    }
    plan["scenes"][0]["shots"][0]["start_state"] = {}
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert error.value.paths == [["render_canon", "props", key] for key in ("case", "fridge", "cord")]
    assert "texte anglais" in str(error.value)


@pytest.mark.asyncio
async def test_object_descriptions_are_repaired_together_before_state_calls(monkeypatch):
    requests = []
    plan = technical_plan()
    for shot in plan["scenes"][0]["shots"]:
        shot.pop("state_changes")
    plan["render_canon"]["props"] = {
        "case": {"name": "Case", "description": "A brass case."},
        "fridge": {"name": "Fridge", "description": "A white fridge."},
    }

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        if len(requests) == 2:
            return copy.deepcopy(plan)
        if len(requests) == 3:
            fields = json.loads(messages[-1]["content"])["repair_fields"]
            assert [field["path"] for field in fields] == [
                ["render_canon", "props", "case"], ["render_canon", "props", "fridge"]]
            return {"values": {"field_1": "A brass case.", "field_2": "A white fridge."}}
        return closed_case_state_values(messages)

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 9
    assert result["render_canon"]["props"] == {"case": "A brass case.", "fridge": "A white fridge."}


@pytest.mark.asyncio
async def test_real_failure_shape_repairs_the_first_shot_not_a_global_shot_index(monkeypatch):
    requests = []
    draft = split_scene_plan()
    draft["scenes"][0]["shots"][0]["start_state"] = {}
    original = copy.deepcopy(draft)

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        if len(requests) == 2:
            return draft
        assert options["required_keys"] == ("initial", "changes")
        repair = json.loads(messages[-1]["content"])
        assert repair["take_index"] == [len(requests) - 3, 0]
        assert '"path"' not in messages[0]["content"]
        return closed_case_state_values(messages)

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 8
    assert draft == original
    assert result["scenes"][0]["shots"][0]["start_state"]["case"] == "Closed on the desk."


@pytest.mark.asyncio
async def test_invalid_value_response_retries_the_same_targets_and_unchanged_draft(monkeypatch):
    requests = []
    draft = split_scene_plan()
    draft["scenes"][0]["shots"][0]["start_state"] = {}

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        if len(requests) == 2:
            return draft
        if len(requests) == 3:
            return closed_case_state_values(messages) | {"updates": []}
        if len(requests) == 4:
            assert json.loads(messages[-1]["content"])["take_index"] == [0, 0]
            assert "uniquement" in json.loads(messages[-1]["content"])["validation_error"]
        assert "initial" in messages[0]["content"]
        return closed_case_state_values(messages)

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 9


@pytest.mark.asyncio
async def test_provider_format_error_keeps_draft_and_records_diagnostics_before_retry(monkeypatch, tmp_path):
    from calliope.config import settings

    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    calls = 0
    draft = split_scene_plan()
    draft["scenes"][0]["shots"][0]["start_state"] = {}

    async def generate(messages, **options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return creative_story()
        if calls == 2:
            return draft
        if calls == 3:
            raise ValueError("Structured reply missing root fields: values")
        return closed_case_state_values(messages)

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr",
         "_planner_trace": {"project_id": 9, "job_id": 1}}, "Une fuite.", "",
    )
    assert result["scenes"][0]["shots"][0]["start_state"] == {"case": "Closed on the desk."}
    records = [json.loads(file.read_text(encoding="utf-8")) for file in (tmp_path / "9/studio/diagnostics").glob("planner-*-state-0-0-attempt-1.json")]
    assert len(records) == 1
    assert records[0]["validation_error"] == "Structured reply missing root fields: values"
    assert json.loads(records[0]["messages"][-1]["content"])["take_index"] == [0, 0]
    assert records[0]["response"] is None


@pytest.mark.asyncio
async def test_unlocated_error_stops_without_arbitrary_whole_episode_repair(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return creative_story()
        draft = split_scene_plan()
        draft["scenes"] = draft["scenes"][:1]
        return draft

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError):
        await series_planner.plan_episode(
            {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
            "Une fuite.", "",
        )
    assert len(requests) == 2
