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


def test_real_french_offer_takes_are_not_rejected_for_numeric_formatting():
    fixture = json.loads((Path(__file__).parent / "fixtures/shot_147_speech.json").read_text(
        encoding="utf-8"
    ))
    turns = [{"speaker": "citron", "text": fixture["expected"]}]
    for take in fixture["takes"]:
        assert transcription.assess_transcript(
            fixture["expected"], take["transcript"]
        )["status"] == "ok"
        assert transcription.assess_turns(turns, take["transcript"])["turns"][0]["coverage"] == 1


@pytest.mark.parametrize("expected,observed,status", [
    ("Je te donne cent mille euros.", "Je te donne 100 000 euros.", "ok"),
    ("Je te donne 100000 euros.", "Je te donne cent mille euros.", "ok"),
    ("Je te donne cent mille euros.", "Je te donne cent euros.", "missing"),
    ("Je te donne cent mille euros.", "Je te donne 100 001 euros.", "missing"),
    ("Il a deux cents points.", "Il a 200 points.", "ok"),
    ("Il a mille cinq cent vingt et un points.", "Il a 1521 points.", "ok"),
    ("Il a soixante et onze points.", "Il a 71 points.", "ok"),
    ("Il a quatre-vingt-dix-neuf points.", "Il a 99 points.", "ok"),
    ("Il a quatre-vingts points.", "Il a 80 points.", "ok"),
    ("Il a vingt et un points.", "Il a 22 points.", "missing"),
    ("Il a un mille points.", "Il a deux mille points.", "missing"),
    ("Un deux trois.", "1 2 3.", "ok"),
])
def test_french_numbers_compare_by_value_and_keep_real_mismatches(expected, observed, status):
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    assert transcription.assess_transcript(expected, transcript)["status"] == status


def test_grouped_number_preserves_the_lowest_asr_word_confidence():
    transcript = {"status": "ok", "text": "Je te donne 100 001 euros.", "segments": [{"words": [
        {"word": word, "probability": 0.3 if word == "001" else 0.98}
        for word in "Je te donne 100 001 euros.".split()
    ]}]}
    expected = "Je te donne cent mille euros."
    assert transcription.assess_transcript(expected, transcript)["status"] == "unknown"
    transcript["segments"][0]["words"][4]["probability"] = 0.98
    assert transcription.assess_transcript(expected, transcript)["status"] == "missing"


def test_real_short_french_exchange_accepts_inaudible_agreement_spelling():
    fixture = json.loads((Path(__file__).parent / "fixtures/shot_160_speech.json").read_text(
        encoding="utf-8"
    ))
    for take in fixture["takes"]:
        original = copy.deepcopy(take["transcript"])
        verdict = transcription.assess_transcript(
            fixture["expected"], take["transcript"], language="fr"
        )
        assert verdict["status"] == "ok"
        turns = transcription.assess_turns(fixture["turns"], take["transcript"], language="fr")
        assert [turn["coverage"] for turn in turns["turns"]] == [1, 1]
        assert take["transcript"] == original


@pytest.mark.parametrize("expected,observed", [
    ("Volée. Empruntée.", "Volé. Emprunté."),
    ("Volé. Emprunté.", "Volée. Empruntée."),
    ("Volés. Empruntées.", "Volé. Emprunté."),
    ("VOLÉES. EMPRUNTÉS.", "volées. empruntée."),
    ("Vole\u0301e. Emprunte\u0301es.", "Volé. Emprunté."),
])
@pytest.mark.parametrize("language", ["fr", "fr-FR", "fr_CA"])
def test_french_silent_e_acute_agreements_compare_by_sound(expected, observed, language):
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    verdict = transcription.assess_transcript(expected, transcript, language=language)
    assert verdict["status"] == "ok"


@pytest.mark.parametrize("language", [None, "en", "de"])
def test_french_sound_equivalence_requires_french_language(language):
    transcript = {"status": "ok", "text": "Volé. Emprunté."}
    assert transcription.assess_transcript(
        "Volée. Empruntée.", transcript, language=language
    )["status"] != "ok"


