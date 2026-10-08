import copy
import json
import re

from calliope.agent.llm import generate_structured
from calliope.studio.audio import AudioSettings
from calliope.studio.h3_prompt import directing_profile, validate_h3_prose
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
The target duration is a real format constraint: the story must carry about
target_seconds of USEFUL screen time, no more, no less. Write the story FIRST,
then cut it into shots. The number of shots is FREE (usually 10 to 30). Each
shot lasts ONLY the time its line, action or reaction honestly needs, then ends
as soon as its beat is over: a quick reaction is 1.5-2.5 s, a spoken line is
2-6 s, a movement or suspense beat is 3-8 s. Give duration_sec one decimal,
for example 1.6, 3.4, 2.1 or 4.8, and vary it: never quantize the episode into
identical blocks. Never stretch a shot to fill time; the edit trims any dead
footage by itself, so script ZERO dead time. A shot beyond 8 s needs a strong
reason, never exceed 15 s.
Alternate story actions and short dialogue; each shot must advance the story.
Each rendered shot has ONE dominant speaking character, with listeners allowed.
Several sentences by this speaker may belong to one continuous shot. Choose
shot/reverse-shot or a two-shot when it helps the scene, never one sentence per cut.
Dialogue is plain speech, without speaker names, quotes or instructions. Keep dialogue
naturally speakable within the shot duration. Prefer short lines, but do not force
an exact words-per-second ratio; the final take is validated from the generated audio.
No narrator, offscreen voices,
overlapping voices, prompt recitation or music inside the video. Prompts and
still prompts in English; speech in the series language. Spell out who is
visible, clothes, location, lighting, camera, expression and quiet native
ambience. Keep the established visual style; no default cartoon or dystopian
style. Apply the directing_profile supplied in the context: it controls realization,
not story content. A generalist profile follows the scene. The social dialogue
preset favors close framing, simple camera, brisk naturally spoken exchanges,
immediate reactions and active listeners. Wider shots and full-body two-shots
remain useful when the action needs them. Density comes from useful story beats,
not artificially accelerated words or stretched durations. Keep canon masks,
helmets and non-human designs; express speech and listening through fitting
gestures and posture when a mouth is hidden. Preserve screen direction and
eyelines. Pauses serve the scene, with brief motivated pauses in the social preset.
Describe visual direction in English; only dialogue uses the series language.
The renderer produces native dialogue with very faint location ambience.
Music and additional ambience and sound effects are handled in post-production.
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
"reference_key":"composition key", "duration_sec":3.4, "audio_mode":"native",
"action":"simple physical action", "reaction":"listener or speaker reaction",
"framing":"composition appropriate to the action", "camera":"simple camera behavior"}]}],
"audio":{"enabled":true,"music":"none/neutral/tension/warm/playful/energetic/horror",
"tempo":72,"music_gain_db":-28,"voice_gain_db":-5,"ducking":true,
"ambience":"none/room/wind/rain/cafe/night/city/forest", "ambience_gain_db":-38,
"sfx":[{"effect":"click/beep/impact/boom/whoosh/riser/door/footstep","shot_index":0,
"at_sec":0.2,"gain_db":-24}],
"holds":[{"shot_index":0,"at_sec":0.0,"duration_sec":0.8,"drop_db":-36}]}}.
Sound design is post-production, synchronized with the real beats: pick the
ambience that matches the main location, keep the music bed VERY light so it
never masks the voices, keep sfx sparse (a riser into the final reveal, one
impact or boom on it), and use at most one or two holds — short voluntary
silences right before a reveal — never under a spoken line. Summary and events
must describe what the episode actually shows, not future intentions. Never
claim that a cliffhanger has already been resolved."""
    public_context = {k: v for k, v in context.items() if k != "entities"}
    public_context["directing_profile"] = directing_profile(context.get("directing_profile"))
    public_context["entities"] = [
        {k: e[k] for k in ("key", "kind", "name", "description")} for e in context["entities"]
    ]
    target = context.get("target_seconds", 65)
    estimated_shots = max(8, round(target / 3))
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "series": public_context,
                    "idea": idea,
                    "objective": objective,
                    "target_seconds": target,
                    "pacing": (
                        "free shot count, one decimal duration_sec per shot, "
                        "each as short as its beat allows, total near target_seconds"
                    ),
                },
                ensure_ascii=False,
            ),
        },
    ]
    for attempt in range(3):
        result = await generate_structured(
            messages,
            temperature=0.45,
            max_tokens=max(4096, min(16000, estimated_shots * 650 + 2000)),
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
                            f"Keep free per-shot durations; adjust the cutting or the "
                            f"dialogue so the useful total stays near {target} seconds "
                            "without stretching any shot. "
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
    total_duration = 0
    if not result["scenes"]:
        raise ValueError("L'épisode doit contenir au moins une scène.")
    for scene in result["scenes"]:
        if (
            not isinstance(scene, dict)
            or not isinstance(scene.get("shots"), list)
            or not scene["shots"]
        ):
            raise ValueError("Le scénario contient une scène vide.")
        for shot in scene["shots"]:
            if (
                not isinstance(shot, dict)
                or not str(shot.get("prompt", "")).strip()
                or not str(shot.get("image_prompt", "")).strip()
            ):
                raise ValueError("Le scénario contient un plan sans prompt.")
            try:
                duration = float(shot.get("duration_sec") or 0)
            except (TypeError, ValueError):
                duration = 0
            if not 1 <= duration <= 15:
                raise ValueError(
                    "Chaque plan doit viser une durée utile entre 1 et 15 secondes."
                )
            duration = round(duration, 1)
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
            shot["audio_mode"] = "native" if dialogue else "silent"
            shot["prompt"] = re.sub(
                r"<d>.*?</d>", "", str(shot["prompt"]), flags=re.S | re.I
            ).strip()
            for field in ("action", "reaction", "framing", "camera"):
                if field in shot and (
                    not isinstance(shot[field], str) or len(shot[field]) > 5000
                ):
                    raise ValueError("Une directive de réalisation est invalide.")
                if field in shot:
                    validate_h3_prose(shot[field])
    if "target_seconds" in context:
        target = float(context["target_seconds"])
        # Le montage coupe mais n'étire jamais : un scénario manifestement plus
        # court ou plus long que le format est réparé avant tout rendu GPU, la
        # manière de découper restant entièrement libre.
        if total_duration < target - 2.0:
            raise ValueError(
                f"Le scénario ne remplit que {total_duration:g} s utiles sur "
                f"les {target:g} s du format : l'histoire est trop courte. "
                "Développez l'événement ou ajoutez une action/réaction utile, "
                "sans jamais étirer un plan existant."
            )
        if total_duration > target + 5.0:
            raise ValueError(
                f"La durée utile du scénario ({total_duration:g} s) dépasse la "
                f"durée demandée ({target:g} s). Ajustez le découpage ou les "
                "répliques, sans étirer les plans."
            )
    result["audio"] = AudioSettings.model_validate(result.get("audio") or {}).model_dump()
    if directing_profile(context.get("directing_profile"))["music"] == "none":
        result["audio"]["music"] = "none"
    result["bible"] = {
        key: str(value) for key, value in (result.get("bible") or {}).items() if key in BIBLE_FIELDS
    }
    if not isinstance(result.get("events", []), list):
        raise ValueError("Le récapitulatif de continuité est invalide.")
    return result
