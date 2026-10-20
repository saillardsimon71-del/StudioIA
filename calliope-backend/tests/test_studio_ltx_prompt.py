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


def test_ltx_prompt_keeps_critical_constraints_when_trimming_optional_prose():
    shot, context = sample()
    shot["soundscape"] = "quiet room tone"
    context['directing_profile'] = {'dialogue_density': 'brisk', 'silences': 'brief'}
    context['render_canon']['characters']['lila']['appearance'] = 'A long optional appearance ' * 40
    shot["reaction"] = "A long optional reaction " * 20
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "Only the named speaker speaks" in prompt
    assert "Speak each quoted line exactly once" in prompt
    assert "The voice stays in the foreground" in prompt
    assert "Ambient sound: quiet room tone" in prompt
    assert "brief natural breaths" in prompt
    assert "Perform the described gestures while speaking" in prompt
    assert len(prompt.split()) <= 200


def test_ltx_prompt_rejects_an_ambience_that_cannot_fit_without_silent_loss():
    shot, context = sample()
    shot['soundscape'] = 'quiet room tone ' * 80
    with pytest.raises(ValueError, match='trop long'):
        compile_ltx_prompt(shot, context, first_frame=True)


def test_ltx_prompt_fails_loudly_instead_of_dropping_critical_dialogue():
    shot, context = sample()
    long_line = "This line is deliberately long " * 8
    shot["dialogue"] = " ".join([long_line] * 20)
    shot["dialogue_turns"] = [
        {"speaker": "lila", "text": long_line} for _ in range(20)
    ]
    with pytest.raises(ValueError, match="trop long même après réduction"):
        compile_ltx_prompt(shot, context, first_frame=False)



def test_ltx_prompt_cues_prompt_dialogue_onset_by_default():
    # Sans contrat, la parole démarre franchement : pas de longue amorce muette
    # (la dérive d'onset documentée sur la 1re prise du compteur).
    shot, context = sample()
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "begins promptly as the shot opens" in prompt
    assert "brief silent beat" not in prompt


def test_ltx_prompt_cues_a_silent_beat_when_the_contract_delays_speech():
    # Onset du contrat > 1,0 s : la consigne relative suit le contrat, jamais un
    # timing absolu (interdit par le linter LTX).
    shot, context = sample()
    context["director_contract"] = {"dialogue": [
        {"speaker": "lila", "text": "Bonjour.", "start_sec": 2.5, "end_sec": 3.0},
    ]}
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "begins only after a brief silent beat" in prompt
    assert "begins promptly as the shot opens" not in prompt
    lint_ltx_prompt(prompt)


def test_ltx_prompt_restricts_visible_text_to_declared_counter_numerals():
    shot, context = sample()
    context["director_contract"] = {
        "initial_state": {"compteur": "display reads 000"},
        "final_state": {"compteur": "display reads 001"},
    }
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert "the only visible characters are the numerals 000, 001" in prompt
    assert "No subtitles, captions, speech bubbles, watermarks, logos" in prompt


def test_ltx_prompt_deduplicates_a_repeated_discovery_gesture():
    # La découverte du compteur décrite avant la réplique puis répétée ensuite
    # (défaut du job 681) : la seconde formulation quasi identique est écartée.
    shot, context = sample()
    shot["prompt"] = "A small studio at night."
    shot["action"] = "Marion notices the counter on her wrist."
    shot["reaction"] = "Marion notices the counter at her wrist."
    prompt = compile_ltx_prompt(shot, context, first_frame=False)
    assert prompt.casefold().count("marion notices the counter") == 1
