import copy
import json
from pathlib import Path

import pytest

from calliope.studio import transcription


def test_real_outro_with_an_inserted_repeated_clause_requires_retry():
    fixture = json.loads((Path(__file__).parent / "fixtures/outro_take_258_speech.json").read_text(
        encoding="utf-8"
    ))
    verdict = transcription.assess_transcript(fixture["expected"], fixture["transcript"])
    assert verdict["status"] == "extra"
    assert "trim_end_limit_sec" not in verdict


def test_intentionally_repeated_outro_is_not_rejected():
    fixture = json.loads((Path(__file__).parent / "fixtures/outro_take_258_speech.json").read_text(
        encoding="utf-8"
    ))
    assert transcription.assess_transcript(
        fixture["transcript"]["text"], fixture["transcript"]
    )["status"] == "ok"


def test_uncertain_repeated_clause_does_not_establish_invented_speech():
    fixture = json.loads((Path(__file__).parent / "fixtures/outro_take_258_speech.json").read_text(
        encoding="utf-8"
    ))
    for segment in fixture["transcript"]["segments"]:
        for word in segment["words"]:
            word["probability"] = 0.2
    assert transcription.assess_transcript(
        fixture["expected"], fixture["transcript"]
    )["status"] == "unknown"


@pytest.mark.parametrize("observed,words", [
    (
        "That's impossible, homie. Leo doesn't rapon still dare to get no. Leo.",
        [("That's", 0.545), ("impossible,", 0.964), ("homie.", 0.835),
         ("Leo", 0.931), ("doesn't", 0.983), ("rapon", 0.656), ("still", 0.901),
         ("dare", 0.588), ("to", 0.961), ("get", 0.997), ("no.", 0.736), ("Leo.", 0.984)],
    ),
    (
        "That's impossible, homie. Leo doesn't Rapon's still at no.",
        [("That's", 0.619), ("impossible,", 0.936), ("homie.", 0.894),
         ("Leo", 0.952), ("doesn't", 0.991), ("Rapon's", 0.574), ("still", 0.656),
         ("at", 0.682), ("no.", 0.646)],
    ),
])
def test_recited_reaction_is_diagnosed_without_accepting_the_extra_words(observed, words):
    expected = "That's impossible, homie."
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": probability} for word, probability in words
    ]}]}
    prompt = (
        "integrated_multimodal_description: [Shot 1] Student 2 (S9) says: "
        "<d>[English] That's impossible, homie.</d> "
        "Leo doesn't respond - still staring at Noah. Leo remains frozen.\n\n"
        "overall_soundscape: Quiet hallway.\n\nnon_diegetic_music: N/A"
    )
    original = copy.deepcopy(transcript)
    assert transcription.assess_transcript(expected, transcript)["status"] == "extra"
    diagnostic = transcription.assess_prompt_recitation(expected, transcript, prompt)
    assert diagnostic["kind"] == "instruction_recitation"
    assert diagnostic["expected"] == expected
    assert diagnostic["observed"] == observed
    assert "Leo doesn't" in diagnostic["observed_extra"]
    assert "Leo doesn't respond" in diagnostic["instruction"]
    assert diagnostic["matched_tokens"] >= 4
    assert transcript == original
    assert transcription.assess_transcript(expected, transcript)["status"] == "extra"


def test_complete_second_reply_is_dialogue_and_never_a_recited_instruction():
    turns = [{"speaker": "a", "text": "You changed the score."},
             {"speaker": "b", "text": "Look again. I won."}]
    expected = "You changed the score. Look again. I won."
    transcript = {"status": "ok", "text": expected, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in expected.split()
    ]}]}
    prompt = (
        "integrated_multimodal_description: [Shot 1] Two students face each other. "
        "Ada (S1) says: <d>[English] You changed the score.</d> "
        "Ben (S2) says: <d>[English] Look again. I won.</d>"
    )
    assert transcription.assess_transcript(expected, transcript)["status"] == "ok"
    assert transcription.assess_turns(turns, transcript)["status"] == "ok"
    assert transcription.assess_prompt_recitation(expected, transcript, prompt) == {}


