import copy

import pytest

from calliope.studio import production, series_planner, service
from calliope.studio.h3_prompt import compile_h3_prompt
from calliope.studio.visual_identity import compile_image_request
from tests.test_studio_series import episode, planned_episode, series
from tests.test_studio_series import isolated_production as isolated_production


def office_context():
    return {
        "language": "fr",
        "entities": [
            {"key": "host", "name": "Host", "kind": "character", "description": "A lemon."},
            {"key": "guest", "name": "Guest", "kind": "character", "description": "A banana."},
            {"key": "office", "name": "Office", "kind": "location", "description": "Dark desk."},
        ],
        "bible": {
            "visual_style": "Close portraits in cinematic 3D.",
            "locations": "Host behind the desk; Guest seated across it.",
            "world_rules": "All inhabitants are fruits, never humans.",
            "continuity": "The window stays behind Host; maintain the conversation axis.",
        },
        "shot": {
            "character_keys": ["host", "guest"],
            "location_key": "office",
            "blocking": "Host on the left, Guest on the right, facing each other across the desk.",
            "framing": "Shared three-quarter view of both faces and the desk.",
            "camera": "Static side camera, slightly above eye level.",
        },
    }


def test_still_and_video_requests_preserve_layout_and_world_rules():
    context = office_context()
    shot = {
        "image_prompt": "Two fruits exchange a look.", "prompt": "Two fruits exchange a look.",
        "dialogue": "Une offre.", "speaker": "host",
    }
    original = copy.deepcopy(context)
    image = compile_image_request(shot, context)["prompt"]
    video = compile_h3_prompt(shot | context["shot"], context)
    for prompt in (image, video):
        for field in ("locations", "world_rules", "continuity"):
            assert context["bible"][field] in prompt
        for field in ("blocking", "framing", "camera"):
            assert context["shot"][field] in prompt
        assert "Do not add extra characters" in prompt
    assert context == original


def test_canonical_reference_uses_world_rules_without_inventing_a_scene():
    context = office_context()
    request = compile_image_request({}, context, canonical_entity=context["entities"][0])
    assert context["bible"]["world_rules"] in request["prompt"]
    assert context["bible"]["locations"] not in request["prompt"]
    assert context["shot"]["blocking"] not in request["prompt"]


def test_scene_blocking_survives_normalization_and_storage(client):
    created = series(client)
    project = episode(client, created["id"]).json()["project_id"]
    plan = planned_episode({})
    blocking = "Keeper stays behind the radio desk, facing the door."
    plan["scenes"][0]["blocking"] = blocking
    normalized = series_planner.normalize_plan(plan, {"entities": []})
    assert all(s["blocking"] == blocking for s in normalized["scenes"][0]["shots"])
    production.save_episode_plan(project, normalized)
    shot = service.board(project)["scenes"][0]["shots"][0]
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
    assert context["shot"]["blocking"] == blocking


@pytest.mark.parametrize("preset,expected", [
    ("fast_dialogue_social_fiction", "silent"), ("generalist", "native"),
])
def test_social_fiction_reactions_do_not_keep_unverified_native_voices(preset, expected):
    plan = planned_episode({})
    plan["scenes"][0]["shots"][1]["audio_mode"] = "native"
    context = {"entities": [], "directing_profile": {"preset": preset}}
    result = series_planner.normalize_plan(plan, context)
    assert result["scenes"][0]["shots"][0]["audio_mode"] == "native"
    assert result["scenes"][0]["shots"][1]["audio_mode"] == expected


def test_spatial_edits_invalidate_an_existing_visual_approval():
    context = office_context()
    shot = {"screen_text": []}
    approved = service.visual_review_contract(context, shot)
    context["shot"]["blocking"] = "Guest now stands behind Host."
    assert service.visual_review_contract(context, shot) != approved


def test_midroll_changes_gaze_without_resetting_the_scene():
    context = office_context()
    shot = context["shot"] | {"purpose": "midroll", "dialogue": "Abonne-toi.", "speaker": "host"}
    prompt = compile_h3_prompt(shot, context)
    assert "temporary gaze overrides the narrative eyelines" in prompt
    assert "They look at their interlocutors, not into the camera" not in prompt
    assert context["shot"]["blocking"] in prompt
