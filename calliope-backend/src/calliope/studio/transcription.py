import math
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

_lock = threading.Lock()
_model = None
_checkpoint = None


def _load_transcriber(checkpoint):
    global _model, _checkpoint
    import whisper

    if _model is None or _checkpoint != str(checkpoint):
        _model = whisper.load_model(str(checkpoint), device='cpu')
        _checkpoint = str(checkpoint)


def warm_transcriber(checkpoint_path):
    if not checkpoint_path:
        return
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if checkpoint.is_file():
        with _lock:
            _load_transcriber(checkpoint)


def transcribe_dialogue(path, checkpoint_path, language):
    """Transcrit une prise avec Whisper CPU.

    ``environment_failure`` distingue un échec d'environnement (checkpoint
    absent, langue non configurée, import/whisper indisponible) d'un résultat
    d'analyse honnête (parole incertaine ou absente). Un échec d'environnement
    ne doit jamais être mis en cache : après réparation du lanceur ou du
    checkpoint, la transcription doit être retentée. Un résultat honnête,
    ``ok`` ou ``unknown`` stable pour le même fichier, reste mis en cache pour
    ne pas repayer Whisper à chaque contrôle.
    """
    global _model, _checkpoint
    result = {
        "status": "unknown", "text": "", "segments": [], "speech_intervals": [],
        "reason": "Checkpoint ASR local non configuré.", "environment_failure": True,
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
            _load_transcriber(checkpoint)
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
            result["environment_failure"] = False
            return result
        result["speech_intervals"] = [
            [segment["words"][0]["start"], segment["words"][-1]["end"]]
            for segment in result["segments"]
        ]
        result.update(status="ok", reason="", environment_failure=False)
    except Exception as exc:
        result["reason"] = f"Transcription CPU indisponible ({type(exc).__name__})."
        result["environment_failure"] = True
    return result


def _tokens(text, language=None):
    text = unicodedata.normalize("NFC", str(text or "").casefold())
    if str(language or "").lower().replace("_", "-").split("-")[0] == "fr":
        # These French agreement endings are inaudible; ASR cannot establish their spelling.
        text = re.sub(r"é(?:e(?:s)?|s)\b", "é", text)
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    return re.findall(r"\w+", text)


_ENGLISH_UNITS = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split(), range(20),
))
_ENGLISH_TENS = dict(zip(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10),
))
_FRENCH_UNITS = dict(zip(
    "zero un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze "
    "seize".split(), range(17),
)) | {"une": 1}
_FRENCH_TENS = dict(zip("vingt trente quarante cinquante soixante".split(), range(20, 70, 10)))
_NUMBER_WORDS = {
    word: str(value) for word, value in (
        _FRENCH_UNITS | _FRENCH_TENS | {"cent": 100, "cents": 100, "mille": 1000}
        | _ENGLISH_UNITS | _ENGLISH_TENS
    ).items()
}


def _english_number(words, start):
    def under_hundred(index):
        if index >= len(words):
            return None, index
        word = words[index]
        if word in _ENGLISH_UNITS:
            return _ENGLISH_UNITS[word], index + 1
        if word in _ENGLISH_TENS:
            value, end = _ENGLISH_TENS[word], index + 1
            if end < len(words) and 1 <= _ENGLISH_UNITS.get(words[end], 0) <= 9:
                value += _ENGLISH_UNITS[words[end]]
                end += 1
            return value, end
        return None, index

    def under_thousand(index):
        value, end = under_hundred(index)
        if value is not None and 1 <= value <= 9 and end < len(words) and words[end] == "hundred":
            value *= 100
            end += 1
            remainder_start = end + 1 if end < len(words) and words[end] == "and" else end
            remainder, remainder_end = under_hundred(remainder_start)
            if remainder is not None and 1 <= remainder <= 99:
                value += remainder
                end = remainder_end
        return value, end

    value, end = under_thousand(start)
    if value is not None and 1 <= value <= 999 and end < len(words) and words[end] == "thousand":
        value *= 1000
        end += 1
        remainder_start = end + 1 if end < len(words) and words[end] == "and" else end
        remainder, remainder_end = under_thousand(remainder_start)
        if remainder is not None and 1 <= remainder <= 999:
            value += remainder
            end = remainder_end
    return value, end


