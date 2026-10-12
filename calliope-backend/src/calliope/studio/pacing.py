"""Rythme de montage : fenêtre utile par prise et validation tolérante des dialogues.

H3 génère librement (au minimum ~5,17 s). Le montage ne conserve que la partie
utile de chaque prise, exprimée sur la même grille temporelle 24 fps que la
vidéo et que les départs de SFX. Aucun plan n'est rallongé artificiellement :
si la prise est plus courte que le beat écrit, elle est conservée telle quelle.

La validation des dialogues ne signale que les échecs manifestes (réplique
absente, invention importante, audio inexploitable). Un début de parole tardif,
un silence court ou quelques images inutiles sont rattrapés par le trim, jamais
par un rerender GPU.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess

GRID_FPS = 24
GRID = 1 / GRID_FPS

# Fenêtre utile autour de la parole détectée.
PRE_ROLL_SEC = 0.2
POST_ROLL_SEC = 0.35
ASIDE_PRE_ROLL_SEC = 2 / GRID_FPS
ASIDE_POST_ROLL_SEC = 3 / GRID_FPS
VOICE_MIN_SEC = 0.06
MERGE_GAP_SEC = 0.35

# Activité sonore par seuil de silence (ce n'est pas une reconnaissance de parole).
SILENCE_DB = -33.0
MIN_SILENCE_SEC = 0.28

# Validation tolérante des dialogues : seuils d'échecs manifestes uniquement.
MIN_HEARD_SEC = 0.15
SILENT_FLOOR_DB = -48.0

_PROCESS_OPTIONS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
_MAX_VOLUME = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def frames(seconds: float) -> int:
    """Nombre de frames entières le plus proche d'une durée en secondes."""
    return max(0, int(math.floor(float(seconds) * GRID_FPS + 0.5)))


def quantize(seconds: float) -> float:
    """Arrondit une durée à la frame la plus proche sur la grille 24 fps."""
    return frames(seconds) / GRID_FPS


def floor_grid(seconds: float) -> float:
    """Plus grande durée de la grille qui ne dépasse pas la durée réelle."""
    return math.floor(float(seconds) * GRID_FPS + 1e-6) / GRID_FPS


