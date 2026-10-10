import copy
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from calliope.agent.llm import generate_structured
from calliope.config import settings
from calliope.studio.audio import AudioSettings
from calliope.studio.h3_prompt import (
    LANGUAGES,
    dialogue_language,
    directing_profile,
    validate_h3_prose,
)
from calliope.studio.series import BIBLE_FIELDS

logger = logging.getLogger(__name__)
OUTRO_SECONDS = 8.0
OUTRO_KEY = "studio_cta_host"
OUTRO_DIALOGUES = {
    "en": "Want to make videos too? Copy this video's link, like, subscribe, and comment GO!",
    "fr": (
        "Toi aussi, tu veux faire des vidéos ? "
        "Copie le lien de la vidéo, like, abonne-toi et commente GO !"
    ),
    "ar": "هل تريد صنع فيديوهات أيضا؟ انسخ رابط الفيديو، وأعجب به، واشترك، واكتب GO في التعليقات!",
    "zh": "你也想制作视频吗？复制这个视频的链接，点赞、订阅，并评论GO！",
    "de": (
        "Willst du auch Videos machen? "
        "Kopiere den Link dieses Videos, like, abonniere und kommentiere GO!"
    ),
    "it": (
        "Vuoi fare video anche tu? "
        "Copia il link del video, metti mi piace, iscriviti e commenta GO!"
    ),
    "ja": (
        "あなたも動画を作りたい？この動画のリンクをコピーして、"
        "いいねと登録をして、GOとコメントしてね！"
    ),
    "ko": (
        "너도 영상을 만들고 싶어? 이 영상 링크를 복사하고, "
        "좋아요와 구독을 누르고, GO라고 댓글을 남겨!"
    ),
    "pt": "Você também quer fazer vídeos? Copie o link do vídeo, curta, inscreva-se e comente GO!",
    "ru": (
        "Тоже хочешь создавать видео? "
        "Скопируй ссылку на видео, поставь лайк, подпишись и напиши GO в комментариях!"
    ),
    "es": (
        "¿Tú también quieres hacer videos? "
        "Copia el enlace del video, dale me gusta, suscríbete y comenta GO!"
    ),
}


