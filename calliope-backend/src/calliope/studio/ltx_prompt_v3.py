"""Compilateur LTX-2.5 natif : prose fluide, dialogue entre guillemets.

Règles appliquées (documentation officielle LTX-2.5) :
- un seul paragraphe visuel, au présent ;
- les répliques sont entre guillemets, avec le locuteur et la langue en prose ;
- aucun timing absolu, aucune balise H3, aucune clé de blocking brute ;
- pas de répétition du staging ;
- longueur cible 180 mots, plafond dur 200 mots.

Le compilateur est volontairement déterministe : aucune dépendance au LLM.
"""

from __future__ import annotations

import re
import unicodedata

from .h3_prompt import (
    dialogue_language,
    directing_profile,
    spoken_dialogue,
    validate_h3_prose,
)
from .visual_identity import strip_generated_text, visual_description

LTX_PROMPT_VERSION = 3
LTX_PROMPT_TARGET_WORDS = 180
LTX_PROMPT_MAX_WORDS = 200

_FORBIDDEN = (
    (re.compile(r"<\s*/?\s*d\s*>", re.I), "balises H3 <d>"),
    (re.compile(r"\[\s*(?:French|English|German|Spanish|Italian|Portuguese|"
                r"Japanese|Korean|Chinese|Russian|Arabic)\s*\]", re.I),
     "tag de langue H3"),
    (re.compile(r"\bSPOKEN LINES\b", re.I), "bloc SPOKEN LINES"),
    (re.compile(r"\bVisual timing\b", re.I), "bloc Visual timing"),
    (re.compile(r"\bnot spoken\b", re.I), "négation not spoken"),
    (re.compile(r"\bspoken between\b", re.I), "timing absolu"),
    (re.compile(r"\bAt \d+(?:\.\d+)? seconds\b", re.I), "temps absolu"),
    (re.compile(r"\b(?:positions|eyelines)\s*:", re.I), "clé de blocking brute"),
    (re.compile(r";\s*looking\b", re.I), "clé looking brute"),
)


def lint_ltx_prompt(prompt: str) -> str:
    """Refuse un prompt non conforme avant l'envoi au GPU."""
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("Prompt LTX vide.")
    for pattern, label in _FORBIDDEN:
        if pattern.search(text):
            raise ValueError(f"Prompt LTX non conforme : {label} interdit.")
    words = len(text.split())
    if words > LTX_PROMPT_MAX_WORDS:
        raise ValueError(
            f"Prompt LTX trop long : {words} mots (plafond {LTX_PROMPT_MAX_WORDS})."
        )
    if text.count('"') % 2:
        raise ValueError("Prompt LTX non conforme : guillemets non appariés.")
    return text


def _sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", str(value or ""))
            if part.strip()]


def _unique_sentences(*values: str) -> str:
    """Fusionne plusieurs prose en supprimant les phrases déjà vues."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for sentence in _sentences(value):
            key = sentence.casefold()
            if key not in seen:
                seen.add(key)
                result.append(sentence)
    return " ".join(result)


def _fold_accents(value: str) -> str:
    """Retire les accents pour comparer des descriptions sans dependance d'encodage."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def _voice_label(description: str) -> str:
    """Traduit une description vocale en indication LTX courte.

    Les nuances sont testees de la plus precise a la plus generale : une voix
    "legerement plus grave" ne doit pas devenir "low, raspy" comme une voix
    "tres grave, rauque", sinon l'identite vocale des personnages se confond.
    """
    text = _fold_accents(str(description or "").casefold())
    if any(word in text for word in (
        "legerement plus grave", "un peu plus grave",
        "plus adulte", "moins enfantine", "moins enfantin",
    )):
        return "slightly lower, more mature"
    if "medium-grave" in text:
        return "warm, medium-low"
    if any(word in text for word in ("tres grave", "rauque", "profond")):
        return "low, raspy"
    if any(word in text for word in ("aigu", "nasillard", "nasillarde")):
        return "high, nasal"
    if any(word in text for word in ("calme", "pose")):
        return "calm, measured"
    if any(word in text for word in ("trembl", "nerveux", "nerveuse")):
        return "nervous, trembling"
    if any(word in text for word in ("deep", "low", "masculine")):
        return "low, resonant"
    if "grave" in text:
        return "low"
    if any(word in text for word in ("bright", "young")):
        return "bright, youthful"
    if any(word in text for word in ("warm", "feminine")):
        return "warm, feminine"
    return "natural"


def _append_visual(parts: list[dict], value: str, optional: bool, seen: set[str]) -> None:
    """Ajoute les phrases visuelles encore absentes, une seule fois."""
    for sentence in _sentences(value):
        key = sentence.casefold()
        if key and key not in seen:
            seen.add(key)
            parts.append({"text": sentence, "optional": optional})



