from calliope.studio.h3_prompt import compile_h3_prompt
from calliope.studio.series_planner import FRENCH_MIDROLL, insert_midroll
from tests.test_studio_native_contract import native_plan


def prepared_midroll():
    context = {"entities": [], "language": "fr"}
    plan = insert_midroll(native_plan(), context)
    shot = next(shot for scene in plan["scenes"] for shot in scene["shots"]
                if shot.get("purpose") == "midroll")
    return plan | context, shot


def test_french_midroll_keeps_complete_offer_in_a_faster_take():
    _, shot = prepared_midroll()
    assert shot["dialogue"] == FRENCH_MIDROLL
    assert 4.4 <= shot["duration_sec"] <= 4.8
    assert shot["screen_text"] == []


def test_native_midroll_requests_fast_delivery_without_a_held_pose():
    context, shot = prepared_midroll()
    prompt = compile_h3_prompt(shot, context)
    assert f"<d>[French] {FRENCH_MIDROLL}</d>" in prompt
    assert "rapid but intelligible" in prompt
    assert "clear natural French delivery" not in prompt
    assert "holds the pointing gesture" not in prompt
    assert "held pose" in prompt
    assert "within about four seconds" not in prompt
