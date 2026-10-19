import pytest

from calliope.studio.h3_prompt import compile_video_prompt
from calliope.studio.ltx_prompt import (
    LTX_PROMPT_VERSION,
    compile_ltx_prompt,
    lint_ltx_prompt,
)


def sample():
    shot = {
        "prompt": "A kitchen. A man raises a hand.",
        "dialogue": "Bonjour.",
        "speaker": "lila",
        "audio_mode": "native",
        "action": "A man raises a hand.",
        "framing": "A medium shot.",
        "camera": "Static camera.",
        "soundscape": "Room tone.",
    }
    context = {
        "language": "fr",
        "entities": [{"key": "lila", "name": "Lila", "description": "A woman in blue."}],
        "render_canon": {
            "characters": {
                "lila": {
                    "name": "Lila",
                    "appearance": "A woman in a blue coat.",
                    "voice": "Une voix grave et rauque.",
                }
            }
        },
    }
    return shot, context


def test_ltx_prompt_uses_quotes_without_h3_tags_or_timings():
    shot, context = sample()
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert '"Bonjour."' in prompt
    for forbidden in (
        "<d>", "</d>", "[French]", "SPOKEN LINES", "Visual timing",
        "not spoken", "spoken between", "At 2 seconds", "positions:", "eyelines:",
    ):
        assert forbidden not in prompt
    assert len(prompt.split()) <= 200
    assert LTX_PROMPT_VERSION == 3


def test_ltx_prompt_deduplicates_the_action():
    shot, context = sample()
    shot["prompt"] = "A man raises a hand. A man raises a hand."
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert prompt.casefold().count("a man raises a hand") == 1


def test_ltx_prompt_keeps_a_silent_take_silent():
    shot, context = sample()
    shot["audio_mode"] = "silent"
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "Bonjour" not in prompt
    assert "The characters remain silent" in prompt


def test_ltx_lint_rejects_h3_tags_and_absolute_timings():
    with pytest.raises(ValueError, match="balises H3"):
        lint_ltx_prompt('A shot. <d>[French] Bonjour.</d>')
    with pytest.raises(ValueError, match="timing absolu"):
        lint_ltx_prompt(
            "A shot. The following line is spoken between 1 and 2 seconds."
        )
    with pytest.raises(ValueError, match="trop long"):
        lint_ltx_prompt("A shot. " + "word " * 250)


def test_compile_video_prompt_routes_ltx_to_v3_and_keeps_h3_tags_for_fast():
    shot, context = sample()
    ltx = compile_video_prompt(shot, context, first_frame=False, video_profile="ltx")
    assert "<d>" not in ltx and "spoken between" not in ltx
    assert '"Bonjour."' in ltx
    fast = compile_video_prompt(shot, context, first_frame=False, video_profile="fast")
    assert "<d>" in fast


def test_ltx_prompt_carries_momo_lower_voice_without_confusing_durio():
    shot, context = sample()
    context["render_canon"]["characters"]["lila"]["voice"] = (
        "Masculine fran\u00e7aise adulte, l\u00e9g\u00e8rement plus grave que la voix "
        "native valid\u00e9e, timbre pos\u00e9, calme, moins enfantine."
    )
    momo_prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "slightly lower, more mature French voice" in momo_prompt

    context["render_canon"]["characters"]["lila"]["voice"] = (
        "Masculine fran\u00e7aise, tr\u00e8s grave, rauque, puissante, diction agressive."
    )
    durio_prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "low, raspy French voice" in durio_prompt
    assert "slightly lower, more mature French voice" not in durio_prompt


def test_ltx_prompt_keeps_a_medium_low_voice_out_of_raspy():
    shot, context = sample()
    context["render_canon"]["characters"]["lila"]["voice"] = (
        "Masculine fran\u00e7aise, m\u00e9dium-grave, pos\u00e9e, froide."
    )
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "warm, medium-low French voice" in prompt
    assert "low, raspy French voice" not in prompt
