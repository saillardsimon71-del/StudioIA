"""Incrustations de compte à rebours calculées sur la timeline globale du film.

Aucun compteur n'est ajouté sans demande explicite dans le brief. Les textes générés
réutilisent ensuite le mécanisme existant `display_overlay` / ASS de l'export.
"""

from __future__ import annotations

import math
import re
import unicodedata

from core.scenario import DisplayChange, DisplayOverlay, Project

_COUNTDOWN = re.compile(
    r"\b(?:compte\s+a\s+rebours|decompte|count\s*down|countdown)\b", re.I,
)
_NEGATED_COUNTDOWN = re.compile(
    r"\b(?:sans|pas\s+de|aucun(?:e)?|no|without)\W{0,18}"
    r"(?:compte\s+a\s+rebours|decompte|count\s*down|countdown)\b", re.I,
)
_NUMBER = re.compile(r"(?<!\w)(\d{1,3})(?!\w)")
_DURATION = re.compile(
    r"(?:compte\s+a\s+rebours|decompte|count\s*down|countdown)"
    r"[^.\n]{0,40}?(\d{1,3})\s*(?:s\b|sec(?:onde)?s?\b)", re.I,
)
_REVERSE_DURATION = re.compile(
    r"\b(\d{1,3})\s*(?:s\b|sec(?:onde)?s?\b)[^.\n]{0,30}?"
    r"(?:compte\s+a\s+rebours|decompte|count\s*down|countdown)\b", re.I,
)
_BLACK = re.compile(
    r"\b(?:black screen|pure black|full black|ecran noir|image noire|fond noir)\b", re.I,
)


def countdown_requested(idea: str) -> bool:
    """N'active le compteur que pour une demande explicite et non annulée."""
    normalized = _normalize(idea)
    return bool(_COUNTDOWN.search(normalized) and not _NEGATED_COUNTDOWN.search(normalized))


def _normalize(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", str(text or "").casefold())
        if not unicodedata.combining(char)
    )


def _number_from_overlay(text: str) -> int | None:
    match = _NUMBER.fullmatch(text.strip())
    return int(match.group(1)) if match else None


def _black_final_shot(project: Project) -> bool:
    if not project.shots:
        return False
    final = project.shots[-1]
    # The initial state confirms black is present for the whole final take. A
    # final-state-only change is not enough to hide a counter earlier in a take.
    return bool(_BLACK.search(_normalize(final.initial_state)))


def countdown_overlays(
    project: Project, durations_s: list[float] | None = None,
) -> list[DisplayOverlay | None] | None:
    """Return deterministic per-shot overlays, or None when the brief has no countdown.

    Timer values are derived from elapsed global seconds, not reset at cuts. A
    nonnumeric opening overlay is retained as a three-second hook; its counter
    then shows the value appropriate to elapsed film time. A final black take is
    left free of overlays, so no artificial zero is forced before the cliffhanger.
    """
    if not countdown_requested(project.idea) or not project.shots:
        return None
    durations = [shot.duration_s for shot in project.shots] if durations_s is None else durations_s
    if len(durations) != len(project.shots) or any(
        duration <= 0 or not math.isfinite(duration) for duration in durations
    ):
        raise ValueError("Durées invalides pour calculer la timeline du compte à rebours.")

    existing = [shot.display_overlay for shot in project.shots]
    first_numeric = next(
        (value for overlay in existing if overlay
         for value in [overlay.initial_text, *(change.text for change in overlay.changes)]
         if _number_from_overlay(value) is not None),
        None,
    )
    normalized_idea = _normalize(project.idea)
    duration_match = _DURATION.search(normalized_idea) or _REVERSE_DURATION.search(normalized_idea)
    start_value = (
        int(duration_match.group(1)) if duration_match else
        _number_from_overlay(first_numeric) if first_numeric is not None else
        max(1, math.ceil(project.target_duration_s))
    )

    style = next((overlay for overlay in existing if overlay is not None), None)
    style_fields = {
        "x": style.x if style else 0.5,
        "y": style.y if style else 0.14,
        "font_size": style.font_size if style else 0.05,
        "color": style.color if style else "00FF88",
        "box_bg": style.box_bg if style else "101418",
    }
    first_overlay = existing[0]
    opening_hook = None
    hook_end = 0.0
    if (
        first_overlay
        and _number_from_overlay(first_overlay.initial_text) is None
        and first_overlay.initial_text.strip()
    ):
        opening_hook = first_overlay.initial_text
        hook_end = min(durations[0], 3.0)
        if first_overlay.changes and first_overlay.changes[0].at_sec > 0:
            hook_end = min(durations[0], first_overlay.changes[0].at_sec)

    starts: list[float] = []
    elapsed = 0.0
    for duration in durations:
        starts.append(elapsed)
        elapsed += duration
    deadline = starts[-1] if _black_final_shot(project) else elapsed

    overlays: list[DisplayOverlay | None] = []
    for index, (shot, clip_start, duration) in enumerate(zip(project.shots, starts, durations)):
        if index == len(project.shots) - 1 and _black_final_shot(project):
            overlays.append(None)
            continue
        clip_end = min(clip_start + duration, deadline)
        local_hook_end = (
            max(0.0, min(hook_end - clip_start, duration))
            if opening_hook and index == 0 else 0.0
        )

        def label(global_time: float) -> str:
            remaining = math.ceil(start_value - global_time - 1e-9)
            return str(remaining) if remaining > 0 else ""

        if clip_start >= deadline:
            overlays.append(None)
            continue
        initial = opening_hook if local_hook_end > 0 else label(clip_start)
        changes_by_time: dict[float, str] = {}
        if local_hook_end > 0 and local_hook_end < duration:
            changes_by_time[local_hook_end] = label(clip_start + local_hook_end)
        # Decrement at every elapsed whole second; non-integral clip boundaries
        # naturally carry their fractional remainder into the next shot.
        first_tick = math.floor(clip_start) + 1
        for tick in range(first_tick, math.ceil(clip_end)):
            if clip_start < tick < clip_end:
                if opening_hook and index == 0 and tick < hook_end:
                    continue
                local_time = round(tick - clip_start, 6)
                changes_by_time[local_time] = label(float(tick))
        changes = [
            DisplayChange(at_sec=at, text=text)
            for at, text in sorted(changes_by_time.items())
            if 0 < at < duration
        ]
        overlays.append(DisplayOverlay(initial_text=initial, changes=changes, **style_fields))
    return overlays