@pytest.mark.parametrize("status,probability", [("unknown", 0.98), ("ok", 0.3)])
def test_uncertain_recognition_does_not_establish_prompt_recitation(status, probability):
    observed = "That's impossible, homie. Leo doesn't respond still staring at Noah."
    transcript = {"status": status, "text": observed,
                  "segments": [{"words": [
                      {"word": word, "probability": probability}
                      for word in observed.split()
                  ]}]}
    prompt = "Student 2 says: <d>[English] That's impossible, homie.</d> "
    prompt += "Leo doesn't respond - still staring at Noah."
    assert transcription.assess_prompt_recitation("That's impossible, homie.", transcript,
                                                  prompt) == {}


@pytest.mark.parametrize("extra,prompt", [
    ("We should go home before the storm arrives.",
     "Leo doesn't respond - still staring at Noah."),
    ("Leo doesn't respond still staring at Noah.",
     "Leo (S2) says: <d>[English] Leo doesn't respond still staring at Noah.</d>"),
])
def test_unrelated_extra_speech_and_tagged_dialogue_are_not_visual_recitation(extra, prompt):
    expected = "That's impossible, homie."
    observed = expected + " " + extra
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    assert transcription.assess_transcript(expected, transcript)["status"] == "extra"
    assert transcription.assess_prompt_recitation(expected, transcript, prompt) == {}


def test_unknown_compound_number_transcription_stays_unknown():
    transcript = {"status": "unknown", "text": "My son, a 500.",
                  "reason": "Transcription incertaine ou aucune parole reconnue.",
                  "segments": [{"avg_logprob": -0.7261198891533746, "words": [
                      {"word": "My", "probability": 0.745},
                      {"word": "son,", "probability": 0.554},
                      {"word": "a", "probability": 0.891},
                      {"word": "500.", "probability": 0.912},
                  ]}]}
    assert transcription.assess_transcript("My son. A five hundred.", transcript) == {
        "status": "unknown", "reason": "Transcription incertaine ou aucune parole reconnue.",
    }


@pytest.mark.parametrize("expected,observed,status", [
    ("My son. A five hundred.", "My son, a 500.", "ok"),
    ("He has two hundred and five points.", "He has 205 points.", "ok"),
    ("He has twelve thousand three hundred and forty-five points.",
     "He has 12345 points.", "ok"),
    ("He has one thousand points.", "He has 1000 points.", "ok"),
    ("He has five hundred thousand points.", "He has 500000 points.", "ok"),
    ("My son. A five hundred.", "My son, a 1.", "missing"),
    ("He has one thousand points.", "He has 2000 points.", "missing"),
    ("He has 500 points.", "He has 1 points.", "missing"),
])
def test_compound_numbers_compare_by_value_without_hiding_changed_numbers(expected, observed,
                                                                          status):
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    assert transcription.assess_transcript(expected, transcript)["status"] == status


def test_compound_number_in_a_complete_two_speaker_exchange_is_not_a_changed_number():
    turns = [
        {"speaker": "maximilian", "text":
         "Keep the five hundred. I don't want the other one, homie."},
        {"speaker": "elise", "text": "They're both your sons, Maximilian."},
    ]
    expected = "Keep the five hundred. I don't want the other one, homie. "
    expected += "They're both your sons, Maximilian."
    observed = "Keep the 500. I don't want the other one homie. They're both your son's Maximilian"
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    assert transcription.assess_transcript(expected, transcript)["status"] == "ok"
    checked = transcription.assess_turns(turns, transcript)
    assert checked["status"] == "ok"
    assert checked["turns"][0]["coverage"] == 1


def test_changed_compound_number_keeps_the_lowest_word_confidence():
    transcript = {"status": "ok", "text": "He has six hundred points.", "segments": [{"words": [
        {"word": word, "probability": 0.3 if word == "six" else 0.98}
        for word in "He has six hundred points.".split()
    ]}]}
    assert transcription.assess_transcript("He has 500 points.", transcript)["status"] == "unknown"
    transcript["segments"][0]["words"][2]["probability"] = 0.98
    assert transcription.assess_transcript("He has 500 points.", transcript)["status"] == "missing"