@pytest.mark.parametrize("expected,observed,status", [
    ("La mallette n'est pas volée.", "La mallette est volé.", "missing"),
    ("La mallette volée contient cent mille euros.",
     "La mallette volé contient 100 001 euros.", "missing"),
    ("Juste empruntée.", "Juste achetée.", "unknown"),
    ("Elle est prise.", "Elle est pris.", "unknown"),
    ("Tu l'as volée ? Juste empruntée.", "Tu l'as volé ?", "unknown"),
    ("Tu l'as volée ? Juste empruntée.",
     "Tu l'as volé ? Juste emprunté. Et maintenant tout le monde doit me payer.", "extra"),
])
def test_french_sound_equivalence_keeps_audible_errors_and_added_speech(expected, observed, status):
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    assert transcription.assess_transcript(expected, transcript, language="fr")["status"] == status


def test_french_sound_equivalence_preserves_unknown_asr_and_missing_turns():
    fixture = json.loads((Path(__file__).parent / "fixtures/shot_160_speech.json").read_text(
        encoding="utf-8"
    ))
    transcript = copy.deepcopy(fixture["takes"][0]["transcript"])
    transcript.update(status="unknown", reason="ASR incertain")
    assert transcription.assess_transcript(
        fixture["expected"], transcript, language="fr"
    )["status"] == "unknown"
    transcript.update(status="ok", text="Tu l'as volé ?")
    turns = fixture["turns"][:1] + [{"speaker": "riko", "text": "Je l'ai juste empruntée."}]
    assert transcription.assess_turns(turns, transcript, language="fr")["status"] == "missing"


def test_cached_native_take_uses_french_equivalence_without_retranscription(
    client, tmp_path, monkeypatch
):
    import asyncio

    from calliope.config import settings
    from calliope.studio import production, service
    from tests.test_studio_pacing import make_clip

    fixture = json.loads((Path(__file__).parent / "fixtures/shot_160_speech.json").read_text(
        encoding="utf-8"
    ))
    project = service.create_project({"title": "Accord oral", "aspect_ratio": "9:16"})["id"]
    with service.database() as conn:
        service.add_scene(conn, project, {"shots": [{"prompt": "Two characters talk.",
            "dialogue": fixture["expected"], "duration_sec": 5.8, "audio_mode": "native"}]})
        conn.commit()
    shot = service.board(project)["scenes"][0]["shots"][0]
    clip = make_clip(tmp_path / "cached.mp4", 5.875, tone_windows=[(0, 5.8)])
    take = service.store_upload(shot["id"], clip.read_bytes(), "video")
    service.select_take(take["id"])
    stat = Path(take["output_path"]).stat()
    original = fixture["takes"][0]
    metadata = take["metadata"] | {"transcription_cache": {
        "signature": [stat.st_size, stat.st_mtime_ns, settings.studio_asr_checkpoint, "fr"],
        "transcript": original["transcript"], "activity": original["activity"],
    }}
    with service.database() as conn:
        conn.execute("INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?)", (
            shot["id"], json.dumps({"language": "fr", "dialogue_turns": fixture["turns"]}),
        ))
        conn.execute("UPDATE studio_takes SET metadata_json=? WHERE id=?",
                     (json.dumps(metadata), take["id"]))
        conn.commit()

    def forbidden(*args):
        pytest.fail("Le transcript existant doit être réutilisé.")

    monkeypatch.setattr(transcription, "transcribe_dialogue", forbidden)
    verdict = asyncio.run(production.assess_take(production.load_shot(shot["id"])))
    assert verdict["status"] == "ok"
    assert [turn["coverage"] for turn in verdict["turns"]] == [1, 1]
    assert verdict["voice"] == original["transcript"]["speech_intervals"]
    stored = production.selected_video(production.load_shot(shot["id"]))["metadata"]
    assert stored["dialogue_qc"]["status"] == "ok"
    assert stored["transcription_cache"]["transcript"] == original["transcript"]


