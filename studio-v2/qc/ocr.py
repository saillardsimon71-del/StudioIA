"""Contrôle OCR des textes parasites extrait de V1 (`studio/generated_text.py`).

Politique V2 :
- Prise native : aucun sous-titre parasite incrusté par LTX.
- Export avec `DisplayOverlay` : les valeurs déclarées (ex. `000`, `001`) et leurs
  variantes OCR 7-segments (`0/O`, `1/l/I`) forment la liste blanche autorisée.
- 1 frame suspecte ou OCR absent -> avertissement (`unverified`), revue humaine, pas de blocage.
- >= 2 frames avec texte parasite nouveau -> signalé (`unexpected_text`) pour revue/reprise.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import cv2

_SEGMENT_CONFUSIONS = str.maketrans({"o": "0", "l": "1", "i": "1"})


def _parse_tsv_words(tsv: str) -> list[str]:
    found: list[str] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if (
            row.get("level") == "5"
            and float(row.get("conf") or -1) >= 80
            and sum(ch.isalnum() for ch in text) >= 3
        ):
            found.append(text)
    return found


def _fold(word: str) -> str:
    folded = unicodedata.normalize("NFKD", str(word or ""))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", folded.casefold())


def token_variants(token: str) -> set[str]:
    norm = _fold(token)
    if not norm:
        return set()
    forms = {norm}
    if re.fullmatch(r"[0-9]{1,8}", norm):
        forms.add(norm.lstrip("0") or "0")
    if re.fullmatch(r"[0-9oli]{1,8}", norm):
        mapped = norm.translate(_SEGMENT_CONFUSIONS)
        forms.add(mapped)
        forms.add(mapped.lstrip("0") or "0")
    return forms


def build_allowed_forms(allowed_values: list[str] | tuple[str, ...] = ()) -> set[str]:
    forms: set[str] = set()
    for item in allowed_values:
        for tok in re.split(r"[^A-Za-z0-9]+", str(item or "")):
            forms |= token_variants(tok)
        for num in re.findall(r"\d+", str(item or "")):
            forms |= token_variants(num)
    return {f for f in forms if f}


def _sample_video_words(video_path: Path, tesseract_bin: str) -> list[list[str]]:
    cap = cv2.VideoCapture(str(video_path))
    try:
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if frames < 2:
            raise ValueError("Nombre de frames insuffisant pour l'échantillonnage OCR.")
        count = min(frames, 8)
        samples: list[list[str]] = []
        for idx in range(count):
            pos = round(idx * (frames - 1) / (count - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if not ok:
                raise ValueError(f"Frame {pos} illisible.")
            h, w = frame.shape[:2]
            if h > 960:
                frame = cv2.resize(frame, (round(w * 960 / h), 960))
            ok_enc, png = cv2.imencode(".png", frame)
            if not ok_enc:
                raise ValueError("Encodage PNG OCR impossible.")
            proc = subprocess.run(
                [tesseract_bin, "stdin", "stdout", "--psm", "11", "tsv"],
                input=png.tobytes(),
                capture_output=True,
                check=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            samples.append(_parse_tsv_words(proc.stdout.decode("utf-8", errors="replace")))
        return samples
    finally:
        cap.release()


def scan_parasite_text(
    video_path: Path, allowed_values: list[str] | tuple[str, ...] = ()
) -> dict:
    """Analyse OCR d'une vidéo avec distinction texte de décor / chiffres déclarés / sous-titre parasite."""
    binary = shutil.which("tesseract")
    if not binary:
        return {
            "status": "unverified",
            "sampled_frames": 0,
            "hits": [],
            "reason": "Tesseract non détecté dans le PATH : contrôle visuel humain requis.",
        }

    try:
        samples = _sample_video_words(Path(video_path), binary)
    except Exception as exc:
        return {
            "status": "unverified",
            "sampled_frames": 0,
            "hits": [],
            "reason": f"OCR indisponible ({type(exc).__name__}) : contrôle visuel humain requis.",
        }

    permitted = build_allowed_forms(allowed_values)
    baseline: set[str] = set()
    for word in samples[0]:
        baseline |= token_variants(word)

    def _is_tolerated(word: str) -> bool:
        vars_ = token_variants(word)
        return bool(vars_ & permitted) or bool(vars_ & baseline)

    hits = [
        {"sample": idx, "words": [w for w in frame_words if not _is_tolerated(w)]}
        for idx, frame_words in enumerate(samples[1:], start=1)
    ]
    hits = [h for h in hits if h["words"]]

    if len(hits) >= 2:
        status = "unexpected_text"
        reason = "Texte/sous-titre parasite détecté sur plusieurs frames."
    elif hits:
        status = "unverified"
        reason = "Texte isolé sur une frame : avertissement pour revue humaine."
    else:
        status = "not_detected"
        reason = "Aucun texte parasite détecté sur les frames échantillonnées."

    return {
        "status": status,
        "sampled_frames": len(samples),
        "hits": hits,
        "allowed_count": len(permitted),
        "reason": reason,
    }
