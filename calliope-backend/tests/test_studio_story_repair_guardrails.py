"""Focused, no-network regression checks for catastrophic dialogue loss."""

import pytest

from calliope.studio import series_planner
from tests.test_studio_story_preparation import creative_story, technical_plan


def test_missing_three_authored_lines_stops_without_fake_local_repair():
    plan = technical_plan()
    plan["scenes"][0]["shots"] = plan["scenes"][0]["shots"][:3]

    with pytest.raises(series_planner._UnrecoverableStoryRealizationError) as error:
        series_planner._validate_story_realization(creative_story(), plan)

    assert "3 répliques" in str(error.value)
    assert error.value.paths == []


def test_missing_one_authored_line_remains_eligible_for_targeted_repair():
    plan = technical_plan()
    plan["scenes"][0]["shots"] = plan["scenes"][0]["shots"][:-1]

    with pytest.raises(series_planner._StoryRealizationError) as error:
        series_planner._validate_story_realization(creative_story(), plan)

    assert type(error.value) is series_planner._StoryRealizationError
    assert error.value.paths == [["scenes", 0, "shots"]]


def test_three_unexpected_dialogue_lines_also_stop_whole_episode_repair():
    plan = technical_plan()
    plan["scenes"][0]["shots"] += plan["scenes"][0]["shots"][:3]

    with pytest.raises(series_planner._UnrecoverableStoryRealizationError) as error:
        series_planner._validate_story_realization(creative_story(), plan)

    assert error.value.paths == []
