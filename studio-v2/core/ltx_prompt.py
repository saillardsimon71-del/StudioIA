"""Préparation déterministe et validation stricte des prompts LTX-2.5.

Règles V2 :
- Source unique : `Project` + `Shot` (`core/scenario.py`).
- Prose visuelle au présent, répliques françaises entre guillemets avec locuteur.
- Garde-fou local : **300 mots maximum**, sans tronquer les décisions du réalisateur.
- Si le prompt dépasse ce garde-fou ou viole les contraintes moteur : arrêt immédiat et
  message explicite AVANT tout appel GPU. Aucune chaîne de réparation automatique.
- Si `shot.display_overlay` est défini, l'écran/zone réservée reste vierge dans LTX
  (les chiffres exacts sont ajoutés au montage FFmpeg).
"""

from __future__ import annotations

import re

from core.scenario import Character, Project, Shot
from core.storyboard import visual_style

MAX_LTX_PROMPT_WORDS = 300

_FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<\s*/?\s*d\s*>", re.IGNORECASE), "balise H3 <d> interdite"),
    (
        re.compile(
            r"\[\s*(?:French|English|German|Spanish|Italian|Portuguese)\s*\]",
            re.IGNORECASE,
        ),
        "tag de langue entre crochets interdit",
    ),
    (re.compile(r"\bSPOKEN LINES\b", re.IGNORECASE), "bloc SPOKEN LINES interdit"),
    (re.compile(r"\bVisual timing\b", re.IGNORECASE), "bloc Visual timing interdit"),
    (
        re.compile(r"\bAt \d+(?:\.\d+)? seconds\b", re.IGNORECASE),
        "horodatage absolu interdit dans le prompt LTX",
    ),
    (
        re.compile(r"\b(?:subtitle|subtitles|sous-titre|sous-titres)\b", re.IGNORECASE),
        "mention de sous-titres interdite dans le prompt positif (risque d'incrustation LTX)",
    ),
)


class PromptCapacityError(ValueError):
    """Erreur levée avant GPU lorsqu'un prompt dépasse les capacités du moteur LTX."""


def count_words(text: str) -> int:
    return len(str(text or "").strip().split())


def _clean_sentence(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _voice_cue(voice_hint: str) -> str:
    hint = str(voice_hint or "").strip().lower()
    if not hint:
        return "natural French voice"
    return f"{hint} French voice"


def validate_ltx_prompt(prompt: str, shot_id: str = "") -> dict:
    """Vérifie strictement un prompt LTX avant tout appel GPU (zéro réparation)."""
    prefix = f"Plan « {shot_id} » — " if shot_id else ""
    text = re.sub(r"\s+", " ", str(prompt or "").strip())
    if not text:
        raise PromptCapacityError(f"{prefix}Arrêt avant GPU : le prompt LTX est vide.")

    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            raise PromptCapacityError(f"{prefix}Arrêt avant GPU : {label}.")

    if text.count('"') % 2 != 0:
        raise PromptCapacityError(
            f"{prefix}Arrêt avant GPU : guillemets doubles non appariés dans le prompt LTX."
        )

    words = count_words(text)
    if words > MAX_LTX_PROMPT_WORDS:
        raise PromptCapacityError(
            f"{prefix}Arrêt avant GPU : le prompt LTX fait {words} mots, au-delà de la "
            f"limite prudente de {MAX_LTX_PROMPT_WORDS} mots. Raccourcissez l'action, "
            "les états ou les dialogues avant de lancer le rendu."
        )

    return {"ok": True, "word_count": words, "max_words": MAX_LTX_PROMPT_WORDS, "prompt": text}


def compile_shot_prompt(project: Project, shot: Shot) -> str:
    """Construit le prompt LTX-2.5 d'un plan depuis `Project` et `Shot`."""
    char_map: dict[str, Character] = {c.id: c for c in project.characters}
    has_visual_ref = bool(shot.image_ref or shot.continue_from_previous)

    speaking_ids = [line.speaker_id for line in shot.dialogue if line.delivery == "on_screen"]
    mentioned_ids: list[str] = []
    haystack = f"{shot.action} {shot.initial_state} {shot.final_state}".casefold()
    for char in project.characters:
        if (
            char.id in speaking_ids
            or re.search(r"\b(?:" + re.escape(char.id) + "|" + re.escape(char.name) + r")\b", haystack, re.I)
        ):
            mentioned_ids.append(char.id)
    cast_ids = list(dict.fromkeys(mentioned_ids))

    parts: list[str] = []
    if has_visual_ref:
        parts.append(f"{project.aspect_ratio} {visual_style(project)} shot continuing directly from the reference frame.")
        for cid in cast_ids:
            char = char_map.get(cid)
            if char:
                parts.append(f"{char.name} ({char.description.strip()}).")
        if cast_ids:
            parts.append("Keep exact characters and setting without duplicating anyone.")
    else:
        parts.append(f"{project.aspect_ratio} {visual_style(project)} shot.")
        for cid in cast_ids:
            char = char_map.get(cid)
            if char:
                parts.append(_clean_sentence(f"{char.name} is {char.description.strip()}"))

    if shot.camera_setup:
        parts.append(_clean_sentence(f"Starting composition: {shot.camera_setup}"))

    if shot.initial_state:
        parts.append(_clean_sentence(f"Initial state: {shot.initial_state}"))

    parts.append(_clean_sentence(shot.action))

    if shot.dialogue:
        # `start_sec` remains planning/preflight metadata. LTX receives relative
        # beats through `action`, not raw timestamps.
        announced: set[str] = set()
        for idx, line in enumerate(shot.dialogue):
            speaker = char_map.get(line.speaker_id)
            speaker_name = speaker.name if speaker else line.speaker_id
            spoken = line.text.replace('"', "'").strip()
            verb = "says" if idx == 0 else "replies"
            if line.delivery != "on_screen":
                cue = _voice_cue(speaker.voice_hint if speaker else "")
                kind = "Voice-over" if line.delivery == "voice_over" else "Off-screen voice"
                parts.append(f'{kind}, {speaker_name}, {cue}: "{spoken}"')
                continue
            if line.speaker_id not in announced:
                cue = _voice_cue(speaker.voice_hint if speaker else "")
                parts.append(f'{speaker_name} {verb} in a {cue}: "{spoken}"')
                announced.add(line.speaker_id)
            else:
                parts.append(f'{speaker_name} {verb}: "{spoken}"')
        if cast_ids:
            parts.append("Only the named on-screen speaker lip-syncs; other characters may react silently.")
    else:
        parts.append("Characters stay silent with expressive physical reactions." if cast_ids else "No spoken dialogue.")

    if shot.final_state:
        parts.append(_clean_sentence(f"End state: {shot.final_state}"))

    if shot.display_overlay is not None:
        parts.append(
            "The reserved display screen stays completely blank and dark with zero digits or letters."
        )

    prompt = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
    validate_ltx_prompt(prompt, shot_id=shot.id)
    return prompt


def prepare_project_prompts(project: Project, overwrite: bool = True) -> dict[str, dict]:
    """Prépare et valide les prompts LTX de tous les plans d'un projet."""
    report: dict[str, dict] = {}
    for shot in project.shots:
        if overwrite or not shot.ltx_prompt.strip():
            shot.ltx_prompt = compile_shot_prompt(project, shot)
        info = validate_ltx_prompt(shot.ltx_prompt, shot_id=shot.id)
        report[shot.id] = info
    return report
