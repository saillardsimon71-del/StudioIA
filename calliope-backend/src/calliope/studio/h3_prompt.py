import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DirectingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["generalist", "fast_dialogue_social_fiction"] = "generalist"
    framing: Literal["story_driven", "close_preferred"] = "story_driven"
    camera: Literal["scene_driven", "static", "subtle"] = "scene_driven"
    dialogue_density: Literal["natural", "brisk"] = "natural"
    reactions: Literal["story_driven", "immediate"] = "story_driven"
    active_listening: bool = True
    continuity: bool = True
    silences: Literal["story_driven", "brief"] = "story_driven"
    music: Literal["postproduction", "none"] = "postproduction"
    music_style: Literal["neutral", "tension", "warm", "playful", "energetic", "horror"] = "neutral"
    music_gain_db: float = Field(default=-18, ge=-60, le=0)
    ambience_gain_db: float = Field(default=-34, ge=-60, le=0)
    number_format: Literal["plain", "grouped_comma", "grouped_space"] = "grouped_comma"

    @model_validator(mode="before")
    @classmethod
    def preset_defaults(cls, value):
        if isinstance(value, dict) and value.get("preset") == "fast_dialogue_social_fiction":
            return {
                "framing": "close_preferred",
                "camera": "subtle",
                "dialogue_density": "brisk",
                "reactions": "immediate",
                "silences": "brief",
                "music_style": "tension",
            } | value
        return value


def directing_profile(value=None):
    return DirectingProfile.model_validate(value or {}).model_dump()


# MiniMax-H3 official model card, consulted 2026-10-08: the eleven stable languages.
# https://huggingface.co/MiniMaxAI/MiniMax-H3#system-overview
LANGUAGES = {
    "ar": "Arabic",
    "zh": "Chinese",
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pt": "Portuguese",
    "ru": "Russian",
    "es": "Spanish",
}


def dialogue_language(value):
    normalized = str(value or "en").strip().lower().replace("_", "-")
    label = LANGUAGES.get(normalized.split("-")[0])
    if not label:
        label = next((name for name in LANGUAGES.values() if name.lower() == normalized), None)
    if not label:
        raise ValueError(f"Langue de dialogue H3 non configurée dans StudioIA : {value}.")
    return label


def validate_h3_prose(value):
    text = str(value)
    if re.search(r"<\s*/?\s*d\s*>", text, flags=re.I):
        raise ValueError(
            "Les directives et le canon ne doivent pas contenir de balises de parole H3."
        )
    return text


def _speaker_identity(shot, context):
    speaker = str(shot.get("speaker") or context.get("speaker") or "").strip()
    entity = next((item for item in context.get("entities", []) if item.get("key") == speaker), {})
    name = str(entity.get("name") or speaker or "The speaking character")
    dialogue = str(shot.get("dialogue") or "").strip()
    prefix = re.match(r"^([^:\n]{1,80}):\s*", dialogue)
    if prefix:
        legacy_name = prefix[1].strip()
        known_names = {
            str(value or "").strip().casefold() for value in (speaker, entity.get("name"))
        }
        prompt = re.sub(r"<d>.*?</d>", "", str(shot.get("prompt") or ""), flags=re.S | re.I)
        identified = legacy_name.casefold() in known_names or (
            not speaker
            and re.search(r"\b" + re.escape(legacy_name.casefold()) + r"\b", prompt.casefold())
        )
        if identified:
            return name if speaker else legacy_name, entity, prefix.end()
    return name, entity, 0


def spoken_dialogue(shot, context=None) -> str:
    if shot.get("audio_mode", "native") != "native":
        return ""
    context = context or {}
    if shot.get("dialogue_turns"):
        dialogue = " ".join(str(turn["text"]).strip() for turn in shot["dialogue_turns"])
    else:
        dialogue = str(shot.get("dialogue") or "").strip()
    _, _, prefix_end = _speaker_identity(shot, context)
    dialogue = dialogue[prefix_end:]
    if re.search(r"<\s*/?\s*d\s*>", dialogue, flags=re.I):
        raise ValueError("Le dialogue doit contenir seulement les paroles, sans balises H3.")
    return dialogue