def _french_number(words, start):
    def under_hundred(index):
        if index >= len(words):
            return None, index
        word = words[index]
        value = _FRENCH_UNITS.get(word, _FRENCH_TENS.get(word))
        end = index + 1
        if word == "quatre" and end < len(words) and words[end] in {"vingt", "vingts"}:
            value = 80
            end += 1
        if value is None:
            return None, index
        if value not in {10, 20, 30, 40, 50, 60, 80}:
            return value, end
        remainder_start = end
        conjunction = end < len(words) and words[end] == "et"
        if conjunction:
            remainder_start += 1
        remainder = (
            _FRENCH_UNITS.get(words[remainder_start]) if remainder_start < len(words) else None
        )
        if remainder == 10 and remainder_start + 1 < len(words):
            unit = _FRENCH_UNITS.get(words[remainder_start + 1], 0)
            if 7 <= unit <= 9:
                remainder += unit
                remainder_start += 1
        if remainder is not None:
            if conjunction:
                valid = remainder == 1 or value == 60 and remainder == 11
            elif value == 10:
                valid = 7 <= remainder <= 9
            else:
                valid = 1 <= remainder <= (19 if value in {60, 80} else 9)
            if valid:
                return value + remainder, remainder_start + 1
        return value, end

    def under_thousand(index):
        value, end = under_hundred(index)
        if index < len(words) and words[index] in {"cent", "cents"}:
            value, end = 100, index + 1
        elif value is not None and 1 <= value <= 9 and end < len(words) and words[end] in {
            "cent", "cents",
        }:
            value *= 100
            end += 1
        else:
            return value, end
        remainder, remainder_end = under_hundred(end)
        if remainder is not None and 1 <= remainder <= 99:
            value += remainder
            end = remainder_end
        return value, end

    value, end = under_thousand(start)
    if words[start] == "mille":
        value, end = 1000, start + 1
    elif value is not None and 1 <= value <= 999 and end < len(words) and words[end] == "mille":
        value *= 1000
        end += 1
    else:
        return value, end
    remainder, remainder_end = under_thousand(end)
    if remainder is not None and 1 <= remainder <= 999:
        value += remainder
        end = remainder_end
    return value, end


def _comparison_tokens(words):
    normalized, spans = [], []
    index = 0
    while index < len(words):
        value, end = _english_number(words, index)
        if value is None:
            value, end = _french_number(words, index)
        if words[index].isdigit():
            digits, end = words[index], index + 1
            if 1 <= len(digits) <= 3 and not digits.startswith("0"):
                while end < len(words) and words[end].isdigit() and len(words[end]) == 3:
                    digits += words[end]
                    end += 1
            value = int(digits)
        normalized.append(
            str(value) if value is not None else _NUMBER_WORDS.get(words[index], words[index])
        )
        if value is None:
            end = index + 1
        spans.append((index, end))
        index = end
    return normalized, spans


def assess_prompt_recitation(expected, transcript, compiled_prompt, language=None):
    if not transcript or transcript.get("status") != "ok":
        return {}
    wanted = _tokens(expected, language)
    observed = str(transcript.get("text") or "")
    heard = _tokens(observed, language)
    if not wanted or wanted == heard:
        return {}
    speech = SequenceMatcher(None, wanted, heard, autojunk=False)
    if sum(block.size for block in speech.get_matching_blocks()) != len(wanted):
        return {}
    word_tokens, probabilities = [], []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            tokens = _tokens(word["word"], language)
            word_tokens.extend(tokens)
            probabilities.extend([float(word.get("probability", 0))] * len(tokens))
    if word_tokens != heard:
        return {}
    visual = re.sub(r"<d>.*?</d>", "\n", str(compiled_prompt or ""), flags=re.I | re.S)
    instructions = [part.strip() for part in re.split(r"[.!?\n]+", visual) if part.strip()]
    token_positions = list(re.finditer(r"\w+", observed))
    for tag, _, _, begin, end in speech.get_opcodes():
        if tag == "equal" or end - begin < 4:
            continue
        extra = heard[begin:end]
        for instruction in instructions:
            source = _tokens(instruction, language)
            blocks = SequenceMatcher(None, source, extra, autojunk=False).get_matching_blocks()
            matches = sum(block.size for block in blocks)
            anchor = any(
                block.size >= 3 and all(
                    math.isfinite(probabilities[begin + index])
                    and probabilities[begin + index] >= 0.8
                    for index in range(block.b, block.b + block.size)
                ) for block in blocks
            )
            if anchor and matches >= 4 and matches / min(len(source), len(extra)) >= 0.5:
                return {
                    "kind": "instruction_recitation", "expected": expected,
                    "observed": observed,
                    "observed_extra": observed[token_positions[begin].start():
                                               token_positions[end - 1].end()],
                    "instruction": instruction, "matched_tokens": matches,
                    "reason": "Paroles supplémentaires correspondant à une directive du prompt.",
                }
    return {}


