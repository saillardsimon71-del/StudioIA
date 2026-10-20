"""Revue humaine d'une prise : verdicts de scripts/qc_take.py.

Verrouille le principe du dossier de reprise — privilégier « à revoir » à un faux
succès ou un faux rejet, distinguer les chiffres diégétiques du compteur des
sous-titres parasites, et recaler une parole qui dérive du contrat. Aucun GPU,
aucune dépense : l'OCR et Whisper sont simulés.
"""
import importlib.util
from pathlib import Path

import pytest

from calliope.studio import generated_text, transcription

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qc_take():
    spec = importlib.util.spec_from_file_location(
        "qc_take", BACKEND / "scripts" / "qc_take.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text_status,speech_status,expected", [
    ("unexpected_text", "ok", "REJETÉE"),
    ("not_detected", "timing_mismatch", "REJETÉE"),
    ("unverified", "ok", "À REVOIR"),
    ("not_detected", "unchecked", "À REVOIR"),
    ("not_detected", "environment_failure", "À REVOIR"),
    ("not_detected", "unknown", "À REVOIR"),
    ("not_detected", "ok", "CONTRÔLES AUTO OK"),
])
def test_summarize_prefers_review_over_false_success(qc_take, text_status, speech_status, expected):
    assert qc_take.summarize(text_status, speech_status).startswith(expected)


def _mock(monkeypatch, scan_result, transcript_result):
    monkeypatch.setattr(generated_text, "scan", lambda path, allowed=(): scan_result)
    monkeypatch.setattr(transcription, "transcribe_dialogue",
                        lambda *args: transcript_result)


def test_allowed_counter_digits_with_prompt_speech_is_provisionally_ok(qc_take, monkeypatch):
    _mock(monkeypatch,
          {"status": "not_detected", "reason": "Aucun texte parasite.", "hits": [],
           "allowed_count": 3},
          {"status": "ok", "environment_failure": False, "speech_intervals": [[0.6, 2.4]]})
    report = qc_take.qc_take("prise.mp4", allowed=("000", "001", "002"),
                             contract_onset=0.5, checkpoint="local.pt")
    assert report["verdict"].startswith("CONTRÔLES AUTO OK")
    assert report["speech"]["speech_onset_sec"] == 0.6
    assert report["allowed_display"] == ["000", "001", "002"]


def test_parasite_subtitle_is_rejected_even_with_prompt_speech(qc_take, monkeypatch):
    _mock(monkeypatch,
          {"status": "unexpected_text", "reason": "Texte parasite.",
           "hits": [{"sample": 2, "words": ["Dourze"]}]},
          {"status": "ok", "environment_failure": False, "speech_intervals": [[0.6, 2.4]]})
    report = qc_take.qc_take("prise.mp4", allowed=("001",), contract_onset=0.5)
    assert report["verdict"].startswith("REJETÉE")
    assert report["text_qc"]["hits"][0]["words"] == ["Dourze"]


def test_speech_onset_drift_beyond_one_second_is_rejected(qc_take, monkeypatch):
    _mock(monkeypatch,
          {"status": "not_detected", "reason": "Aucun texte parasite.", "hits": []},
          {"status": "ok", "environment_failure": False, "speech_intervals": [[2.6, 3.4]]})
    report = qc_take.qc_take("prise.mp4", allowed=("000",), contract_onset=0.5)
    assert report["verdict"].startswith("REJETÉE")
    assert report["speech"]["status"] == "timing_mismatch"
    assert report["speech"]["speech_onset_sec"] == 2.6


def test_unavailable_ocr_falls_back_to_review(qc_take, monkeypatch):
    _mock(monkeypatch,
          {"status": "unverified", "reason": "Tesseract local indisponible.", "hits": []},
          {"status": "ok", "environment_failure": False, "speech_intervals": [[0.6, 2.4]]})
    report = qc_take.qc_take("prise.mp4", allowed=("001",), contract_onset=0.5)
    assert report["verdict"].startswith("À REVOIR")


def test_missing_contract_onset_is_unchecked_and_needs_review(qc_take, monkeypatch):
    _mock(monkeypatch,
          {"status": "not_detected", "reason": "Aucun texte parasite.", "hits": []},
          {"status": "ok", "environment_failure": False, "speech_intervals": [[0.6, 2.4]]})
    report = qc_take.qc_take("prise.mp4", allowed=("001",), contract_onset=None)
    assert report["verdict"].startswith("À REVOIR")
    assert report["speech"]["status"] == "unchecked"


def test_environment_failure_transcription_needs_review(qc_take, monkeypatch):
    _mock(monkeypatch,
          {"status": "not_detected", "reason": "Aucun texte parasite.", "hits": []},
          {"status": "unknown", "environment_failure": True,
           "reason": "Checkpoint ASR local introuvable.", "speech_intervals": []})
    report = qc_take.qc_take("prise.mp4", allowed=("001",), contract_onset=0.5)
    assert report["verdict"].startswith("À REVOIR")
    assert report["transcription"]["environment_failure"] is True