def compile_h3_prompt(shot, context=None, first_frame=False) -> str:
    context = context or {}
    profile = directing_profile(context.get("directing_profile"))
    prompt = re.sub(r"<d>.*?</d>", "", str(shot.get("prompt") or ""), flags=re.S | re.I)
    prompt = re.sub(
        r"For the target video, at 0\.00 seconds into the target video,\s*"
        r"<Picture 1> \(from \[Shot 1\]\) is fully referenced\.\s*",
        "",
        prompt,
        flags=re.I,
    )
    prompt = re.split(r"\b(?:overall_soundscape|non_diegetic_music):", prompt, flags=re.I)[0]
    prompt = re.sub(
        r"\bintegrated_multimodal_description:\s*|\[Shot 1\]\s*", "", prompt, flags=re.I
    )
    prompt = re.sub(r"<Picture\s+\d+>", "", prompt, flags=re.I).strip()
    prompt = validate_h3_prose(prompt)
    dialogue = spoken_dialogue(shot, context)
    name, entity, _ = _speaker_identity(shot, context)
    name = validate_h3_prose(name)
    parts = ["[Shot 1] One continuous shot."]
    style = context.get("bible", {}).get("visual_style")
    if style:
        parts.append(validate_h3_prose(style))
    if entity.get("description") and not shot.get("dialogue_turns"):
        parts.append(f"{name}: {validate_h3_prose(entity['description'])}")
    if prompt:
        parts.append(prompt)
    for field in ("framing", "camera", "action"):
        value = shot.get(field) or context.get(field)
        if value:
            parts.append(validate_h3_prose(value))
    if (
        not (shot.get("framing") or context.get("framing"))
        and profile["framing"] == "close_preferred"
    ):
        parts.append("Prefer close framing; keep a wider composition when the action needs it.")
    if not (shot.get("camera") or context.get("camera")):
        if profile["camera"] == "static":
            parts.append("The camera stays static.")
        elif profile["camera"] == "subtle":
            parts.append("The camera stays stable or moves gently with the action.")
    if first_frame and profile["continuity"]:
        parts.append(
            "Continue the first frame's identity, clothing, lighting and screen direction."
        )
    if dialogue:
        language = dialogue_language(shot.get("language") or context.get("language"))
        turns = shot.get("dialogue_turns") or [{"speaker": shot.get("speaker"), "text": dialogue}]
        entities = context.get("entities", [])
        ids = dict(context.get("speaker_ids") or {})
        for item in entities:
            if item.get("kind", "character") == "character" and item["key"] not in ids:
                ids[item["key"]] = f"S{len(ids) + 1}"
        delivery = "brisk conversational" if profile["dialogue_density"] == "brisk" else "natural"
        for turn in turns:
            who, identity, _ = _speaker_identity(
                {"speaker": turn.get("speaker"), "dialogue": turn["text"]}, context
            )
            if not turn.get("speaker"):
                who = name
            if shot.get("dialogue_turns") and identity.get("description"):
                parts.append(f"{who}: {validate_h3_prose(identity['description'])}")
            voice = identity.get("voice_description")
            if voice:
                parts.append(f"{who}'s voice: {validate_h3_prose(voice)}")
            if turn.get("action"):
                parts.append(validate_h3_prose(turn["action"]))
            speaker_id = ids.get(turn.get("speaker"), "S1")
            parts.append(
                f"{validate_h3_prose(who)} ({speaker_id}) speaks with clear "
                f"{delivery} {language} delivery: <d>[{language}] {turn['text']}</d>"
            )
            if turn.get("reaction"):
                parts.append(validate_h3_prose(turn["reaction"]))
        if len(turns) > 1:
            parts.append("The speakers take turns in this order, without overlapping speech.")
        soundscape = "Very faint location ambience beneath the clearly audible dialogue."
    else:
        parts.append(
            "The characters remain silent, expressing the beat through gestures and posture."
        )
        soundscape = "Quiet location ambience and natural physical action sounds, without voices."
    if shot.get("audio_mode") == "silent":
        soundscape = "N/A"
    elif shot.get("soundscape"):
        soundscape = validate_h3_prose(shot["soundscape"])
    reaction = shot.get("reaction") or context.get("reaction")
    if reaction:
        parts.append(validate_h3_prose(reaction))
    elif profile["reactions"] == "immediate":
        parts.append(
            "The character reacts immediately through expression or gesture after the beat."
        )
    if profile["active_listening"]:
        parts.append(
            "Any listener follows the speaker with attentive posture and responsive gestures."
        )
    if profile["silences"] == "brief":
        parts.append("Pauses are brief and motivated by the action.")
    result = (
        "integrated_multimodal_description: "
        + " ".join(parts)
        + f"\n\noverall_soundscape: {soundscape}\n\nnon_diegetic_music: N/A"
    )
    if first_frame:
        result = (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.\n\n" + result
        )
    return result