def test_french_homophone_repeat_retains_original_word_times():
    fixture = json.loads((Path(__file__).parent / "fixtures/shot_160_speech.json").read_text(
        encoding="utf-8"
    ))
    transcript = copy.deepcopy(fixture["takes"][0]["transcript"])
    following = copy.deepcopy(transcript["segments"])
    for segment in following:
        for word in segment["words"]:
            word["start"] += 6
            word["end"] += 6
    transcript["text"] += " " + transcript["text"]
    transcript["segments"].extend(following)
    verdict = transcription.assess_transcript(fixture["expected"], transcript, language="fr")
    assert verdict["status"] == "ok"
    assert verdict["repeated_occurrences"] == 2
    assert verdict["voice"][0] == pytest.approx([1.28, 4.34])
    assert verdict["trim_end_limit_sec"] == pytest.approx(7.28)


def test_french_homophones_do_not_hide_recited_visual_instructions():
    expected = "Tu l'as volée ? Juste empruntée."
    observed = "Tu l'as volé ? Juste emprunté. Nora reste figé derrière le grand comptoir."
    transcript = {"status": "ok", "text": observed, "segments": [{"words": [
        {"word": word, "probability": 0.98} for word in observed.split()
    ]}]}
    prompt = "<d>Tu l'as volée ? Juste empruntée.</d> Nora reste figée derrière le grand comptoir."
    assert transcription.assess_transcript(expected, transcript, language="fr")["status"] == "extra"
    diagnostic = transcription.assess_prompt_recitation(
        expected, transcript, prompt, language="fr"
    )
    assert diagnostic["kind"] == "instruction_recitation"
    assert diagnostic["observed_extra"] == "Nora reste figé derrière le grand comptoir"


def _words_transcript(text, probability=0.9, start=0.0, step=0.25):
    words = [
        {
            "word": word,
            "start": start + index * step,
            "end": start + (index + 1) * step,
            "probability": probability,
        }
        for index, word in enumerate(text.split())
    ]
    return {
        "status": "ok",
        "text": text,
        "segments": [{"words": words}],
        "speech_intervals": [[words[0]["start"], words[-1]["end"]]],
    }


def test_qc_v2_rejects_a_short_staging_insertion():
    expected = "Remplis ça. Oui, oui, je... je fais. Plus vite. Tenez, c'est plein."
    observed = (
        "Remplis ça. Oui, oui, je... je fais... Devant le comptoir côté salle, "
        "plus vite. Tenez, c'est plein."
    )
    verdict = transcription.assess_transcript_v2(
        expected, _words_transcript(observed), language="fr"
    )
    assert verdict["status"] == "extra"


def test_qc_v2_rejects_a_low_confidence_missing_last_turn():
    turns = [
        {"speaker": "durio", "text": "Remplis ça."},
        {"speaker": "potiron", "text": "Oui, oui, je... je fais."},
        {"speaker": "durio", "text": "Plus vite."},
        {"speaker": "potiron", "text": "Tenez, c'est plein."},
    ]
    observed = "Remplis ça Oui oui je je fais Plus vite Tenez ces plans"
    verdict = transcription.assess_turns_v2(
        turns, _words_transcript(observed, probability=0.70), language="fr"
    )
    assert verdict["status"] == "missing"


def test_qc_v2_accepts_an_exact_transcript():
    expected = "C'est quoi, ça ? La portion."
    verdict = transcription.assess_transcript_v2(
        expected, _words_transcript(expected), language="fr"
    )
    assert verdict["status"] == "ok"
    assert verdict["wer"] == 0.0


def test_qc_v2_reports_a_timing_drift():
    turns = [{
        "speaker": "durio",
        "text": "One two three",
        "start_sec": 0.0,
        "end_sec": 1.0,
    }]
    verdict = transcription.assess_turns_v2(
        turns,
        _words_transcript("One two three", start=1.5),
        language="en",
    )
    assert verdict["status"] == "timing_mismatch"
    assert verdict["timing_drift_sec"] >= 0.4


