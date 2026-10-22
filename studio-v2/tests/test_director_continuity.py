"""Contrôles locaux de continuité narrative : avertir sans bloquer la création."""

from __future__ import annotations

import json
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from core.config import V2Settings  # noqa: E402
from core.scenario import Character, Decor, DialogueLine, Project, Shot  # noqa: E402
from core.storyboard import continuity_warnings, prepare_storyboard  # noqa: E402
from llm.deepseek import generate_scenario_once  # noqa: E402


def _project(**overrides) -> Project:
    payload = {
        "id": "continuite",
        "idea": "Un citron renverse son bol de céréales puis tente de le rattraper.",
        "target_duration_s": 8.0,
        "characters": [Character(id="zeste", name="Zeste", description="Yellow lemon in a beige coat")],
        "decors": [Decor(id="cuisine", name="Cuisine", description="Kitchen counter under warm light")],
        "shots": [
            Shot(id="s1", duration_s=4, action="Zeste tips the cereal bowl forward.",
                 initial_state="Zeste holds a full cereal bowl above the counter.",
                 final_state="Zeste lies face down in the cereal bowl, milk on his coat.",
                 decor_id="cuisine"),
            Shot(id="s2", duration_s=4, action="Zeste lifts his head out of the bowl.",
                 initial_state="Zeste stands clean at the far end of the kitchen.",
                 final_state="Zeste wipes milk from his eyes.", decor_id="cuisine"),
        ],
    }
    payload.update(overrides)
    return Project.model_validate(payload)


def test_physical_reset_between_continuous_shots_is_warned():
    warnings = continuity_warnings(_project())
    assert any("s2" in warning and "état initial" in warning for warning in warnings)


def test_coherent_continuation_has_no_continuity_warning():
    project = _project()
    project.shots[1].initial_state = project.shots[0].final_state
    assert continuity_warnings(project) == []


def test_ellipsis_or_new_location_does_not_trigger_the_state_warning():
    project = _project()
    project.shots[1].time_transition = "ellipse"
    assert not any("état initial" in warning for warning in continuity_warnings(project))

    moved = _project(decors=[
        {"id": "cuisine", "name": "Cuisine", "description": "Kitchen"},
        {"id": "couloir", "name": "Couloir", "description": "Hallway"},
    ])
    moved.shots[1].decor_id = "couloir"
    assert not any("état initial" in warning for warning in continuity_warnings(moved))


def test_identical_action_in_two_shots_is_reported_as_duplicate():
    project = _project()
    project.shots[1].action = project.shots[0].action
    assert any("doublon" in warning for warning in continuity_warnings(project))


def test_silent_brief_with_dialogue_is_reported():
    project = _project(idea="Un gag muet, sans paroles, dans une cuisine.")
    project.shots[0].dialogue = [DialogueLine(speaker_id="zeste", text="Oups.")]
    assert any("sans paroles" in warning for warning in continuity_warnings(project))


def test_prepare_storyboard_exposes_warnings_without_blocking():
    project = _project()
    warnings = prepare_storyboard(project)
    assert any("état initial" in warning for warning in warnings)
    # Aucun plan n'est supprimé et le scénario reste exploitable.
    assert [shot.id for shot in project.shots] == ["s1", "s2"]


def test_generation_surfaces_continuity_warning_in_llm_meta():
    draft = {
        "title": "Céréales",
        "narration_mode": "dialogue",
        "visual_style": "stylized 3D cinema",
        "characters": [{"id": "zeste", "name": "Zeste", "description": "Yellow lemon in a beige coat",
                        "voice_hint": "dry"}],
        "decors": [{"id": "cuisine", "name": "Cuisine", "description": "Kitchen counter"}],
        "shots": [
            {"id": "s1", "duration_s": 4, "action": "Zeste tips the cereal bowl forward.",
             "initial_state": "Zeste holds a full bowl.", "final_state": "Milk spills on his coat.",
             "camera_setup": "medium shot", "decor_id": "cuisine", "dialogue": []},
            {"id": "s2", "duration_s": 4, "action": "Zeste lifts his head.",
             "initial_state": "Zeste stands clean across the room.", "final_state": "Zeste wipes milk.",
             "camera_setup": "medium shot", "decor_id": "cuisine", "dialogue": []},
        ],
    }
    project, meta = generate_scenario_once(
        settings=V2Settings(), idea="Un citron renverse ses céréales.", target_duration_s=8,
        transport_fn=lambda body: {"choices": [{"message": {"content": json.dumps(draft)}}]},
    )
    assert project.shots[1].initial_state == "Zeste stands clean across the room."
    assert any("état initial" in warning for warning in meta["warnings"])