def assess_turns(turns, transcript, language=None):
    if not transcript or transcript.get("status") != "ok":
        return {"status": "unknown", "reason": "Transcription indisponible.", "speaker_identity_status": "unverified"}
    wanted = [_comparison_tokens(_tokens(turn["text"], language))[0] for turn in turns]
    heard = _comparison_tokens(_tokens(transcript.get("text"), language))[0]
    flat = [word for turn in wanted for word in turn]
    matching = set()
    for block in SequenceMatcher(None, flat, heard, autojunk=False).get_matching_blocks():
        matching.update(range(block.a, block.a + block.size))
    probabilities = [
        word.get("probability", 0) for segment in transcript.get("segments", [])
        for word in segment.get("words", [])
    ]
    reliable = probabilities and sum(probabilities) / len(probabilities) >= 0.8
    offset, checks = 0, []
    for index, words in enumerate(wanted):
        matched = sum(i in matching for i in range(offset, offset + len(words)))
        coverage = matched / max(1, len(words))
        status = "missing" if reliable and len(words) >= 3 and coverage < 0.45 else "ok"
        checks.append({"turn": index, "speaker": turns[index]["speaker"],
                       "expected_speaker": turns[index]["speaker"],
                       "expected": turns[index]["text"], "coverage": coverage, "status": status})
        offset += len(words)
    missing = next((item for item in checks if item["status"] == "missing"), None)
    return {
        "status": "missing" if missing else "ok",
        "reason": (
            f"Tour de parole {missing['turn'] + 1} absent du transcript fiable." if missing else ""
        ),
        "turns": checks,
        "speaker_identity_status": "unverified",
    }


def assess_transcript(expected, transcript, language=None):
    expected_words = _tokens(expected, language)
    if not expected_words:
        return {"status": "ok", "reason": ""}
    if not transcript or transcript.get("status") != "ok":
        return {"status": "unknown", "reason": (transcript or {}).get("reason")
                or "Transcription indisponible."}
    actual_words = _tokens(transcript.get("text"), language)
    if not actual_words:
        return {"status": "missing", "reason": "Réplique attendue absente du transcript fiable."}
    occurrences = len(actual_words) // len(expected_words)
    if (
        len(expected_words) >= 3 and occurrences > 1
        and actual_words == expected_words * occurrences
    ):
        timed = []
        for segment in transcript.get("segments") or []:
            for word in segment.get("words") or []:
                timed.extend((token, word) for token in _tokens(word["word"], language))
        if [token for token, _ in timed] == actual_words:
            first = [word for _, word in timed[:len(expected_words)]]
            following = timed[len(expected_words)][1]
            times = [word.get(key) for word in first for key in ("start", "end")]
            limit = following.get("start")
            if all(isinstance(t, (int, float)) and math.isfinite(t) and t >= 0
                   for t in times + [limit]):
                start, end = min(times[::2]), max(times[1::2])
                if start < end <= limit and all(w["start"] <= w["end"] for w in first):
                    return {
                        "status": "ok", "reason": "Répétition finale supprimable au montage.",
                        "voice": [[start, end]], "trim_end_limit_sec": limit,
                        "repeated_occurrences": occurrences,
                    }
        return {"status": "extra", "reason": "Réplique répétée sans coupe temporelle fiable."}
    wanted, wanted_spans = _comparison_tokens(expected_words)
    heard, heard_spans = _comparison_tokens(actual_words)
    matcher = SequenceMatcher(None, wanted, heard, autojunk=False)
    matches = sum(block.size for block in matcher.get_matching_blocks())
    coverage = matches / len(wanted)
    precision = matches / len(heard)
    if wanted == heard:
        return {"status": "ok", "reason": ""}
    word_tokens, probabilities = [], []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            tokens = _tokens(word["word"], language)
            word_tokens.extend(tokens)
            probabilities.extend([float(word.get("probability", 0))] * len(tokens))
    if word_tokens and word_tokens != actual_words:
        probabilities = [0] * len(actual_words)
    elif not word_tokens:
        probabilities = [1] * len(actual_words)
    probabilities = [min(probabilities[start:end]) for start, end in heard_spans]
    if word_tokens and coverage >= 0.9 and precision < 0.8:
        for tag, _, _, begin, end in matcher.get_opcodes():
            if tag != "insert":
                continue
            repeated = SequenceMatcher(None, wanted, heard[begin:end], autojunk=False)
            for block in repeated.get_matching_blocks():
                if any(
                    all(probabilities[begin + index] >= 0.8 for index in range(start, start + 4))
                    for start in range(block.b, block.b + block.size - 3)
                ):
                    return {
                        "status": "extra",
                        "reason": "Fragment de réplique répété avec des paroles supplémentaires.",
                    }
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
        changed_words = [
            word for start, stop in wanted_spans[wanted_begin:wanted_end]
            for word in expected_words[start:stop]
        ] + [word for start, stop in heard_spans[begin:end] for word in actual_words[start:stop]]
        if wanted_numbers != heard_numbers and any(
            word.isdigit() or word in _NUMBER_WORDS and word not in {"un", "une"}
            for word in changed_words
        ):
            number_change = True
            number_ambiguous = number_ambiguous or any(
                stop - start == 1 and words[start] in {"un", "une"}
                for words, spans in (
                    (expected_words, wanted_spans[wanted_begin:wanted_end]),
                    (actual_words, heard_spans[begin:end]),
                ) for start, stop in spans
            )
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



