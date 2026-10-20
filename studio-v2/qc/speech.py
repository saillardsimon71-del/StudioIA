"""Transcription Whisper CPU et mesure du début de parole extraites de V1 (`transcription.py`).

S'exécute toujours dans le même interpréteur Python que Studio IA V2 afin d'éviter
le défaut V1 du 19/10/2026 (Whisper appelé dans un mauvais environnement Python).
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

_LOCK = threading.Lock()
_MODEL = None
_MODEL_SPEC: str | None = None

_FRENCH_NUMBERS = {
    "zero": "0",
    "un": "1",
    "une": "1",
    "deux": "2",
    "trois": "3",
    "quatre": "4",
    "cinq": "5",
    "six": "6",
    "sept": "7",
    "huit": "8",
    "neuf": "9",
    "dix": "10",
    "onze": "11",
    "douze": "12",
    "treize": "13",
    "quatorze": "14",
    "quinze": "15",
    "seize": "16",
    "vingt": "20",
    "trente": "30",
    "quarante": "40",
    "cinquante": "50",
    "soixante": "60",
    "cent": "100",
    "mille": "1000",
}


def normalize_speech_tokens(text: str, language: str = "fr") -> list[str]:
    norm = unicodedata.normalize("NFC", str(text or "").casefold())
    if language.lower().startswith("fr"):
        norm = re.sub(r"é(?:e(?:s)?|s)\b", "é", norm)
    norm = "".join(
        ch for ch in unicodedata.normalize("NFKD", norm) if not unicodedata.combining(ch)
    )
    raw = re.findall(r"\w+", norm)
    return [_FRENCH_NUMBERS.get(tok, tok) for tok in raw]


def _levenshtein(left: list[str], right: list[str]) -> int:
    prev = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        curr = [i]
        for j, b in enumerate(right, start=1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = curr
    return prev[-1]


def transcribe_audio_cpu(
    video_path: Path, model_or_path: str = "small", language: str = "fr"
) -> dict:
    """Transcrit une vidéo sur CPU avec `openai-whisper` et relève le début de parole."""
    global _MODEL, _MODEL_SPEC

    spec = str(model_or_path or "").strip()
    if not spec:
        return {
            "status": "unverified",
            "text": "",
            "first_word_start_sec": None,
            "speech_intervals": [],
            "segments": [],
            "reason": "Modèle Whisper non configuré : contrôle audio humain requis.",
        }

    # Si un chemin de fichier explicite est fourni, vérifier sa présence sans téléchargement surprise
    if ("/" in spec or "\\" in spec or spec.endswith(".pt")) and not Path(spec).expanduser().is_file():
        return {
            "status": "unverified",
            "text": "",
            "first_word_start_sec": None,
            "speech_intervals": [],
            "segments": [],
            "reason": f"Checkpoint Whisper introuvable ({spec}) : vérifiez Paramètres.",
        }

    try:
        import whisper  # type: ignore[import-not-found]
    except ImportError:
        return {
            "status": "unverified",
            "text": "",
            "first_word_start_sec": None,
            "speech_intervals": [],
            "segments": [],
            "reason": "Package `openai-whisper` absent de `.venv` : installez `pip install openai-whisper` sous Windows.",
        }

    try:
        with _LOCK:
            if _MODEL is None or _MODEL_SPEC != spec:
                _MODEL = whisper.load_model(spec, device="cpu")
                _MODEL_SPEC = spec
            raw = _MODEL.transcribe(
                str(video_path),
                fp16=False,
                word_timestamps=True,
                language=language,
            )
    except Exception as exc:
        return {
            "status": "unverified",
            "text": "",
            "first_word_start_sec": None,
            "speech_intervals": [],
            "segments": [],
            "reason": f"Échec Whisper CPU ({type(exc).__name__}: {exc}).",
        }

    text = str(raw.get("text") or "").strip()
    segments_out: list[dict] = []
    intervals: list[list[float]] = []
    first_word_start: float | None = None

    for seg in raw.get("segments") or []:
        words = [
            {
                "word": str(w.get("word") or ""),
                "start": round(float(w.get("start") or 0.0), 3),
                "end": round(float(w.get("end") or 0.0), 3),
                "probability": round(float(w.get("probability") or 0.0), 3),
            }
            for w in (seg.get("words") or [])
        ]
        s_start = round(float(seg.get("start") or 0.0), 3)
        s_end = round(float(seg.get("end") or 0.0), 3)
        segments_out.append(
            {"start": s_start, "end": s_end, "text": str(seg.get("text") or ""), "words": words}
        )
        if words:
            w0, w_last = words[0]["start"], words[-1]["end"]
            if math.isfinite(w0) and math.isfinite(w_last):
                intervals.append([w0, w_last])
                if first_word_start is None or w0 < first_word_start:
                    first_word_start = w0

    return {
        "status": "ok" if text else "empty",
        "text": text,
        "first_word_start_sec": first_word_start,
        "speech_intervals": intervals,
        "segments": segments_out,
        "reason": "" if text else "Aucune parole reconnue par Whisper.",
    }


def assess_speech(
    expected_text: str, transcript: dict, language: str = "fr"
) -> dict:
    """Compare le dialogue attendu au transcript Whisper (WER, couverture, début de parole)."""
    expected_tokens = normalize_speech_tokens(expected_text, language)
    first_start = transcript.get("first_word_start_sec")

    if not expected_tokens:
        if transcript.get("status") == "ok" and transcript.get("text"):
            return {
                "status": "warning",
                "wer": None,
                "coverage": 1.0,
                "first_word_start_sec": first_start,
                "reason": "Plan censé être muet mais paroles détectées : vérifiez à l'écoute.",
            }
        return {
            "status": "ok",
            "wer": 0.0,
            "coverage": 1.0,
            "first_word_start_sec": first_start,
            "reason": "Aucune réplique attendue.",
        }

    if transcript.get("status") != "ok":
        return {
            "status": "unverified",
            "wer": None,
            "coverage": None,
            "first_word_start_sec": first_start,
            "reason": transcript.get("reason") or "Transcription non vérifiée.",
        }

    heard_tokens = normalize_speech_tokens(transcript.get("text") or "", language)
    if not heard_tokens:
        return {
            "status": "warning",
            "wer": 1.0,
            "coverage": 0.0,
            "first_word_start_sec": first_start,
            "reason": "Réplique attendue absente du transcript Whisper.",
        }

    wer = round(_levenshtein(expected_tokens, heard_tokens) / max(1, len(expected_tokens)), 3)
    matcher = SequenceMatcher(None, expected_tokens, heard_tokens, autojunk=False)
    matches = sum(b.size for b in matcher.get_matching_blocks())
    coverage = round(matches / max(1, len(expected_tokens)), 3)

    if coverage >= 0.85 and wer <= 0.25:
        return {
            "status": "ok",
            "wer": wer,
            "coverage": coverage,
            "first_word_start_sec": first_start,
            "reason": "",
        }
    return {
        "status": "warning",
        "wer": wer,
        "coverage": coverage,
        "first_word_start_sec": first_start,
        "reason": f"Divergence partielle Whisper (couverture {coverage:.0%}, WER {wer:.0%}) : écoutez la prise.",
    }
