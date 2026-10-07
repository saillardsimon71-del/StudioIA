import copy
import json
import re

from calliope.agent.llm import generate_structured
from calliope.studio.audio import AudioSettings
from calliope.studio.series import BIBLE_FIELDS


async def plan_episode(context, idea, objective):
    prompt = """You write a short, filmable episode for a GENERALIST video studio.
The supplied series context is the ONLY canon. Never borrow names, rules or
events from unrelated series. Preserve existing identities, relationships,
wardrobe, geography and unresolved events. Respect the requested genre and
language. New entities must have stable lowercase ASCII keys.
Develop missing bible fields from the concept; do not contradict filled fields.
Open with an immediate visual hook, develop one coherent event, end with a
revelation or compelling next question. No padding, no unexplained location jumps.
Use the target duration: roughly one complete 5-second shot per 5 seconds.
For a 70-second episode, write 14 five-second shots (actual H3 export about 72s).
Return exactly required_shots shots, each lasting shot_duration_sec seconds.
Alternate story actions and short dialogue; each shot must advance the story.
Each shot shows at most ONE speaking character. Shot/reverse-shot for dialogue.
Dialogue is plain speech, without speaker names, quotes or instructions. At most
11 spoken words per 5 seconds. No narrator, offscreen voices, overlapping voices,
prompt recitation or music inside the video. Prompts and still prompts in English;
speech in the series language. Spell out who is visible, clothes, location,
lighting, camera, expression and quiet native ambience. Keep the established
visual style; no default cartoon or dystopian style. Prefer a stable medium
close-up for dialogue, one natural voice with synchronized lips, then silence.
Reuse a reference_key for the SAME character, location and camera composition;
change it when the scene really needs a new composition. Existing characters
and locations have canonical image references used by the image service.
Return ONLY JSON with these fields:
{"title":"episode title", "summary":"brief factual recap", "events":["actual new event"],
"ongoing_plot":"unresolved plot", "bible":{"universe":"", "tone":"", "visual_style":"",
"characters":"identity and voices", "relationships":"", "locations":"", "world_rules":"",
"past_events":"", "ongoing_plot":"", "continuity":"screen direction, visual rules"},
"entities":[{"key":"stable_key","kind":"character or location","name":"name",
"description":"precise permanent visual identity; wardrobe; voice for characters"}],
"scenes":[{"heading":"scene title","action":"action","dialog":"full scene dialogue",
"shots":[{"description":"story beat","prompt":"visual motion and native sound",
"image_prompt":"still composition matching motion prompt", "dialogue":"plain speech",
"speaker":"character key or empty", "character_keys":["key"], "location_key":"key or empty",
"reference_key":"composition key", "duration_sec":5, "audio_mode":"native"}]}],
"audio":{"enabled":true,"music":"none/neutral/tension/warm/playful/energetic/horror",
"tempo":72,"music_gain_db":-28,"voice_gain_db":-5,"ducking":true,
"ambience":"none/room/wind/rain", "ambience_gain_db":-38,
"sfx":[{"effect":"click/beep/impact/whoosh/door/footstep","shot_index":0,
"at_sec":0.2,"gain_db":-24}]}}.
SFX are optional and sparse. Prefer native ambience for realistic environmental
sounds. Summary and events must describe what the episode actually shows, not
future intentions. Never claim that a cliffhanger has already been resolved."""
    public_context = {k: v for k, v in context.items() if k != "entities"}
    public_context["entities"] = [
        {k: e[k] for k in ("key", "kind", "name", "description")} for e in context["entities"]
    ]
    target = context.get("target_seconds", 70)
    shot_count = round(target / 5)
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "series": public_context,
                    "idea": idea,
                    "objective": objective,
                    "required_shots": shot_count,
                    "shot_duration_sec": 5,
                },
                ensure_ascii=False,
            ),
        },
    ]
    for attempt in range(3):
        result = await generate_structured(
            messages,
            temperature=0.45,
            max_tokens=max(4096, min(15000, shot_count * 650 + 2000)),
        )
        try:
            return normalize_plan(copy.deepcopy(result), context)
        except ValueError as exc:
            if attempt == 2:
                raise
            messages.extend(
                [
                    {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            f"Validation failed: {exc}. Repair the complete JSON episode. "
                            f"Exactly {shot_count} shots of 5 seconds for about {target} seconds. "
                            "Preserve the story and series canon. Shorten dialogue if needed. "
                            "Return the complete corrected JSON, with all scenes and shots."
                        ),
                    },
                ]
            )


