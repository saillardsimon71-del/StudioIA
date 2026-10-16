from calliope.studio.h3_prompt import (
    dialogue_language,
    directing_profile,
    spoken_dialogue,
    validate_h3_prose,
)
from calliope.studio.visual_identity import strip_generated_text, visual_description


def compile_ltx_prompt(shot, context, first_frame):
    shot = (context.get("shot") or {}) | shot
    profile = directing_profile(context.get("directing_profile"))
    canon = context.get("render_canon") or {}
    characters = canon.get("characters") or {
        entity["key"]: {"name": entity.get("name") or entity["key"],
                        "appearance": visual_description(entity),
                        "voice": entity.get("voice_description") or ""}
        for entity in context.get("entities", []) if entity.get("kind", "character") == "character"
    }
    labels = shot.get("screen_text") or []

    def prose(value):
        return strip_generated_text(validate_h3_prose(value), labels)

    dialogue = spoken_dialogue(shot, context)
    turns = shot.get("dialogue_turns") or (
        [{"speaker": shot.get("speaker"), "text": dialogue}] if dialogue else []
    )
    parts = ["One continuous cinematic shot with a clean, unobstructed picture."]
    if first_frame:
        parts.append("Continue the supplied opening image's characters, setting and lighting.")
    style = canon.get("style") or context.get("bible", {}).get("visual_style")
    if style and not first_frame:
        parts.append(prose(style))
    speakers = {turn.get("speaker") for turn in turns}
    cast = shot.get("character_keys", []) + [turn.get("speaker") for turn in turns]
    for key in dict.fromkeys(cast):
        character = characters.get(key)
        if character:
            name = prose(character["name"])
            parts.append(f"{name} is {prose(character.get('appearance') or '')}")
            if key in speakers and character.get("voice"):
                parts.append(f"{name}'s voice: {prose(character['voice'])}")
    if not first_frame and shot.get("location_key") in canon.get("locations", {}):
        parts.append(prose(canon["locations"][shot["location_key"]]))
    action = prose(shot.get("action") or "")
    for field in ("prompt", "framing", "camera", "action"):
        if shot.get(field):
            value = prose(shot[field])
            if field == "prompt" and (action == value or action.startswith(value + " ")):
                continue
            parts.append(value)
    if dialogue:
        language = dialogue_language(shot.get("language") or context.get("language"))
        delivery = "rapid, intelligible" if shot.get("purpose") == "midroll" else (
            "brisk, expressive" if profile["dialogue_density"] == "brisk"
            else "natural, expressive")
        parts.append(f"The dialogue is in {language}, with {delivery} delivery "
                     "and clear articulation.")
        parts.append("Speak each quoted line exactly once, in full and in the written order. "
                     "The clear, close voice stays in the foreground above the nonverbal "
                     "location sounds.")
        if profile["dialogue_density"] == "brisk" or profile["silences"] == "brief":
            parts.append("Each reply picks up as the previous line ends, "
                         "with brief natural breaths. Perform the described gestures "
                         "while speaking, keeping the exchange flowing.")
        if profile["active_listening"] and len(shot.get("character_keys", [])) > 1:
            parts.append("Use facial expressions and small motivated head or hand movements during "
                         "the dialogue, consistent with the planned action, blocking "
                         "and prop states. "
                         "Listeners react silently while the speaker talks.")
        if len(speakers) == 1:
            owner = next(iter(speakers))
            name = prose((characters.get(owner) or {}).get('name') or owner)
            parts.append(f'Only {name} speaks in this take, starting promptly. '
                         'The other visible characters listen and react silently '
                         'with closed mouths.')
        for turn in turns:
            character = characters.get(turn.get("speaker")) or {}
            name = prose(character.get("name") or turn.get("speaker") or "The visible character")
            if turn.get("action"):
                parts.append(prose(turn["action"]))
            parts.append(f'{name} says: "{validate_h3_prose(turn["text"])}"')
            if turn.get("reaction"):
                parts.append(prose(turn["reaction"]))
    else:
        parts.append("The characters perform the action silently.")
    if shot.get("reaction"):
        parts.append(prose(shot["reaction"]))
    if shot.get("audio_mode", "native") == "native":
        parts.append(prose(shot.get("soundscape") or
                           "Audible nonverbal location ambience beneath the dialogue."))
    parts.append("The uninterrupted picture shows only the physical scene. The characters' words "
                 "exist only as sound, leaving the entire picture unobstructed.")
    return " ".join(parts)