def _levenshtein(left, right):
    previous = list(range(len(right) + 1))
    for i, value in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (value != other),
            ))
        previous = current
    return previous[-1]


def _speech_alignment(turns, transcript, language=None):
    """Aligne les tours attendus sur les mots horodatÃ©s du transcript."""
    turn_tokens = [_tokens(turn.get("text"), language) for turn in turns]
    flat = [token for words in turn_tokens for token in words]
    heard_words = [
        word for segment in transcript.get("segments") or []
        for word in segment.get("words") or []
    ]
    token_words = [
        (token, word) for word in heard_words for token in _tokens(word["word"], language)
    ]
    heard = [token for token, _ in token_words]
    turn_of_index = [index for index, words in enumerate(turn_tokens) for _ in words]
    matcher = SequenceMatcher(None, flat, heard, autojunk=False)
    turn_heard = {index: [] for index in range(len(turns))}
    matched_expected, matched_heard = set(), set()
    for block in matcher.get_matching_blocks():
        for delta in range(block.size):
            expected_index = block.a + delta
            heard_index = block.b + delta
            matched_expected.add(expected_index)
            matched_heard.add(heard_index)
            turn_heard[turn_of_index[expected_index]].append(heard_index)
    extras = [heard[index] for index in range(len(heard)) if index not in matched_heard]
    results, max_drift, offset = [], 0.0, 0
    for index, (turn, words) in enumerate(zip(turns, turn_tokens)):
        coverage = (
            sum(token in matched_expected for token in range(offset, offset + len(words)))
            / max(1, len(words))
        )
        indexes = turn_heard[index]
        interval, drift = None, None
        if indexes:
            first = token_words[min(indexes)][1]
            last = token_words[max(indexes)][1]
            if "start" in first and "end" in last:
                interval = [float(first["start"]), float(last["end"])]
        expected_start = turn.get("start_sec")
        expected_end = turn.get("end_sec")
        if interval and isinstance(expected_start, (int, float)) and isinstance(
            expected_end, (int, float)
        ):
            drift = max(abs(interval[0] - expected_start), abs(interval[1] - expected_end))
            max_drift = max(max_drift, drift)
        results.append({
            "turn": index,
            "speaker": turn.get("speaker"),
            "expected_speaker": turn.get("speaker"),
            "expected": turn.get("text"),
            "coverage": round(coverage, 3),
            "status": "missing" if coverage < 0.80 and len(words) >= 3 else "ok",
            "interval": interval,
            "timing_drift_sec": round(drift, 3) if drift is not None else None,
        })
        offset += len(words)
    probabilities = [float(word.get("probability", 0)) for word in heard_words]
    mean_probability = sum(probabilities) / len(probabilities) if probabilities else None
    return results, extras, mean_probability, round(max_drift, 3)