def detect_voice(path, probe_sec: float = 0.0) -> dict | None:
    """Intervalles d'activité sonore et niveau max, mesurés localement.

    Retourne None quand la mesure est impossible : dans le doute, jamais de
    rejet (principe permissif). Retourne sinon {"intervals": [(début, fin)],
    "max_db": float | None}.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not path or not os.path.isfile(str(path)):
        return None
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={SILENCE_DB}dB:d={MIN_SILENCE_SEC},volumedetect",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=180, **_PROCESS_OPTIONS)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    log = result.stderr.decode("utf-8", "replace")
    starts = [float(m.group(1)) for m in _SILENCE_START.finditer(log)]
    ends = [float(m.group(1)) for m in _SILENCE_END.finditer(log)]
    maximum = _MAX_VOLUME.search(log)
    max_db = float(maximum.group(1)) if maximum else None
    duration = float(probe_sec or 0)
    if duration <= 0 and ends:
        duration = ends[-1]
    if duration <= 0:
        stamps = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", log)
        if stamps:
            h, m, s = stamps[-1]
            duration = int(h) * 3600 + int(m) * 60 + float(s)
    if duration <= 0:
        return None
    silences, cursor = [], 0.0
    for index, begin in enumerate(starts):
        begin = max(min(begin, duration), cursor)
        end = ends[index] if index < len(ends) else duration
        silences.append((begin, max(begin, min(end, duration))))
        cursor = silences[-1][1]
    intervals, cursor = [], 0.0
    for begin, end in silences:
        if begin - cursor >= VOICE_MIN_SEC:
            intervals.append((cursor, begin))
        cursor = max(cursor, end)
    if duration - cursor >= VOICE_MIN_SEC:
        intervals.append((cursor, duration))
    merged = []
    for begin, end in intervals:
        if merged and begin - merged[-1][1] < MERGE_GAP_SEC:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    return {"intervals": merged, "max_db": max_db}


def useful_window(beat_sec, probe_sec, voice=None, *, preserve_end_sec=None) -> tuple[float, float]:
    """Fenêtre utile (début, fin) d'une prise, en secondes, sur la grille 24 fps.

    beat_sec est la durée utile visée par le scénario ; une valeur nulle garde
    la prise entière. La fin ne dépasse jamais la durée réelle de la prise et
    une parole détectée n'est jamais coupée.
    """
    probe = max(0.0, float(probe_sec))
    available = int(math.floor(probe * GRID_FPS + 1e-6))
    if available <= 0 or not beat_sec or float(beat_sec) <= 0:
        return 0.0, probe
    target = max(1, frames(float(beat_sec)))
    if available <= target:
        # Prise déjà à la bonne taille ou plus courte : aucun padding.
        return 0.0, probe
    segments = [
        (float(b), float(e)) for b, e in (voice or []) if float(e) - float(b) >= VOICE_MIN_SEC
    ]
    if segments:
        onset = segments[0][0]
        start = max(0, int(math.floor((onset - PRE_ROLL_SEC) * GRID_FPS)))
        start = max(0, min(start, available - target))
        end = start + target
        if segments[-1][1] > end * GRID:
            # La parole déborde du beat écrit : on la conserve entière.
            end = min(available, int(math.ceil(segments[-1][1] * GRID_FPS - 1e-9)))
    else:
        start, end = 0, target
    if preserve_end_sec is not None:
        end = min(available, max(end, math.ceil(float(preserve_end_sec) * GRID_FPS)))
    return start / GRID_FPS, end / GRID_FPS


def explicit_window(trim_start, trim_end, probe_sec) -> tuple[float, float]:
    """Points d'entrée/sortie choisis explicitement, alignés sur la grille.

    Les bornes sont ramenées dans la prise ; une fenêtre invalide est une
    erreur de configuration manifeste et doit être signalée.
    """
    probe = max(0.0, float(probe_sec))
    start = 0.0 if trim_start is None else quantize(max(0.0, float(trim_start)))
    end = floor_grid(probe) if trim_end is None else quantize(float(trim_end))
    end = min(end, floor_grid(probe))
    if probe <= 0 or start + GRID > end + 1e-9:
        raise ValueError(
            "Points de coupe invalides pour cette prise : l'entrée doit précéder"
            " la sortie, dans la durée réelle du clip."
        )
    if start <= 1e-9 and end >= floor_grid(probe) - 1e-9:
        return 0.0, probe
    return start, end


def speech_window(probe_sec, speech_intervals, post_roll_sec=POST_ROLL_SEC, *, pre_roll_sec=PRE_ROLL_SEC):
    probe = max(0.0, float(probe_sec))
    segments = list(speech_intervals or [])
    if not segments:
        return 0.0, probe
    start = max(0.0, floor_grid(min(float(b) for b, _ in segments) - max(0.0, pre_roll_sec)))
    end = min(probe, math.ceil(
        (max(float(e) for _, e in segments) + max(0.0, float(post_roll_sec))) * GRID_FPS
    ) / GRID_FPS)
    if start >= end:
        return 0.0, probe
    return start, end


def assess_dialogue(dialogue, beat_sec, probe_sec, voice) -> dict:
    """Audibilité seulement; le contenu des mots exige une transcription."""
    words = len(str(dialogue or "").split())
    if not words:
        return {"status": "ok", "reason": ""}
    if voice is None:
        return {"status": "unknown", "reason": "Mesure audio indisponible."}
    heard = sum(end - start for start, end in voice["intervals"])
    if voice.get("max_db") is not None and voice["max_db"] < SILENT_FLOOR_DB:
        return {
            "status": "inaudible",
            "reason": "Audio inexploitable : aucun niveau sonore mesurable.",
        }
    if heard < MIN_HEARD_SEC:
        return {
            "status": "missing",
            "reason": "Réplique attendue absente de la piste audio.",
        }
    return {
        "status": "unknown",
        "reason": "Activité sonore mesurée; mots non vérifiés sans transcription.",
    }