def test_qc_v2_can_ignore_timing_drift_for_a_timing_free_prompt():
    turns = [{
        "speaker": "durio",
        "text": "One two three",
        "start_sec": 0.0,
        "end_sec": 1.0,
    }]
    verdict = transcription.assess_turns_v2(
        turns,
        _words_transcript("One two three", start=1.5),
        language="en",
        check_timing=False,
    )
    assert verdict["status"] == "ok"
    assert verdict["timing_drift_sec"] >= 0.4


@pytest.mark.parametrize("text", ["C'est bon, j'ai compris.", "Abonne-toi pour la suite."])
def test_qc_v2_aligns_multiple_tokens_to_their_original_word_times(text):
    transcript = _words_transcript(text, start=0.5)
    verdict = transcription.assess_turns_v2(
        [{"speaker": "durio", "text": text}], transcript, language="fr"
    )
    assert verdict["status"] == "ok"
    assert verdict["turns"][0]["interval"] == transcript["speech_intervals"][0]


# --- environment_failure : échec d'environnement vs résultat honnête ---------
# Un échec d'environnement (checkpoint absent, langue non configurée, Whisper
# indisponible) ne doit jamais être mis en cache : il est retenté après
# réparation. Un résultat honnête (parole incertaine ou absente, ou succès)
# reste mis en cache pour ne pas repayer Whisper.


def test_missing_or_absent_checkpoint_is_an_environment_failure(tmp_path):
    assert transcription.transcribe_dialogue('take.mp4', '', 'fr')['environment_failure'] is True
    absent = transcription.transcribe_dialogue('take.mp4', str(tmp_path / 'absent.pt'), 'fr')
    assert absent['environment_failure'] is True
    assert absent['status'] == 'unknown'


def test_missing_language_is_an_environment_failure(tmp_path):
    checkpoint = tmp_path / 'asr.pt'
    checkpoint.write_bytes(b'x')
    result = transcription.transcribe_dialogue('take.mp4', str(checkpoint), '')
    assert result['environment_failure'] is True


def test_whisper_crash_is_an_environment_failure(tmp_path, monkeypatch):
    checkpoint = tmp_path / 'asr.pt'
    checkpoint.write_bytes(b'x')

    def boom(_checkpoint):
        raise RuntimeError('whisper indisponible')

    monkeypatch.setattr(transcription, '_load_transcriber', boom)
    result = transcription.transcribe_dialogue('take.mp4', str(checkpoint), 'fr')
    assert result['environment_failure'] is True
    assert result['status'] == 'unknown'


def test_honest_uncertain_transcription_is_cacheable(tmp_path, monkeypatch):
    checkpoint = tmp_path / 'asr.pt'
    checkpoint.write_bytes(b'x')

    class Model:
        def transcribe(self, *args, **kwargs):
            return {'text': '', 'segments': []}

    monkeypatch.setattr(transcription, '_load_transcriber', lambda _c: None)
    monkeypatch.setattr(transcription, '_model', Model(), raising=False)
    result = transcription.transcribe_dialogue('take.mp4', str(checkpoint), 'fr')
    assert result['environment_failure'] is False
    assert result['status'] == 'unknown'


def test_successful_transcription_is_cacheable(tmp_path, monkeypatch):
    checkpoint = tmp_path / 'asr.pt'
    checkpoint.write_bytes(b'x')

    class Model:
        def transcribe(self, *args, **kwargs):
            return {'text': 'Bonjour', 'segments': [{
                'start': 0.0, 'end': 1.0, 'avg_logprob': -0.3,
                'no_speech_prob': 0.1, 'compression_ratio': 1.5,
                'words': [{'word': 'Bonjour', 'start': 0.0, 'end': 0.8,
                           'probability': 0.9}],
            }]}

    monkeypatch.setattr(transcription, '_load_transcriber', lambda _c: None)
    monkeypatch.setattr(transcription, '_model', Model(), raising=False)
    result = transcription.transcribe_dialogue('take.mp4', str(checkpoint), 'fr')
    assert result['environment_failure'] is False
    assert result['status'] == 'ok'
    assert result['speech_intervals'] == [[0.0, 0.8]]