def assess_turns_v2(turns, transcript, language=None, check_timing=True):
    """QC strict par tour : couverture, ordre, timings et confiance."""
    if not transcript or transcript.get("status") != "ok":
        return {
            "status": "unknown",
            "reason": "Transcription indisponible.",
            "turns": [],
            "speaker_identity_status": "unverified",
            "speaker_identity_reason": "Diarisation non installÃ©e.",
        }
    if not turns:
        return {
            "status": "ok",
            "reason": "",
            "turns": [],
            "speaker_identity_status": "unverified",
            "speaker_identity_reason": "Aucun tour Ã  vÃ©rifier.",
        }
    checks, extras, mean_probability, drift = _speech_alignment(
        turns, transcript, language
    )
    missing = next((item for item in checks if item["status"] == "missing"), None)
    if missing:
        status = "missing"
        reason = f"Tour de parole {missing['turn'] + 1} absent ou trop incomplet."
    elif len(extras) > 2:
        status = "extra"
        reason = f"{len(extras)} mots hors script : {' '.join(extras[:8])}."
    elif check_timing and drift > 0.40:
        status = "timing_mismatch"
        reason = f"DÃ©rive temporelle de {drift:.2f} s entre contrat et parole."
    elif (
        mean_probability is not None
        and mean_probability < 0.80
        and (extras or any(item["coverage"] < 0.95 for item in checks))
    ):
        status = "unknown"
        reason = f"Confiance ASR faible ({mean_probability:.2f}) avec divergences."
    else:
        status, reason = "ok", ""
    return {
        "status": status,
        "reason": reason,
        "turns": checks,
        "timing_drift_sec": drift,
        "mean_word_probability": round(mean_probability, 3)
        if mean_probability is not None else None,
        "speaker_identity_status": "unverified",
        "speaker_identity_reason": (
            "Diarisation non installÃ©e : le locuteur est recopiÃ© de l'attendu."
        ),
    }


def assess_transcript_v2(expected, transcript, language=None):
    """QC strict global : WER, mots hors script, couverture et ordre."""
    expected_tokens = _tokens(expected, language)
    if not expected_tokens:
        return {"status": "ok", "reason": "", "wer": 0.0, "extras": []}
    if not transcript or transcript.get("status") != "ok":
        return {
            "status": "unknown",
            "reason": (transcript or {}).get("reason") or "Transcription indisponible.",
            "wer": None,
            "extras": [],
        }
    wanted, _ = _comparison_tokens(expected_tokens)
    heard_tokens = _tokens(transcript.get("text"), language)
    heard, _ = _comparison_tokens(heard_tokens)
    if not heard:
        return {
            "status": "missing",
            "reason": "RÃ©plique attendue absente du transcript.",
            "wer": 1.0,
            "extras": [],
        }
    wer = _levenshtein(wanted, heard) / max(1, len(wanted))
    matcher = SequenceMatcher(None, wanted, heard, autojunk=False)
    matches = sum(block.size for block in matcher.get_matching_blocks())
    extras = [
        token
        for tag, _, _, begin, end in matcher.get_opcodes()
        if tag in ("insert", "replace")
        for token in heard[begin:end]
    ]
    coverage = matches / max(1, len(wanted))
    if wer == 0 and not extras:
        status, reason = "ok", ""
    elif len(extras) > 2:
        status = "extra"
        reason = f"{len(extras)} mots hors script."
    elif coverage < 0.90:
        status = "missing"
        reason = f"Couverture insuffisante ({coverage:.2f})."
    elif wer > 0.10:
        status = "extra"
        reason = f"WER trop Ã©levÃ© ({wer:.2f})."
    else:
        status, reason = "ok", ""
    return {
        "status": status,
        "reason": reason,
        "wer": round(wer, 3),
        "extras": extras,
        "coverage": round(coverage, 3),
        "precision": round(matches / max(1, len(heard)), 3),
    }