def compile_ltx_prompt(shot, context, first_frame):
    """Compile une prise LTX en une prose unique, sans timings ni tags H3."""
    context = context or {}
    shot = (context.get("shot") or {}) | (shot or {})
    labels = shot.get("screen_text") or []
    canon = context.get("render_canon") or {}
    characters = canon.get("characters") or {
        entity["key"]: {
            "name": entity.get("name") or entity["key"],
            "appearance": visual_description(entity),
            "voice": entity.get("voice_description") or "",
        }
        for entity in context.get("entities", [])
        if entity.get("kind", "character") == "character"
    }

    def prose(value):
        return strip_generated_text(validate_h3_prose(value), labels).strip()

    dialogue = spoken_dialogue(shot, context)
    turns = list(shot.get("dialogue_turns") or [])
    if dialogue and not turns:
        turns = [{"speaker": shot.get("speaker"), "text": dialogue}]

    def name_for(key):
        character = characters.get(key) or {}
        return prose(character.get("name") or key or "The visible character")

    cast = list(dict.fromkeys(
        list(shot.get("character_keys") or [])
        + [turn.get("speaker") for turn in turns if turn.get("speaker")]
    ))
    seen: set[str] = set()

    parts: list[dict] = [{"text": "A continuous cinematic shot.", "optional": True}]
    if first_frame:
        parts.append({
            "text": "Continue the supplied opening image's characters, setting and lighting.",
            "optional": False,
        })
        for key in cast:
            appearance = prose((characters.get(key) or {}).get("appearance") or "")
            if appearance:
                short = " ".join(appearance.split()[:18]).rstrip(".,;")
                if short:
                    parts.append({
                        "text": f"{name_for(key)} is {short}.",
                        "optional": True,
                    })
        if cast:
            parts.append({
                "text": (
                    "Keep these exact characters; do not add or duplicate characters."
                ),
                "optional": True,
            })
    else:
        style = canon.get("style") or context.get("bible", {}).get("visual_style")
        if style:
            parts.append({"text": prose(style), "optional": True})
        for key in cast:
            character = characters.get(key)
            appearance = prose((character or {}).get("appearance") or "")
            if appearance:
                parts.append({"text": f"{name_for(key)} is {appearance}.", "optional": True})
        location = shot.get("location_key")
        if location and location in (canon.get("locations") or {}):
            parts.append({"text": prose(canon["locations"][location]), "optional": True})

    _append_visual(
        parts,
        _unique_sentences(
            prose(shot.get("action") or ""),
            prose(shot.get("prompt") or ""),
        ),
        False,
        seen,
    )
    for field in ("framing", "camera"):
        if shot.get(field):
            _append_visual(parts, prose(shot[field]), True, seen)

    if dialogue:
        language = dialogue_language(shot.get("language") or context.get("language"))
        announced: set[str] = set()
        for index, turn in enumerate(turns):
            speaker = turn.get("speaker")
            _append_visual(parts, prose(turn.get("action") or ""), True, seen)
            cue = ""
            if speaker not in announced:
                character = characters.get(speaker) or {}
                voice = _voice_label(character.get("voice") or "")
                cue = f" in a {voice} {language} voice"
                announced.add(speaker)
            text = validate_h3_prose(turn.get("text") or "").replace('"', "'")
            verb = "says" if index == 0 else "answers"
            parts.append({
                "text": f'{name_for(speaker)} {verb}{cue}: "{text}"',
                "optional": False,
            })
            _append_visual(parts, prose(turn.get("reaction") or ""), True, seen)
        parts.append({
            "text": (
                "Only the named speaker speaks; every other character stays "
                "silent with their mouth closed."
            ),
            "optional": False,
        })
        parts.append({
            "text": "The voice stays in the foreground above the ambience.",
            "optional": False,
        })
        parts.append({
            "text": "Speak each quoted line exactly once, in order.",
            "optional": False,
        })
    else:
        parts.append({
            "text": (
                "The characters remain silent, expressing the action "
                "through gestures and posture."
            ),
            "optional": False,
        })

    profile = directing_profile(context.get("directing_profile"))
    if dialogue and (
        profile["dialogue_density"] == "brisk" or profile["silences"] == "brief"
    ):
        parts.append({
            "text": "Each reply starts as the previous line ends, with brief natural breaths.",
            "optional": False,
        })
        parts.append({
            "text": "Perform the described gestures while speaking.",
            "optional": False,
        })
        parts.append({
            "text": "Use facial expressions and small motivated movements.",
            "optional": True,
        })

    _append_visual(parts, prose(shot.get("reaction") or ""), True, seen)
    if shot.get("audio_mode", "native") == "silent":
        parts.append({"text": "The shot is silent.", "optional": True})
    else:
        soundscape = prose(shot.get("soundscape") or "")
        if soundscape and soundscape.casefold() != "n/a":
            parts.append({"text": f"Ambient sound: {soundscape}", "optional": False})
    if labels:
        parts.append({"text": "No on-screen text, captions or subtitles.", "optional": False})
    if first_frame:
        parts.append({
            "text": "Do not re-describe the opening image; show only the new motion.",
            "optional": True,
        })

    parts = _limit_words(parts, LTX_PROMPT_TARGET_WORDS)
    prompt = re.sub(r"\s+", " ", " ".join(part["text"] for part in parts)).strip()
    return prompt


def _limit_words(parts: list[dict], maximum: int) -> list[dict]:
    """Retire les éléments optionnels les plus récents jusqu'à la cible."""
    while sum(len(part["text"].split()) for part in parts) > maximum:
        optional = [index for index, part in enumerate(parts) if part["optional"]]
        if not optional:
            break
        parts.pop(optional[-1])
    total = sum(len(part["text"].split()) for part in parts)
    if total > maximum:
        raise ValueError(
            f"Prompt LTX trop long même après réduction : {total} mots "
            f"(plafond {maximum})."
        )
    return parts
