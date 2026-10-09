import copy

import pytest

from calliope.studio import series_planner
from tests.test_studio_planner_dialogue import manifest


def config():
    return {
        "entities": [],
        "directing_profile": {
            "attached_values": True,
            "require_reference_approval": True,
            "number_format": "plain",
        },
    }


def valued_plan():
    plan = manifest("You're kidding.")
    for entity, value in zip(plan["entities"], [1, 500]):
        entity["identity"] = {
            "visual_description": entity["description"],
            "attached_value": value,
            "label_key": entity["key"] + "_status",
        }
    return plan


def label(key, value, x):
    return {
        "key": key + "_status",
        "entity_key": key,
        "value": value,
        "anchor": "character",
        "anchor_box": [x - 0.1, 0.2, 0.2, 0.3],
        "x": x,
        "y": 0.12,
    }


def test_required_attached_values_cannot_silently_disappear_like_the_real_run():
    with pytest.raises(ValueError, match="screen_text.*mango.*blueberry"):
        series_planner.normalize_plan(valued_plan(), config())


def test_required_values_must_be_structured_instead_of_inferred_from_plot_prose():
    plan = valued_plan()
    for entity in plan["entities"]:
        entity["identity"].pop("attached_value")
    with pytest.raises(ValueError, match="attached_value"):
        series_planner.normalize_plan(plan, config())


def test_configured_character_value_requires_an_overlay_even_without_profile_toggle():
    with pytest.raises(ValueError, match="screen_text"):
        series_planner.normalize_plan(valued_plan(), {"entities": []})


def test_two_values_have_explicit_identity_and_exact_plain_digits():
    plan = valued_plan()
    plan["scenes"][0]["shots"][0]["screen_text"] = [
        label("mango", 1, 0.25),
        label("blueberry", 500, 0.75),
    ]
    result = series_planner.normalize_plan(plan, config())
    labels = result["scenes"][0]["shots"][0]["screen_text"]
    assert [(item["entity_key"], item["text"]) for item in labels] == [
        ("mango", "1"),
        ("blueberry", "500"),
    ]


def test_values_cannot_be_swapped_or_changed_without_a_structured_event():
    plan = valued_plan()
    plan["scenes"][0]["shots"][0]["screen_text"] = [
        label("mango", 500, 0.25),
        label("blueberry", 1, 0.75),
    ]
    with pytest.raises(ValueError, match="valeur.*mango"):
        series_planner.normalize_plan(plan, config())


def test_timed_change_updates_only_the_correct_identity_and_next_shot():
    plan = valued_plan()
    shot = plan["scenes"][0]["shots"][0]
    noah = label("mango", 1, 0.25) | {"event": "change", "changes": [{"at_sec": 1.2, "value": 2}]}
    leo = label("blueberry", 500, 0.75)
    shot["screen_text"] = [noah, leo]
    next_shot = copy.deepcopy(shot)
    next_shot["screen_text"] = [label("mango", 2, 0.25), leo]
    plan["scenes"][0]["shots"].append(next_shot)
    result = series_planner.normalize_plan(plan, config())
    assert result["display_values"] == {"mango_status": "2", "blueberry_status": "500"}


def test_existing_canon_identity_cannot_be_rewritten_by_a_new_episode():
    plan = valued_plan()
    original = copy.deepcopy(plan["entities"])
    plan["entities"][0]["identity"]["visual_description"] = "An unrelated human."
    context = config() | {"entities": original}
    shot = plan["scenes"][0]["shots"][0]
    shot["screen_text"] = [label("mango", 1, 0.25), label("blueberry", 500, 0.75)]
    result = series_planner.normalize_plan(plan, context)
    assert result["entities"][0]["identity"] == original[0]["identity"]


def test_generic_series_without_attached_values_keeps_existing_behavior():
    result = series_planner.normalize_plan(manifest("You're kidding."), {"entities": []})
    assert result["scenes"][0]["shots"][0]["screen_text"] == []


def test_strict_reference_profile_requires_a_location_canon_too():
    plan = manifest("You're kidding.")
    for entity in plan["entities"]:
        entity["identity"] = {"visual_description": entity["description"]}
    plan["entities"].append(
        {"key": "hall", "kind": "location", "name": "Hall", "description": "A hallway."}
    )
    with pytest.raises(ValueError, match="entities\\[hall\\].identity.visual_description"):
        series_planner.normalize_plan(
            plan, {"entities": [], "directing_profile": {"require_reference_approval": True}}
        )


def test_mandatory_outro_has_an_explicit_visual_identity_without_inventing_a_value():
    plan = valued_plan()
    shot = plan["scenes"][0]["shots"][0]
    shot["screen_text"] = [label("mango", 1, 0.25), label("blueberry", 500, 0.75)]
    plan = series_planner.append_outro(series_planner.normalize_plan(plan, config()), config())
    host = next(entity for entity in plan["entities"] if entity["key"] == series_planner.OUTRO_KEY)
    assert host["identity"]["visual_description"] == host["description"]
    assert host["identity"].get("attached_value") is None
    from calliope.studio.visual_identity import validate_value_manifest

    validate_value_manifest(
        plan["scenes"][-1]["shots"][0], config() | {"entities": plan["entities"]}
    )


def test_one_as_value_does_not_corrupt_age_or_character_identity_in_visual_prose():
    plan = valued_plan()
    shot = plan["scenes"][0]["shots"][0]
    shot["screen_text"] = [label("mango", 1, 0.25), label("blueberry", 500, 0.75)]
    shot["image_prompt"] = (
        "Mango is 17 years old. Student 1 listens. A glowing 500 floats above the forehead."
    )
    result = series_planner.normalize_plan(plan, config())
    compiled = result["scenes"][0]["shots"][0]["image_prompt"]
    assert "17 years old" in compiled
    assert "Student 1 listens" in compiled
    assert "500 floats" not in compiled
