import math
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

_lock = threading.Lock()
_model = None
_checkpoint = None


def transcribe_dialogue(path, checkpoint_path, language):
    global _model, _checkpoint
    result = {
        "status": "unknown", "text": "", "segments": [], "speech_intervals": [],
        "reason": "Checkpoint ASR local non configuré.",
    }
    if not checkpoint_path:
        return result
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if not checkpoint.is_file():
        result["reason"] = "Checkpoint ASR local introuvable. Aucun téléchargement effectué."
        return result
    if not language:
        result["reason"] = "Langue de transcription non configurée."
        return result
    try:
        with _lock:
            import whisper

            if _model is None or _checkpoint != str(checkpoint):
                _model = whisper.load_model(str(checkpoint), device="cpu")
                _checkpoint = str(checkpoint)
            raw = _model.transcribe(
                str(path), fp16=False, word_timestamps=True, language=language,
            )
        result["text"] = str(raw.get("text") or "").strip()
        segments = raw.get("segments") or []
        certain = bool(segments) and bool(result["text"])
        for segment in segments:
            start, end = float(segment["start"]), float(segment["end"])
            logprob = float(segment.get("avg_logprob", -10))
            no_speech = float(segment.get("no_speech_prob", 1))
            compression = float(segment.get("compression_ratio", 10))
            words = [{
                "word": str(word["word"]), "start": float(word["start"]),
                "end": float(word["end"]), "probability": float(word.get("probability", 0)),
            } for word in segment.get("words", [])]
            result["segments"].append({
                "start": start, "end": end, "text": str(segment.get("text") or ""),
                "avg_logprob": logprob, "no_speech_prob": no_speech,
                "compression_ratio": compression, "words": words,
            })
            certain = certain and (
                math.isfinite(start) and math.isfinite(end) and 0 <= start < end
                and math.isfinite(logprob) and logprob >= -0.7
                and math.isfinite(no_speech) and no_speech <= 0.5
                and math.isfinite(compression) and compression <= 2.4
                and bool(words)
                and any(word["start"] < word["end"] for word in words)
                and all(math.isfinite(word["start"]) and math.isfinite(word["end"])
                        and 0 <= word["start"] <= word["end"] for word in words)
            )
        if not certain:
            result["reason"] = "Transcription incertaine ou aucune parole reconnue."
            return result
        result["speech_intervals"] = [
            [segment["words"][0]["start"], segment["words"][-1]["end"]]
            for segment in result["segments"]
        ]
        result.update(status="ok", reason="")
    except Exception as exc:
        result["reason"] = f"Transcription CPU indisponible ({type(exc).__name__})."
    return result


def _tokens(text):
    text = str(text or "")
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))
    return re.findall(r"\w+", text)


def assess_transcript(expected, transcript):
    expected_words = _tokens(expected)
    if not expected_words:
        return {"status": "ok", "reason": ""}
    if not transcript or transcript.get("status") != "ok":
        return {"status": "unknown", "reason": (transcript or {}).get("reason")
                or "Transcription indisponible."}
    actual_words = _tokens(transcript.get("text"))
    if not actual_words:
        return {"status": "missing", "reason": "Réplique attendue absente du transcript fiable."}
    # Les petits nombres peuvent être écrits en chiffres par l'ASR. Une table
    # limitée suffit ici; les formes composées restent une comparaison incertaine.
    numbers = {
        "zero": "0", "un": "1", "une": "1", "deux": "2", "trois": "3",
        "quatre": "4", "cinq": "5", "six": "6", "sept": "7", "huit": "8",
        "neuf": "9", "dix": "10", "onze": "11", "douze": "12", "treize": "13",
        "quatorze": "14", "quinze": "15", "seize": "16", "one": "1", "two": "2",
        "three": "3", "four": "4", "five": "5", "seven": "7", "eight": "8",
        "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    }
    wanted = [numbers.get(word, word) for word in expected_words]
    heard = [numbers.get(word, word) for word in actual_words]
    matcher = SequenceMatcher(None, wanted, heard, autojunk=False)
    matches = sum(block.size for block in matcher.get_matching_blocks())
    coverage = matches / len(wanted)
    precision = matches / len(heard)
    if wanted == heard:
        return {"status": "ok", "reason": ""}
    word_tokens, probabilities = [], []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            tokens = _tokens(word["word"])
            word_tokens.extend(tokens)
            probabilities.extend([float(word.get("probability", 0))] * len(tokens))
    if word_tokens and word_tokens != actual_words:
        probabilities = [0] * len(heard)
    elif not word_tokens:
        probabilities = [1] * len(heard)
    uncertain_changes = 0
    uncertain_gap = False
    number_change = False
    number_ambiguous = False
    for tag, wanted_begin, wanted_end, begin, end in matcher.get_opcodes():
        if tag == "equal":
            continue
        uncertain_changes += sum(probabilities[index] < 0.5 for index in range(begin, end))
        if tag == "delete":
            neighbors = probabilities[max(0, begin - 1):min(len(heard), begin + 1)]
            uncertain_gap = uncertain_gap or any(probability < 0.5 for probability in neighbors)
        wanted_numbers = [word for word in wanted[wanted_begin:wanted_end] if word.isdigit()]
        heard_numbers = [word for word in heard[begin:end] if word.isdigit()]
        changed_words = expected_words[wanted_begin:wanted_end] + actual_words[begin:end]
        if wanted_numbers != heard_numbers and any(
            word.isdigit() or word in numbers and word not in {"un", "une"}
            for word in changed_words
        ):
            number_change = True
            number_ambiguous = number_ambiguous or bool(set(changed_words) & {"un", "une"})
    unknown = {"status": "unknown", "reason": "Mots divergents insuffisamment certains."}
    if number_ambiguous:
        return {"status": "unknown", "reason": "Un/une ambigu dans la comparaison numérique."}
    if coverage >= 0.6 and precision >= 0.6:
        negations = {"pas", "jamais", "not", "never", "no"}
        wanted_negations = set(wanted) & negations
        heard_negations = set(heard) & negations
        for words, found in ((wanted, wanted_negations), (heard, heard_negations)):
            for index, word in enumerate(words):
                if word != "plus":
                    continue
                preceding = words[max(0, index - 4):index]
                if set(preceding) & {"ne", "n"} and not set(preceding) & {"pas", "jamais"}:
                    found.add("ne_plus")
        if wanted_negations != heard_negations:
            if uncertain_changes or uncertain_gap:
                return unknown
            return {"status": "missing", "reason": "Négation modifiée dans le transcript fiable."}
        if number_change:
            if uncertain_changes or uncertain_gap:
                return unknown
            return {"status": "missing", "reason": "Nombre modifié dans le transcript fiable."}
    if coverage >= 0.7 and precision >= 0.7:
        return {"status": "ok", "reason": ""}
    if coverage >= 0.7 and precision < 0.5:
        if (matches + uncertain_changes) / len(heard) >= 0.5:
            return unknown
        return {"status": "extra",
                "reason": "Paroles supplémentaires importantes dans le transcript."}
    if coverage < 0.45:
        if uncertain_gap or (matches + uncertain_changes) / len(wanted) >= 0.45:
            return unknown
        return {"status": "missing",
                "reason": "Réplique absente ou divergence majeure du transcript."}
    return {"status": "unknown", "reason": "Comparaison de la réplique incertaine."}