def normalize_plan(result, context):
    if not isinstance(result, dict) or not isinstance(result.get("scenes"), list):
        raise ValueError("L'IA n'a pas renvoyé de scénario exploitable.")
    known = {item["key"] for item in context.get("entities", [])}
    new_entities = result.get("entities", [])
    if not isinstance(new_entities, list) or len(new_entities) > 30:
        raise ValueError("Liste de personnages ou lieux invalide.")
    for entity in new_entities:
        if (
            not isinstance(entity, dict)
            or not re.fullmatch(r"[a-z0-9_-]{1,80}", str(entity.get("key", "")))
            or entity.get("kind") not in ("character", "location")
            or not str(entity.get("description", "")).strip()
        ):
            raise ValueError("Une identité visuelle est incomplète.")
        known.add(entity["key"])
    count = 0
    total_duration = 0
    if not 1 <= len(result["scenes"]) <= 24:
        raise ValueError("L'épisode doit contenir entre 1 et 24 scènes.")
    for scene in result["scenes"]:
        if (
            not isinstance(scene, dict)
            or not isinstance(scene.get("shots"), list)
            or not scene["shots"]
        ):
            raise ValueError("Le scénario contient une scène vide.")
        for shot in scene["shots"]:
            count += 1
            if (
                not isinstance(shot, dict)
                or not str(shot.get("prompt", "")).strip()
                or not str(shot.get("image_prompt", "")).strip()
            ):
                raise ValueError("Le scénario contient un plan sans prompt.")
            duration = min(10, max(5, float(shot.get("duration_sec") or 5)))
            dialogue = str(shot.get("dialogue") or "").strip()
            speaker = str(shot.get("speaker") or "")
            if speaker and speaker not in known:
                raise ValueError("Le locuteur ne fait pas partie des personnages de cette série.")
            if dialogue and not speaker:
                raise ValueError("Chaque réplique doit avoir un unique locuteur identifié.")
            if re.search(
                r"<[^>]+>|\n|(?:^|\s)[A-Z][\w -]{0,30}:|say exactly|spoken dialogue", dialogue, re.I
            ):
                raise ValueError(
                    (
                        "Une réplique contient plusieurs voix ou des instructions. Corrigez"
                        " le scénario avant le rendu."
                    )
                )
            if len(dialogue.split()) > int(duration * 2.2):
                raise ValueError("Une réplique est trop longue pour un dialogue intelligible.")
            keys = shot.get("character_keys") or []
            location = shot.get("location_key") or ""
            if (
                not isinstance(keys, list)
                or any(key not in known for key in keys)
                or (location and location not in known)
            ):
                raise ValueError("Un plan utilise une référence étrangère à cette série.")
            if speaker and speaker not in keys:
                raise ValueError("Le locuteur doit être visible dans son plan.")
            shot["duration_sec"] = duration
            total_duration += duration
            shot["dialogue"] = dialogue
            shot["audio_mode"] = "native"
            shot["prompt"] = re.sub(r"<d>.*?</d>", "", str(shot["prompt"]), flags=re.S).strip()
            shot["prompt"] += (
                "\nOne continuous shot, preserve the reference identity, clothing, "
                "lighting and screen direction. "
            )
            shot["prompt"] += (
                (
                    "Only the visible speaker speaks, a single natural voice, synchronized "
                    "lip movement, then silence. "
                )
                if dialogue
                else "No speech, quiet native ambience. "
            )
            shot["prompt"] += (
                "No narration, no offscreen voices, no overlapping speech, no background music."
            )
    if count > 24:
        raise ValueError("Limitez un épisode à 24 plans.")
    if "target_seconds" in context and abs(total_duration - context["target_seconds"]) > 5:
        raise ValueError(
            f"La durée du scénario ({total_duration:g} s) ne correspond pas "
            f"à la durée demandée ({context['target_seconds']} s)."
        )
    result["audio"] = AudioSettings.model_validate(result.get("audio") or {}).model_dump()
    result["bible"] = {
        key: str(value) for key, value in (result.get("bible") or {}).items() if key in BIBLE_FIELDS
    }
    if not isinstance(result.get("events", []), list):
        raise ValueError("Le récapitulatif de continuité est invalide.")
    return result