async def plan_episode(context, idea, objective):
    prompt = """You write a short, filmable episode for a GENERALIST video studio.
The supplied series context is the ONLY canon. Never borrow names, rules or
events from unrelated series. Preserve existing identities, relationships,
wardrobe, geography and unresolved events. Respect the requested genre and
language. New entities must have stable lowercase ASCII keys.
Every character or location key used by a shot must exist in the supplied canon
or in entities. Declare ALL new locations, including rooms and corridors.
Develop missing bible fields from the concept; do not contradict filled fields.
Open with an immediate visual hook, develop one coherent event, end with a
revelation or compelling next question. No padding, no unexplained location jumps.
The target duration is a real format constraint: the story must carry about
target_seconds of USEFUL screen time, no more, no less. Write the story FIRST,
then cut it into shots. The number of shots is FREE; there is no shot-count
target. Each shot lasts ONLY the time its exchange, action or reaction needs, then ends
as soon as its beat is over: a quick reaction is 1.5-2.5 s, a spoken line is
2-6 s, a movement or suspense beat is 3-8 s. Give duration_sec one decimal,
for example 1.6, 3.4, 2.1 or 4.8, and vary it: never quantize the episode into
identical blocks. Never stretch a shot to fill time; the edit trims any dead
footage by itself, so script ZERO dead time. A shot beyond 8 s needs a strong
reason, never exceed 15 s.
Alternate story actions and short dialogue; each shot must advance the story.
Build conversations as shared compositions, not one isolated portrait per reply.
For a short question and answer in the same location, keep both characters in
one continuous shot by default; cut only when a new visual beat benefits from it.
The responder and listener stay visible together, with matching eyelines and
immediate reactions. A close framing can still include both characters.
One continuous shot may contain several speakers taking turns, with immediate
reactions in the same composition. Use dialogue_turns for an ordered exchange;
each turn has one speaker key and exact plain text, plus optional action/reaction.
No overlapping speech. Cut for a reveal, reaction or stronger composition, never
just because the speaker changes. Prefer simple two-shots for a short exchange.
List every speaking AND listening character in character_keys and in the still
composition. Also declare listener_keys for silent participants in the exchange.
Do not omit a conversation partner simply because they are not speaking yet.
Example of ONE SHOT containing an exchange (adapt to the actual series):
character_keys=["character_a","character_b"], dialogue_turns=[
{"speaker":"character_a","text":"Did you sell it?","reaction":"B looks unimpressed."},
{"speaker":"character_b","text":"It was a good deal.","reaction":"A stares in disbelief."}].
Both characters are present in image_prompt; duration covers the whole exchange.
When no one speaks, use speaker="", dialogue="" and dialogue_turns=[].
Never invent an unidentified Registry, scanner or UI voice. A system notification
without a visible speaking character is a visual event with a chime or effect,
not a dialogue turn. Attribute every spoken turn to an actual character key.
Do not add a decorative silent establishing shot at the start of each scene.
Dialogue is plain speech, without speaker names, quotes or instructions. Never
put H3 tags, language markers or speaker IDs in dialogue; the compiler adds them.
Keep dialogue
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
"description":"precise permanent visual identity; wardrobe; voice for characters",
"identity":{"visual_description":"immutable species, face, colors and proportions",
"variant_of":"existing identity key for an age variant or empty",
"attached_value":null,"label_key":"stable label key shared by variants or empty"}}],
"scenes":[{"heading":"scene title","action":"action","dialog":"full scene dialogue",
"shots":[{"description":"story beat","prompt":"visual motion and native sound",
"image_prompt":"still composition including speakers and listeners",
"character_keys":["key"], "listener_keys":["silent conversation partner key"],
"location_key":"key or empty",
"reference_key":"composition key", "duration_sec":3.4, "audio_mode":"native",
"dialogue_turns":[{"speaker":"character key","text":"plain speech",
"action":"optional gesture","reaction":"optional reaction after this turn"}],
"soundscape":"quiet specific location and physical sounds; no speech or music",
"screen_text":[{"key":"stable label key","entity_key":"character key or empty",
"value":12349,"text":"optional literal label; omit for numeric values",
"x":0.5,"y":0.12,"anchor":"screen or character",
"anchor_box":[0.2,0.2,0.5,0.6],"event":"none/appear/change/reveal"}],
"action":"simple physical action", "reaction":"listener or speaker reaction",
"framing":"composition appropriate to the action", "camera":"simple camera behavior"}]}],
"audio":{"enabled":true,"music":"none/neutral/tension/warm/playful/energetic/horror",
"tempo":72,"music_gain_db":-12,"voice_gain_db":0,"ducking":true,
"ambience":"none/room/wind/rain/cafe/night/city/forest", "ambience_gain_db":-34,
"sfx":[{"effect":"click/beep/impact/boom/whoosh/riser/door/footstep/pop/boing/scratch","shot_index":0,
"at_sec":0.2,"gain_db":-18}],
"holds":[{"shot_index":0,"at_sec":0.0,"duration_sec":0.8,"drop_db":-36}]}}.
Sound design is post-production, synchronized with the real beats: pick the
ambience that matches the main location. The instrumental bed must be audible
between lines; ducking protects speech. Follow the series musical identity.
Place occasional sound accents on specific narrative events, not on a timer:
pop for a sudden appearance, boing for an absurd bounce or physical gag,
scratch for an interrupted boast or power reversal, impact for a reveal,
beep/click for a notification, riser before a cliffhanger. Avoid stacking
effects on every cut or adding effects under important words. Keep cue gains
around -18 dB, with quieter accents at -22 dB. Never use spoken or sung effects.
Use at most one or two holds — short voluntary
silences right before a reveal — never under a spoken line. Summary and events
must describe what the episode actually shows, not future intentions. Never
claim that a cliffhanger has already been resolved."""
    prompt += """
Exact scores, numbers or labels important to the story belong in screen_text.
For a number use value as an integer, never punctuation in text. The software
formats all numbers with the series number_format. For a literal label use text.
Keep their exact spelling stable until an explicit change/reveal beat. Reuse
display_values from the series canon. Never ask the image/video model to write
these values: describe a blank illuminated panel or empty score position instead.
Coordinates are normalized to the reference image. Use screen anchors for a
fixed display; character anchors require a tight, textured anchor_box around the
correct character, excluding other characters. Keep the composition simple.
No decorative score changes. Mark event=appear/change/reveal only at real beats.
The software adds the exact text and event sound in post-production.
If directing_profile.attached_values is true, declare each required character's
initial integer identity.attached_value and stable identity.label_key. Every
visible character with a configured value MUST have its own numeric screen_text
entry attached to that entity_key. Do not replace this data with prose or omit it.
For an in-shot change, keep value as the initial value and add
changes:[{"at_sec":1.2,"value":2}], with event="change"; at_sec is the useful
edited shot time. The next shot starts with that final value. Never infer a
change from action text. Preserve all other characters' values.
Identity variants retain the same immutable species, colors and facial traits;
variant_of supplies their existing reference, it is not a new random design.
If require_reference_approval is true, visual_description must explicitly fix
the immutable design of every character and location. References are reviewed before video.
"""
    trace = context.get("_planner_trace")
    trace_token = uuid.uuid4().hex
    public_context = {k: v for k, v in context.items() if k not in ("entities", "_planner_trace")}
    public_context["directing_profile"] = directing_profile(context.get("directing_profile"))
    public_context["entities"] = [
        {k: e[k] for k in ("key", "kind", "name", "description", "identity") if k in e}
        for e in context["entities"]
        if e["key"] != OUTRO_KEY
    ]
    episode_target = float(context.get("target_seconds", 65))
    target = max(5.0, episode_target - OUTRO_SECONDS)
    public_context["target_seconds"] = target
    estimated_shots = max(8, round(episode_target / 3))
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
                    "ending": (
                        "The software adds a promotional outro in the episode language "
                        "after your story. "
                        "Do not write the promotion or include its host in the story."
                    ),
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
        request_messages = copy.deepcopy(messages)
        result = await generate_structured(
            messages,
            temperature=0.45,
            max_tokens=max(4096, min(16000, estimated_shots * 650 + 2000)),
        )
        validation_error = None
        try:
            plan = normalize_plan(copy.deepcopy(result), context | {"target_seconds": target})
            return append_outro(plan, context)
        except ValueError as exc:
            validation_error = str(exc)
            logger.warning("Planner validation failed attempt=%s: %s", attempt + 1, exc)
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
                            f"of story plus an automatic {OUTRO_SECONDS:g}-second outro "
                            f"for an episode near {episode_target:g} seconds, "
                            "without stretching any shot. "
                            "Preserve the story and series canon. Shorten dialogue if needed. "
                            "Repair the identified field, preserving unaffected shots. "
                            "Each dialogue turn must have one speaker and plain verbatim text. "
                            "For multiple voices use ordered dialogue_turns inside the same "
                            "shot when the composition supports the exchange. Do not discard "
                            "a character's reply or hide it in another field. "
                            "Return the complete corrected JSON, with all scenes and shots."
                        ),
                    },
                ]
            )
        finally:
            if trace:
                directory = (
                    settings.assets_dir / str(trace["project_id"]) / "studio" / "diagnostics"
                )
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / (
                    f"planner-{trace['job_id']}-{trace_token}-attempt-{attempt + 1}.json"
                )
                path.write_text(
                    json.dumps(
                        {
                            "project_id": trace["project_id"],
                            "job_id": trace["job_id"],
                            "attempt": attempt + 1,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "messages": request_messages,
                            "response": result,
                            "validation_error": validation_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )


def canonical_key(value, entities, kind):
    value = str(value or "").strip()
    if not value:
        return ""
    candidates = [e for e in entities if e.get("kind", "character") == kind]
    if any(e["key"] == value for e in candidates):
        return value
    normalized = re.sub(r"[\s_-]+", "_", value.casefold())
    keys = {
        e["key"]
        for e in candidates
        if normalized
        in {
            re.sub(r"[\s_-]+", "_", str(e.get(field) or "").strip().casefold())
            for field in ("key", "name")
        }
    }
    return next(iter(keys)) if len(keys) == 1 else value


def append_outro(plan, context):
    plan.setdefault("entities", [])
    label = dialogue_language(context.get("language"))
    language = next(code for code, name in LANGUAGES.items() if name == label)
    dialogue = OUTRO_DIALOGUES[language]
    locations = {
        entity["key"] for entity in context.get("entities", []) + plan["entities"]
        if entity.get("kind") == "location"
    }
    location = next((
        shot["location_key"] for scene in reversed(plan.get("scenes", []))
        for shot in reversed(scene.get("shots", []))
        if shot.get("purpose") != "outro" and shot.get("location_key") in locations
    ), "")
    lines = re.split(r"(?<=[?؟？])\s*", dialogue, maxsplit=1)
    if not any(e["key"] == OUTRO_KEY for e in context.get("entities", []) + plan["entities"]):
        plan["entities"].append(
            {
                "key": OUTRO_KEY,
                "kind": "character",
                "name": "Studio host",
                "description": (
                    "Friendly anthropomorphic red apple with a small green leaf, "
                    "expressive eyes, clearly visible mouth, small arms, navy jacket, "
                    "consistent proportions. Clear warm voice."
                ),
            }
        )
        host = plan["entities"][-1]
        host["identity"] = {"visual_description": host["description"]}
    shot = {
        "description": "The fruit host invites the viewer to make videos.",
        "prompt": (
            "The series host turns toward the viewer for a playful private aside in the "
            "episode's final location. The host leans slightly toward the lens, makes one "
            "inviting hand gesture and delivers the dialogue clearly. Keep the colorful "
            "background secondary and the face easy to read. No other character speaks. "
            "No text on screen."
        ),
        "image_prompt": (
            "Vertical medium close-up of the canonical series host in the episode's "
            "final location, matching its lighting and visual style. The host faces the "
            "viewer with a knowing smile, ready for a private aside. The host fills the "
            "frame; an expressive, colorful background stays softly out of focus. No text."
        ),
        "dialogue": dialogue,
        "dialogue_turns": [{"speaker": OUTRO_KEY, "text": line} for line in lines if line],
        "speaker": OUTRO_KEY,
        "character_keys": [OUTRO_KEY],
        "location_key": location,
        "reference_key": f"studio-cta-host-{location or 'setting'}",
        "duration_sec": OUTRO_SECONDS,
        "audio_mode": "native",
        "language": language,
        "purpose": "outro",
        "framing": "Medium close-up, visible speaker dominates the vertical frame.",
        "camera": "A gentle purposeful push-in, then steady framing on the host's face.",
        "action": "The host speaks directly to the viewer and gestures invitingly.",
        "reaction": "The host finishes with an enthusiastic smile.",
    }
    ending = {
        "heading": "Studio - Make your own videos",
        "action": shot["action"],
        "dialog": dialogue,
        "shots": [shot],
    }
    if plan.get("scenes") and any(
        s.get("purpose") == "outro" for s in plan["scenes"][-1].get("shots", [])
    ):
        plan["scenes"][-1] = ending
    else:
        plan.setdefault("scenes", []).append(ending)
    return plan


def normalize_dialogue(dialogue, speaker, entities):
    aliases = {}
    for entity in entities:
        if entity.get("kind") == "location":
            continue
        for name in (entity.get("key"), entity.get("name")):
            if name:
                aliases.setdefault(str(name).strip().casefold(), set()).add(entity["key"])
    quotes = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "\u00ab": "\u00bb"}
    had_label = False
    while True:
        before = dialogue
        if len(dialogue) >= 2 and quotes.get(dialogue[0]) == dialogue[-1]:
            dialogue = dialogue[1:-1].strip()
        prefix = re.match(r"^([^:\n]{1,80}):\s*", dialogue)
        owners = aliases.get(prefix[1].strip().casefold(), set()) if prefix else set()
        if owners:
            if len(owners) != 1 or (speaker and speaker not in owners):
                raise ValueError(
                    "La réplique contient plusieurs voix ou un locuteur contradictoire."
                )
            speaker = next(iter(owners))
            dialogue = dialogue[prefix.end() :].strip()
            had_label = True
        wrapper = re.fullmatch(r"<d>\s*(.*?)\s*</d>", dialogue, re.S | re.I)
        if wrapper and not re.search(r"<[^>]+>", wrapper[1]):
            dialogue = wrapper[1].strip()
            language = re.match(r"^\[([^\]]+)\]\s*", dialogue)
            if language and language[1].casefold() in (
                name.casefold() for name in LANGUAGES.values()
            ):
                dialogue = dialogue[language.end() :].strip()
        if before == dialogue:
            break
    labels = re.findall(r"(?:^|\s)([^\W\d_][\w '-]{0,30}):", dialogue, re.I)
    if len(labels) > 1 and any(
        aliases.get(label.strip().casefold()) != {speaker} for label in labels
    ):
        raise ValueError(
            "La réplique contient plusieurs voix non identifiées; utilisez dialogue_turns."
        )
    lines = []
    for index, line in enumerate(dialogue.splitlines()):
        line = line.strip()
        prefix = re.match(r"^([^:\n]{1,80}):\s*", line)
        owners = aliases.get(prefix[1].strip().casefold(), set()) if prefix else set()
        if owners:
            if len(owners) != 1 or (speaker and speaker not in owners):
                raise ValueError(
                    "La réplique contient plusieurs voix; séparez les locuteurs en dialogue_turns."
                )
            speaker = next(iter(owners))
            line = line[prefix.end() :].strip()
            had_label = True
        elif index and had_label and prefix:
            raise ValueError(f"La réplique contient plusieurs voix possibles: {prefix[1]!r}.")
        lines.append(line)
    dialogue = " ".join(" ".join(lines).split())
    for alias in aliases:
        if re.search(r"(?<!\w)" + re.escape(alias) + r"\s*:", dialogue, re.I):
            raise ValueError(
                "La réplique contient plusieurs voix ou une étiquette de locuteur interne."
            )
    if re.search(
        r"<[^>]+>|\[[^\]]*\]|\([^)]*\)|\bsay exactly\b|\bspoken dialogue\b", dialogue, re.I
    ):
        raise ValueError("La réplique contient des instructions ou une syntaxe H3 non isolable.")
    return dialogue, speaker


def normalize_plan(result, context):
    if not isinstance(result, dict) or not isinstance(result.get("scenes"), list):
        raise ValueError("L'IA n'a pas renvoyé de scénario exploitable.")
    profile = directing_profile(context.get("directing_profile"))
    canon = {item["key"]: item for item in context.get("entities", [])}
    known = set(canon)
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
        if entity["key"] in canon:
            original = canon[entity["key"]]
            for field in ("name", "description", "identity"):
                if field in original:
                    entity[field] = copy.deepcopy(original[field])
        known.add(entity["key"])
    all_entities = list((canon | {e["key"]: e for e in new_entities}).values())
    required_values = {}
    for entity in all_entities:
        identity = entity.get("identity") or {}
        if (
            profile["require_reference_approval"]
            and not identity.get("visual_description", "").strip()
        ):
            raise ValueError(
                f"entities[{entity['key']}].identity.visual_description: "
                "identité immuable manquante."
            )
        if entity.get("kind", "character") != "character":
            continue
        if identity.get("attached_value") is not None:
            value = identity["attached_value"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"entities[{entity['key']}].identity.attached_value: entier requis."
                )
            required_values[entity["key"]] = (identity.get("label_key") or entity["key"], value)
    if profile["attached_values"] and not required_values:
        raise ValueError(
            "entities.identity.attached_value: "
            "déclarez les valeurs entières structurées des personnages."
        )
    attached_required = profile["attached_values"] or bool(required_values)
    characters = {e["key"] for e in all_entities if e.get("kind", "character") == "character"}
    result["speaker_ids"] = dict(context.get("speaker_ids") or {})
    for entity in all_entities:
        if entity["key"] in characters and entity["key"] not in result["speaker_ids"]:
            result["speaker_ids"][entity["key"]] = f"S{len(result['speaker_ids']) + 1}"
    reference_errors = []
    for scene_index, scene in enumerate(result["scenes"]):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
            continue
        for shot_index, shot in enumerate(scene["shots"]):
            if not isinstance(shot, dict):
                continue
            field = f"scenes[{scene_index}].shots[{shot_index}]"
            location = canonical_key(shot.get("location_key"), all_entities, "location")
            if location and location not in known:
                reference_errors.append(f"{field}.location_key: Lieu non déclaré {location!r}.")
            turns = shot.get("dialogue_turns") or []
            if not isinstance(turns, list) or len(turns) > 30:
                continue
            for turn_index, turn in enumerate(turns):
                turn_field = f"{field}.dialogue_turns[{turn_index}]"
                if not isinstance(turn, dict):
                    reference_errors.append(f"{turn_field}: Tour de parole invalide.")
                    continue
                try:
                    turn_speaker = canonical_key(turn.get("speaker"), all_entities, "character")
                    text, turn_speaker = normalize_dialogue(
                        str(turn.get("text") or "").strip(), turn_speaker, all_entities
                    )
                    if turn_speaker not in characters:
                        raise ValueError(
                            f"speaker: Locuteur inconnu {turn_speaker!r}. "
                            f"Réplique: {str(turn.get('text'))[:240]!r}."
                        )
                    if not text:
                        raise ValueError("text: Réplique vide.")
                    turn.update(speaker=turn_speaker, text=text)
                except ValueError as exc:
                    reference_errors.append(f"{turn_field}.{exc}")
    if reference_errors:
        raise ValueError(
            "Références du scénario à corriger ensemble: "
            + "\n".join(reference_errors)
            + "\nDéclarez tous les lieux dans entities. Attribuez chaque réplique à un "
            "personnage connu; une notification sans personnage parlant est un son ou "
            "une action, pas une réplique sans locuteur."
        )
    total_duration = 0
    display_values = dict(context.get("display_values") or {})
    if not result["scenes"]:
        raise ValueError("L'épisode doit contenir au moins une scène.")
    for scene_index, scene in enumerate(result["scenes"]):
        if (
            not isinstance(scene, dict)
            or not isinstance(scene.get("shots"), list)
            or not scene["shots"]
        ):
            raise ValueError("Le scénario contient une scène vide.")
        for shot_index, shot in enumerate(scene["shots"]):
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
                raise ValueError("Chaque plan doit viser une durée utile entre 1 et 15 secondes.")
            duration = round(duration, 1)
            turns = shot.get("dialogue_turns") or []
            if not isinstance(turns, list) or len(turns) > 30:
                raise ValueError("Les tours de parole doivent être une liste ordonnée.")
            for turn_index, turn in enumerate(turns):
                for key in ("action", "reaction"):
                    if key in turn:
                        validate_h3_prose(turn[key])
            dialogue = (
                " ".join(t["text"] for t in turns)
                if turns
                else str(shot.get("dialogue") or "").strip()
            )
            speaker = canonical_key(shot.get("speaker"), all_entities, "character")
            if turns:
                voices = {t["speaker"] for t in turns}
                speaker = turns[0]["speaker"] if len(voices) == 1 else ""
            try:
                if not turns:
                    dialogue, speaker = normalize_dialogue(dialogue, speaker, all_entities)
            except ValueError as exc:
                field = f"scenes[{scene_index}].shots[{shot_index}].dialogue"
                excerpt = str(shot.get("dialogue"))[:240]
                raise ValueError(f"{field}: {exc} Extrait brut: {excerpt!r}") from None
            if not dialogue:
                speaker = ""
            if speaker and speaker not in characters:
                raise ValueError(
                    f"scenes[{scene_index}].shots[{shot_index}].speaker: "
                    f"Le locuteur {speaker!r} est inconnu. "
                    f"Clés de personnages autorisées: {sorted(characters)!r}."
                )
            if dialogue and not speaker and not turns:
                raise ValueError("Chaque réplique doit avoir un unique locuteur identifié.")
            keys = shot.get("character_keys") or []
            if isinstance(keys, list):
                keys = [canonical_key(key, all_entities, "character") for key in keys]
            location = canonical_key(shot.get("location_key"), all_entities, "location")
            if (
                not isinstance(keys, list)
                or any(key not in known for key in keys)
                or (location and location not in known)
            ):
                raise ValueError("Un plan utilise une référence étrangère à cette série.")
            speaking_keys = (
                [t["speaker"] for t in turns] if turns else [speaker] if dialogue else []
            )
            listeners = shot.get("listener_keys") or []
            if not isinstance(listeners, list):
                raise ValueError("Les personnages qui écoutent doivent être une liste de clés.")
            listeners = [canonical_key(key, all_entities, "character") for key in listeners]
            if any(key not in characters for key in listeners):
                raise ValueError("Un personnage qui écoute est inconnu dans cette série.")
            shot["listener_keys"] = listeners
            missing_keys = [
                key for key in dict.fromkeys(speaking_keys + listeners) if key not in keys
            ]
            if missing_keys:
                keys.extend(missing_keys)
                names = {e["key"]: e.get("name") or e["key"] for e in all_entities}
                participants = "speaking and listening" if listeners else "speaking"
                direction = (
                    f" Keep these {participants} characters visibly in the frame throughout the "
                    f"continuous shot: {', '.join(names[key] for key in missing_keys)}. "
                    "Use a composition that includes every speaking character; no offscreen speech."
                )
                for field in ("prompt", "image_prompt"):
                    shot[field] = str(shot[field]) + direction
            shot["duration_sec"] = duration
            total_duration += duration
            shot["dialogue"] = dialogue
            shot["speaker"] = speaker
            shot["character_keys"] = keys
            shot["location_key"] = location
            from calliope.studio.screen_text import ScreenText

            labels = shot.get("screen_text") or []
            if not isinstance(labels, list) or len(labels) > 12:
                raise ValueError("Liste de textes à l'écran invalide.")
            shot["screen_text"] = [
                ScreenText.model_validate(
                    label | {"number_format": profile["number_format"]}
                    if isinstance(label, dict) and label.get("value") is not None
                    else label
                ).model_dump()
                for label in labels
            ]
            for label in shot["screen_text"]:
                label["entity_key"] = canonical_key(label["entity_key"], all_entities, "character")
            if attached_required:
                missing = [
                    key
                    for key in keys
                    if key in required_values
                    and not any(
                        label["entity_key"] == key
                        and label["value"] is not None
                        and label["anchor"] == "character"
                        for label in shot["screen_text"]
                    )
                ]
                if missing:
                    raise ValueError(
                        f"scenes[{scene_index}].shots[{shot_index}].screen_text: "
                        f"valeurs attachées manquantes pour {', '.join(missing)}."
                    )
            label_keys = set()
            for label in shot["screen_text"]:
                if label["entity_key"] and label["entity_key"] not in keys:
                    raise ValueError("Un texte référence un personnage absent du plan.")
                if label["key"] in label_keys:
                    raise ValueError("Deux textes du même plan utilisent la même clé.")
                label_keys.add(label["key"])
                if attached_required and label["entity_key"] in required_values:
                    required_key, initial_value = required_values[label["entity_key"]]
                    if label["key"] != required_key:
                        raise ValueError(
                            f"Clé de valeur incohérente pour {label['entity_key']}: {required_key}."
                        )
                    prior_text = display_values.get(required_key)
                    expected_value = (
                        int(re.sub(r"[ ,]", "", prior_text))
                        if prior_text is not None
                        else initial_value
                    )
                    if label["value"] != expected_value and label["event"] not in (
                        "change",
                        "reveal",
                    ):
                        raise ValueError(
                            f"La valeur de {label['entity_key']} doit rester {expected_value} "
                            "sans événement structuré."
                        )
                changes = label.get("changes", [])
                if changes and label["event"] not in ("change", "reveal"):
                    raise ValueError(
                        "Un changement temporel exige un événement narratif change/reveal."
                    )
                if any(change["at_sec"] >= duration for change in changes):
                    raise ValueError(
                        "Un changement de valeur dépasse la durée utile prévue du plan."
                    )
                previous = display_values.get(label["key"])
                if (
                    previous is not None
                    and previous != label["text"]
                    and label["event"] not in ("change", "reveal")
                ):
                    raise ValueError("Une valeur affichée change sans événement narratif.")
                final_label = (
                    ScreenText.model_validate(label | {"value": changes[-1]["value"]})
                    if changes
                    else None
                )
                display_values[label["key"]] = final_label.text if final_label else label["text"]
            if shot["screen_text"]:
                from calliope.studio.visual_identity import strip_generated_text

                blank = (
                    " Leave clean blank space for the exact labels added in editing; "
                    "generate no text or digits."
                )
                for key in ("prompt", "image_prompt"):
                    shot[key] = strip_generated_text(shot[key], shot["screen_text"]) + blank
            shot["audio_mode"] = "native" if dialogue else shot.get("audio_mode", "native")
            if shot["audio_mode"] not in ("native", "silent"):
                raise ValueError("Mode audio invalide.")
            shot["prompt"] = re.sub(
                r"<d>.*?</d>", "", str(shot["prompt"]), flags=re.S | re.I
            ).strip()
            for field in ("action", "reaction", "framing", "camera", "soundscape"):
                if field in shot and (not isinstance(shot[field], str) or len(shot[field]) > 5000):
                    raise ValueError("Une directive de réalisation est invalide.")
                if field in shot:
                    validate_h3_prose(shot[field])
    if "target_seconds" in context:
        target = float(context["target_seconds"])
        tolerance = max(2.0, target * 0.1)
        if total_duration < target - tolerance:
            raise ValueError(
                f"Le scénario ne remplit que {total_duration:g} s utiles sur "
                f"les {target:g} s du format : l'histoire est trop courte. "
                "Développez l'événement ou ajoutez une action/réaction utile, "
                "sans jamais étirer un plan existant."
            )
        if total_duration > target + tolerance:
            raise ValueError(
                f"La durée utile du scénario ({total_duration:g} s) dépasse la "
                f"durée demandée ({target:g} s). Ajustez le découpage ou les "
                "répliques, sans étirer les plans."
            )
    raw_audio = result.get("audio") or {}
    if isinstance(raw_audio, dict):
        for cue in raw_audio.get("sfx") or []:
            if isinstance(cue, dict) and cue.get("effect") == "chime":
                cue["effect"] = "beep"
    result["audio"] = AudioSettings.model_validate(raw_audio).model_dump()
    profile = directing_profile(context.get("directing_profile"))
    if profile["music"] == "none":
        result["audio"]["music"] = "none"
    elif not result["audio"].get("music_asset_id"):
        result["audio"]["music"] = profile["music_style"]
    result["audio"]["music_gain_db"] = profile["music_gain_db"]
    result["audio"]["ambience_gain_db"] = profile["ambience_gain_db"]
    result["display_values"] = display_values
    for shot_index, shot in enumerate(s for scene in result["scenes"] for s in scene["shots"]):
        for label in shot.get("screen_text", []):
            effect = {"appear": "click", "change": "beep", "reveal": "impact"}.get(label["event"])
            if effect and not any(c["shot_index"] == shot_index for c in result["audio"]["sfx"]):
                result["audio"]["sfx"].append(
                    {
                        "effect": effect,
                        "shot_index": shot_index,
                        "at_sec": label.get("changes", [{}])[0].get("at_sec", 0.15)
                        if label.get("changes")
                        else 0.15,
                        "gain_db": -18,
                    }
                )
    result["bible"] = {
        key: str(value) for key, value in (result.get("bible") or {}).items() if key in BIBLE_FIELDS
    }
    if not isinstance(result.get("events", []), list):
        raise ValueError("Le récapitulatif de continuité est invalide.")
    return result
