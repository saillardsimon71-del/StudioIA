import asyncio
import hashlib
import json
import re
from pathlib import Path

from calliope.config import settings
from calliope.events.bus import event_bus
from calliope.queue.manager import queue_manager
from calliope.studio import audio, series, service
from calliope.studio.series_planner import append_outro, plan_episode
from calliope.studio.visual_identity import (
    compile_image_request,
    file_hash,
    ordered_entities,
    reference_bindings,
    validate_value_manifest,
)


class ProductionPausedError(Exception):
    pass


async def checkpoint(job, stage, progress, message, should_stop):
    if should_stop():
        raise asyncio.CancelledError()
    with service.database() as conn:
        row = conn.execute(
            "SELECT pause_requested FROM studio_productions WHERE project_id=?",
            (job["project_id"],),
        ).fetchone()
        if row[0] or queue_manager.is_cancelled(job["id"]):
            conn.execute(
                (
                    "UPDATE studio_productions SET status='paused',message='Production en p"
                    "ause' WHERE project_id=?"
                ),
                (job["project_id"],),
            )
            conn.commit()
            raise ProductionPausedError()
        conn.execute(
            (
                "UPDATE studio_productions SET status='running',stage=?,progress=?,message="
                "?,error=NULL WHERE project_id=?"
            ),
            (stage, progress, message, job["project_id"]),
        )
        conn.commit()
    await event_bus.publish(
        "job.progress",
        {
            "job_id": job["id"],
            "project_id": job["project_id"],
            "progress": progress,
            "message": message,
        },
    )


async def run_child(job_id, should_stop):
    job = queue_manager.get_job(job_id)
    if job["status"] == "done":
        return json.loads(job.get("output_paths_json") or "[]")
    if job["error"] == "cancelled":
        raise ValueError(
            "Une prise a été annulée. Générez et choisissez une nouvelle prise avant de reprendre."
        )
    with service.database() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='running',error=NULL,started_at=CURRENT_TIMESTAMP WHERE id=? "
            "AND status IN ('pending','failed') AND error IS NOT 'cancelled'",
            (job_id,),
        )
        conn.commit()
        if not cur.rowcount:
            raise ValueError('Ce travail est déjà actif ou annulé.')
    try:
        outputs = await service.run_studio_job(job, json.loads(job["payload_json"]), should_stop)
        queue_manager.mark_done(job_id, outputs)
        return outputs
    except asyncio.CancelledError:
        with service.database() as conn:
            conn.execute(
                "UPDATE jobs SET status='pending' WHERE id=? AND status='running'", (job_id,)
            )
            conn.commit()
        raise
    except Exception as exc:
        queue_manager.mark_failed(job_id, service.safe_error(exc))
        raise


def save_episode_plan(project_id, plan):
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = dict(
            conn.execute(
                "SELECT * FROM studio_episodes WHERE project_id=?", (project_id,)
            ).fetchone()
        )
        if conn.execute("SELECT 1 FROM scenes WHERE project_id=?", (project_id,)).fetchone():
            return
        current = series.require_series(conn, episode["series_id"])
        bible = {
            k: current["bible"][k] or plan.get("bible", {}).get(k, "") for k in series.BIBLE_FIELDS
        }
        conn.execute(
            "UPDATE studio_series SET bible_json=? WHERE id=?",
            (json.dumps(bible), episode["series_id"]),
        )
        for entity in plan.get("entities", []):
            conn.execute(
                (
                    "INSERT OR IGNORE INTO studio_entities(series_id,entity_key,kind,name,d"
                    "escription,identity_json) VALUES(?,?,?,?,?,?)"
                ),
                (
                    episode["series_id"],
                    entity["key"],
                    entity["kind"],
                    entity.get("name") or entity["key"],
                    entity["description"],
                    json.dumps(entity.get("identity") or {}),
                ),
            )
        script = []
        shot_ids = []
        for scene in plan["scenes"]:
            scene_id = service.add_scene(conn, project_id, scene)
            shots = conn.execute(
                "SELECT id FROM clips WHERE scene_id=? ORDER BY order_index,id", (scene_id,)
            ).fetchall()
            script.append(scene.get("heading", ""))
            for row, shot in zip(shots, scene["shots"]):
                shot_ids.append(row[0])
                conn.execute(
                    "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?)",
                    (
                        row[0],
                        json.dumps(
                            {
                                **{
                                    key: shot[key]
                                    for key in ('action', 'reaction', 'framing', 'camera',
                                                'language', 'purpose', 'dialogue_turns',
                                                'soundscape', 'screen_text')
                                    if key in shot
                                },
                                "character_keys": shot.get("character_keys", []),
                                "location_key": shot.get("location_key", ""),
                                "speaker": shot.get("speaker", ""),
                                "reference_key": shot.get("reference_key", ""),
                            }
                        ),
                    ),
                )
                script.extend([shot.get("description", ""), shot.get("dialogue", "")])
        conn.execute(
            "UPDATE projects SET title=? WHERE id=?",
            (str(plan.get("title") or current["title"])[:200], project_id),
        )
        conn.execute(
            "UPDATE studio_projects SET script_source=? WHERE project_id=?",
            ("\n".join(script), project_id),
        )
        conn.execute(
            (
                "UPDATE studio_episodes SET plan_json=?,summary=?,events_json=?,ongoing_plo"
                "t=? WHERE project_id=?"
            ),
            (
                json.dumps(plan),
                str(plan.get("summary") or ""),
                json.dumps(plan.get("events") or []),
                str(plan.get("ongoing_plot") or ""),
                project_id,
            ),
        )
        audio_config = plan.get("audio") or audio.AudioSettings().model_dump()
        saved_audio = conn.execute(
            "SELECT settings_json FROM studio_audio WHERE project_id=?", (project_id,)
        ).fetchone()
        if saved_audio:
            audio_config = audio_config | {
                key: value for key, value in json.loads(saved_audio[0]).items()
                if key not in ("sfx", "holds")
            }
        for cue in audio_config.get("sfx", []) + audio_config.get("holds", []):
            if cue["shot_index"] < len(shot_ids):
                cue["shot_id"] = shot_ids[cue["shot_index"]]
        conn.execute(
            "INSERT INTO studio_audio(project_id,settings_json) VALUES(?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json",
            (project_id, json.dumps(audio_config)),
        )
        conn.commit()


def ensure_outro(project_id):
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = conn.execute(
            "SELECT context_json,plan_json,series_id FROM studio_episodes WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if not episode:
            return
        last = conn.execute(
            "SELECT c.id,c.scene_id,sc.context_json,ss.dialogue FROM clips c "
            "JOIN scenes s ON s.id=c.scene_id JOIN studio_shots ss ON ss.clip_id=c.id "
            "LEFT JOIN studio_shot_context sc ON sc.clip_id=c.id WHERE c.project_id=? "
            "ORDER BY s.order_index DESC,s.id DESC,c.order_index DESC,c.id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        context = json.loads(episode["context_json"])
        plan = append_outro(json.loads(episode["plan_json"] or "{}"), context)
        ending = plan["scenes"][-1]
        shot = ending["shots"][0]
        last_context = json.loads(last["context_json"] or "{}") if last else {}
        has_outro = last_context.get("purpose") == "outro"
        if (
            has_outro and last_context.get("language") == shot["language"]
            and last["dialogue"] == shot["dialogue"]
        ):
            return
        host = next(
            e for e in context.get("entities", []) + plan["entities"]
            if e["key"] == ending["shots"][0]["speaker"]
        )
        conn.execute(
            "INSERT OR IGNORE INTO studio_entities(series_id,entity_key,kind,name,description) "
            "VALUES(?,?,?,?,?)",
            (episode["series_id"], host["key"], host["kind"], host["name"], host["description"]),
        )
        if has_outro:
            clip_id = last["id"]
            conn.execute(
                "UPDATE studio_shots SET dialogue=?,selected_video_id=NULL,approved=0,"
                "trim_start_sec=NULL,trim_end_sec=NULL WHERE clip_id=?",
                (shot["dialogue"], clip_id),
            )
            conn.execute("UPDATE clips SET clip_path=NULL WHERE id=?", (clip_id,))
            conn.execute(
                "UPDATE scenes SET dialog=? WHERE id=?", (shot["dialogue"], last["scene_id"])
            )
            service.touch(conn, project_id)
        else:
            scene_id = service.add_scene(conn, project_id, ending)
            clip_id = conn.execute(
                "SELECT id FROM clips WHERE scene_id=?", (scene_id,)
            ).fetchone()[0]
        fields = ("action", "reaction", "framing", "camera", "language", "purpose",
                  "speaker", "character_keys", "location_key", "reference_key")
        conn.execute(
            "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?) "
            "ON CONFLICT(clip_id) DO UPDATE SET context_json=excluded.context_json",
            (clip_id, json.dumps({key: shot[key] for key in fields})),
        )
        conn.execute(
            "UPDATE studio_episodes SET plan_json=? WHERE project_id=?",
            (json.dumps(plan), project_id),
        )
        conn.commit()


def image_references(conn, shot_id):
    context = service.shot_context(conn, shot_id)
    info = context.get("shot") or {}
    keys = list(info.get("character_keys") or [])
    if info.get("location_key"):
        keys.append(info["location_key"])
    return [
        binding["path"]
        for binding in reference_bindings(
            context["entities"],
            keys,
            context.get("directing_profile", {}).get("require_reference_approval", False),
        )
    ]


def generation_id(job, shot, kind, attempt=0):
    inputs = [
        shot["prompt"] if kind == "video" else shot["image_prompt"],
        shot["dialogue"],
        shot.get("dialogue_turns", []),
        shot.get("soundscape", ""),
        shot["duration_sec"],
        shot["selected_image_id"] if kind == "video" else None,
        settings.studio_image_model,
        settings.studio_modal_app,
        settings.studio_modal_function,
    ]
    if kind == "image":
        with service.database() as conn:
            inputs.append(compile_image_request(shot, service.shot_context(conn, shot["id"])))
    else:
        from calliope.studio.h3_prompt import compile_h3_prompt

        with service.database() as conn:
            context = service.shot_context(conn, shot["id"])
            inputs.append(
                compile_h3_prompt(
                    shot | context.get("shot", {}),
                    context,
                    first_frame=bool(shot["selected_image_id"] or shot["continue_from_previous"]),
                )
            )
            inputs.append(service.visual_review_contract(context, shot))
            source = conn.execute(
                "SELECT output_path FROM studio_takes WHERE id=?", (shot["selected_image_id"],)
            ).fetchone()
            if source and Path(source[0] or "").is_file():
                inputs.append(file_hash(source[0]))
            if shot["continue_from_previous"]:
                previous = service._previous_video(conn, shot["id"])
                inputs.append(file_hash(previous["output_path"]))
                inputs.append(
                    conn.execute(
                        "SELECT trim_end_sec FROM studio_shots WHERE clip_id=?",
                        (previous["clip_id"],),
                    ).fetchone()[0]
                )
    digest = hashlib.sha256(json.dumps(inputs).encode()).hexdigest()[:16]
    suffix = f"-r{attempt}" if attempt else ""
    return f"auto-{job['id']}-{shot['id']}-{kind}-{digest}{suffix}"


async def generate_take(job, shot, kind, should_stop, attempt=0):
    request_id = generation_id(job, shot, kind, attempt)
    with service.database() as conn:
        previous = conn.execute(
            "SELECT t.* FROM studio_takes t JOIN jobs j ON j.id=t.job_id "
            "WHERE t.clip_id=? AND t.kind=? AND json_extract(j.payload_json,'$.parent_job_id')=? "
            "AND t.request_id LIKE ? AND j.error IS NOT 'cancelled' "
            "AND (j.status!='done' OR ?=1) ORDER BY t.id DESC LIMIT 1",
            (shot["id"], kind, job["id"], "%-r1" if attempt else "%", attempt),
        ).fetchone()
    if (
        previous
        and previous["request_id"] != request_id
        and not previous["modal_call_id"]
        and previous["submission_state"] == "new"
    ):
        with service.database() as conn:
            conn.execute(
                "UPDATE jobs SET status='failed',error='cancelled' WHERE id=?",
                (previous["job_id"],),
            )
            conn.execute(
                "UPDATE studio_takes SET status='failed',error=? WHERE id=?",
                ("Demande remplacée après modification des entrées", previous["id"]),
            )
            conn.commit()
        previous = None
    if previous and (attempt or not previous["request_id"].endswith("-r1")):
        await run_child(previous["job_id"], should_stop)
        return service.select_take(previous["id"])
    request = {
        "kind": kind,
        "request_id": request_id,
        "n_takes": 1,
        "parent_job_id": job["id"],
    }
    queued = service.enqueue_takes(shot["id"], request)
    take = queued["takes"][0]
    await run_child(take["job_id"], should_stop)
    return service.select_take(take["id"])


def selected_video(shot):
    return next((t for t in shot["takes"] if t["id"] == shot["selected_video_id"]), None)


async def assess_take(shot):
    """Mesure la piste native d'une prise à dialogue et juge la réplique.

    Tolérant par construction : seuls les échecs manifestes sont renvoyés
    (réplique absente, invention importante, audio inexploitable). Le reste se
    corrige au trim, jamais au rerender.
    """
    from calliope.studio import pacing, transcription
    from calliope.studio.h3_prompt import spoken_dialogue

    take = selected_video(shot)
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
    dialogue = spoken_dialogue(shot | context.get("shot", {}), context)
    if take and not dialogue:
        metadata = take.get("metadata") or {}
        metadata.pop("dialogue_qc", None)
        verdict = None
        if shot["audio_mode"] == "native" and metadata.get("has_audio") is False:
            verdict = {
                "status": "inaudible",
                "reason": "La piste sonore native attendue est absente.",
                "voice": None,
            }
            metadata["dialogue_qc"] = verdict
            service.render_event(take["id"], "FAILED", verdict["reason"])
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_takes SET metadata_json=? WHERE id=?",
                (json.dumps(metadata), take["id"]),
            )
            conn.commit()
        return verdict
    if (
        not take
        or not dialogue
        or shot["audio_mode"] != "native"
        or not take["output_path"]
        or not Path(take["output_path"]).is_file()
    ):
        return None
    metadata = take.get("metadata") or {}
    service.render_event(take["id"], "VALIDATING", "Contrôle de la parole native")
    probe = float(metadata.get("duration_sec") or 0)
    if not metadata.get("has_audio"):
        verdict = {
            "status": "inaudible",
            "reason": "Audio inexploitable : pas de piste son native.",
            "voice": None,
        }
        metadata["dialogue_qc"] = verdict
        service.render_event(take["id"], "FAILED", verdict["reason"])
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_takes SET metadata_json=? WHERE id=?",
                (json.dumps(metadata), take["id"]),
            )
            conn.commit()
        return verdict
    stat = Path(take["output_path"]).stat()
    language = context.get("shot", {}).get("language") or context.get("language", "en")
    signature = [
        stat.st_size,
        stat.st_mtime_ns,
        settings.studio_asr_checkpoint,
        language,
    ]
    cached = metadata.get("transcription_cache") or {}
    if cached.get("signature") == signature:
        transcript = cached["transcript"]
        voice = cached.get("activity")
    else:
        voice = await asyncio.to_thread(pacing.detect_voice, take["output_path"], probe)
        transcript = await asyncio.to_thread(
            transcription.transcribe_dialogue,
            take["output_path"],
            settings.studio_asr_checkpoint,
            language,
        )
        metadata["transcription_cache"] = {
            "signature": signature,
            "transcript": transcript,
            "activity": voice,
        }
    verdict = pacing.assess_dialogue(dialogue, shot["duration_sec"], probe, voice)
    if verdict["status"] not in ("missing", "inaudible"):
        verdict = transcription.assess_transcript(dialogue, transcript)
    if context.get("shot", {}).get("dialogue_turns") and verdict["status"] not in (
        "missing",
        "extra",
        "inaudible",
    ):
        turns = transcription.assess_turns(context["shot"]["dialogue_turns"], transcript)
        verdict["turns"] = turns.get("turns", [])
        if turns["status"] == "missing":
            verdict.update(status="missing", reason=turns["reason"])
    verdict.setdefault("voice", (
        transcript.get("speech_intervals") if transcript.get("status") == "ok" else None
    ))
    metadata["dialogue_qc"] = verdict
    if verdict["status"] == "extra":
        with service.database() as conn:
            request_row = conn.execute(
                "SELECT request_json FROM studio_takes WHERE id=?", (take["id"],)
            ).fetchone()
        compiled = json.loads(request_row[0] or "{}").get("prompt") or take.get("prompt", "")
        diagnosis = transcription.assess_prompt_recitation(dialogue, transcript, compiled)
        if diagnosis:
            metadata["dialogue_qc"]["diagnostic"] = diagnosis
    service.render_event(
        take["id"],
        "FAILED" if verdict["status"] in ("missing", "extra", "inaudible") else "DONE",
        verdict.get("reason", "Contrôle terminé"),
    )
    with service.database() as conn:
        conn.execute(
            "UPDATE studio_takes SET metadata_json=? WHERE id=?", (json.dumps(metadata), take["id"])
        )
        conn.commit()
    return verdict


def trim_take(shot, voice=None, end_limit=None):
    """Calcule et enregistre la fenêtre utile d'une prise conservée.

    Une prise déjà à la bonne taille n'a aucun point à enregistrer ; le montage
    la garde entière. Aucun rallongement n'est jamais produit.
    """
    from calliope.studio import pacing

    take = selected_video(shot)
    if not take or not take.get("metadata"):
        return
    probe = float(take["metadata"].get("duration_sec") or 0)
    if probe <= 0:
        return
    if shot["audio_mode"] == "native" and shot["dialogue"].strip() and not voice:
        return
    start, end = pacing.useful_window(shot["duration_sec"], probe, voice)
    if end_limit is not None:
        end = min(end, pacing.floor_grid(end_limit))
    if start <= 1e-9 and end >= probe - 1e-9:
        return
    with service.database() as conn:
        if service.save_trim(conn, shot["id"], start, end):
            conn.commit()


def reuse_reference(project_id, shot):
    with service.database() as conn:
        episode = conn.execute(
            "SELECT series_id FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        context_row = conn.execute(
            "SELECT context_json FROM studio_shot_context WHERE clip_id=?", (shot["id"],)
        ).fetchone()
        info = json.loads(context_row[0]) if context_row else {}
        key = info.get("reference_key")
        if not key:
            return False
        compiled = compile_image_request(shot, service.shot_context(conn, shot["id"]))
        candidates = conn.execute(
            (
                "SELECT t.output_path,t.request_json,sc.context_json FROM studio_takes t JO"
                "IN studio_shot_context sc ON sc.clip_id=t.clip_id JOIN studio_episodes e O"
                "N e.project_id=t.project_id WHERE e.series_id=? AND t.kind='image' AND t.s"
                "tatus='done' AND t.clip_id!=? ORDER BY t.id DESC"
            ),
            (episode[0], shot["id"]),
        ).fetchall()
        path = None
        for item in candidates:
            previous = json.loads(item["context_json"])
            request = json.loads(item["request_json"])
            if (
                previous.get("reference_key") == key
                and previous.get("character_keys") == info.get("character_keys")
                and previous.get("location_key") == info.get("location_key")
                and request.get("reference_bindings", []) == compiled["reference_bindings"]
                and request.get("input_hashes", {}) == compiled["input_hashes"]
                and Path(item["output_path"] or "").is_file()
            ):
                path = item["output_path"]
                break
    if path:
        take = service.store_upload(shot["id"], Path(path).read_bytes(), "image")
        service.select_take(take["id"])
        return True
    return False


async def ensure_entities(job, should_stop):
    with service.database() as conn:
        episode = conn.execute(
            "SELECT series_id,context_json FROM studio_episodes WHERE project_id=?",
            (job["project_id"],),
        ).fetchone()
        frozen = json.loads(episode["context_json"])
        items = series.entities(conn, episode["series_id"])
        planned = json.loads(
            conn.execute(
                "SELECT plan_json FROM studio_episodes WHERE project_id=?", (job["project_id"],)
            ).fetchone()[0]
        )
        canon = {e["key"]: e for e in planned.get("entities", [])}
        canon.update({e["key"]: e for e in frozen.get("entities", [])})
        items = [entity for entity in items if entity["key"] in canon]
        context = frozen | {
            "entities": items,
            "bible": {
                **planned.get("bible", {}),
                **{k: v for k, v in frozen.get("bible", {}).items() if v},
            },
        }
        items = ordered_entities(items)
        strict = context.get("directing_profile", {}).get("require_reference_approval", False)
    pending = []
    for index, entity in enumerate(items):
        await checkpoint(
            job,
            "images",
            0.12 + index / max(1, len(items)) * 0.08,
            f"Référence : {entity['name']}",
            should_stop,
        )
        if entity["reference_path"] and Path(entity["reference_path"]).is_file():
            if strict:
                try:
                    reference_bindings(items, [entity["key"]], True)
                except ValueError:
                    pending.append(entity["name"])
            continue
        parent = entity["identity"].get("variant_of")
        if strict and parent:
            try:
                reference_bindings(items, [parent], True)
            except ValueError:
                pending.append(entity["name"])
                continue
        directory = settings.assets_dir / "series" / str(episode["series_id"]) / "references"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"entity-{entity['id']}.png"
        try:
            compiled = compile_image_request({}, context, canonical_entity=entity)
        except ValueError:
            if not strict:
                raise
            pending.append(entity["name"])
            continue
        request = compiled | {
            "aspect_ratio": frozen["aspect_ratio"],
            "image_base_url": settings.studio_image_base_url,
            "image_model": settings.studio_image_model,
        }
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_entities SET reference_request_json=? WHERE id=?",
                (json.dumps(request), entity["id"]),
            )
            conn.commit()
        content, _ = await service._generate_image(request)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
        entity["reference_path"] = str(path)
        entity["identity"].update(reference_approved=False, reference_sha256="")
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_entities SET reference_path=?,identity_json=? "
                "WHERE id=? AND series_id=?",
                (str(path), json.dumps(entity["identity"]), entity["id"], episode["series_id"]),
            )
            conn.commit()
        if strict:
            pending.append(entity["name"])
    if pending:
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_productions SET status='paused',stage='images',message=? "
                "WHERE project_id=?",
                ("Références à vérifier et approuver: " + ", ".join(pending), job["project_id"]),
            )
            conn.commit()
        raise ProductionPausedError()


def load_shot(shot_id):
    with service.database() as conn:
        return service.public_shot(conn, shot_id)


async def render_shot(job, initial, should_stop):
    from calliope.studio.h3_prompt import compile_h3_prompt

    if should_stop():
        raise asyncio.CancelledError()
    if queue_manager.is_cancelled(job["id"]):
        raise ProductionPausedError()
    shot = load_shot(initial["id"])
    chosen = selected_video(shot)
    if chosen and not shot["approved"] and chosen["engine"] != "import":
        with service.database() as conn:
            context = service.shot_context(conn, shot["id"])
        expected = compile_h3_prompt(
            shot | context.get("shot", {}),
            context,
            first_frame=bool(shot["selected_image_id"] or shot["continue_from_previous"]),
        )
        legacy = shot["prompt"] + (
            "\n<d>" + shot["dialogue"].strip() + "</d>"
            if shot["dialogue"].strip() and shot["audio_mode"] == "native"
            else ""
        )
        if chosen["prompt"] not in (expected, legacy):
            chosen = None
    if not chosen:
        try:
            shot = await generate_take(job, shot, "video", should_stop)
        except service.HTTPException as exc:
            if exc.status_code != 422:
                raise
            with service.database() as conn:
                conn.execute(
                    "UPDATE studio_productions SET status='paused',message=? WHERE project_id=?",
                    (exc.detail, job["project_id"]),
                )
                conn.commit()
            raise ProductionPausedError() from None
    verdict = await assess_take(shot)
    if verdict and verdict["status"] in ("missing", "extra", "inaudible"):
        service.approve_shot(shot["id"], False)
        shot = await generate_take(job, shot, "video", should_stop, attempt=1)
        verdict = await assess_take(shot)
        if verdict and verdict["status"] in ("missing", "extra", "inaudible"):
            raise ValueError(f"Plan {shot['id']} bloqué après une relance : {verdict['reason']}")
    trim_take(shot, (verdict or {}).get("voice"), (verdict or {}).get("trim_end_limit_sec"))
    if should_stop():
        raise asyncio.CancelledError()
    if queue_manager.is_cancelled(job["id"]):
        raise ProductionPausedError()
    if verdict and verdict["status"] == "unknown" and not shot["approved"]:
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_productions SET status='paused',message=? WHERE project_id=?",
                ("Contrôle parole incertain : écoutez la vidéo, puis approuvez le plan "
                 "si le son est correct, ou régénérez cette prise.", job["project_id"]),
            )
            conn.commit()
        raise ProductionPausedError()
    try:
        service.approve_shot(shot["id"], True)
    except service.HTTPException as exc:
        if exc.status_code != 422:
            raise
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_productions SET status='paused',message=? WHERE project_id=?",
                (exc.detail, job["project_id"]),
            )
            conn.commit()
        raise ProductionPausedError() from None


async def render_videos(job, shots, should_stop):
    semaphore = asyncio.Semaphore(settings.studio_video_concurrency)
    aborted = asyncio.Event()
    tasks = []

    async def render(index, shot):
        if shot["continue_from_previous"] and index:
            await tasks[index - 1]
        async with semaphore:
            if aborted.is_set():
                return
            try:
                await checkpoint(
                    job,
                    "video",
                    0.33 + index / len(shots) * 0.55,
                    f"FastH3 : plan {index + 1}/{len(shots)}",
                    should_stop,
                )
                await render_shot(job, shot, should_stop)
            except BaseException:
                aborted.set()
                raise

    for index, shot in enumerate(shots):
        tasks.append(asyncio.create_task(render(index, shot)))
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_production(job, payload, should_stop):
    project_id = payload["studio_production_id"]
    try:
        await checkpoint(job, "writing", 0.02, "Écriture de l'épisode et du découpage", should_stop)
        with service.database() as conn:
            row = conn.execute(
                "SELECT * FROM studio_episodes WHERE project_id=?", (project_id,)
            ).fetchone()
            episode = dict(row)
            has_plan = bool(
                conn.execute("SELECT 1 FROM scenes WHERE project_id=?", (project_id,)).fetchone()
            )
        if not has_plan:
            plan = await plan_episode(
                json.loads(episode["context_json"])
                | {"_planner_trace": {"project_id": project_id, "job_id": job["id"]}},
                episode["idea"],
                episode["objective"],
            )
            await checkpoint(job, "writing", 0.1, "Scénario préparé", should_stop)
            save_episode_plan(project_id, plan)
        ensure_outro(project_id)
        with service.database() as conn:
            snapshot = json.loads(episode["context_json"])
            expected_values = {
                key: int(re.sub(r"[ ,]", "", str(value)))
                for key, value in snapshot.get("display_values", {}).items()
                if re.fullmatch(r"-?[\d ,]+", str(value))
            }
            for row in conn.execute(
                "SELECT c.id FROM clips c JOIN scenes s ON s.id=c.scene_id WHERE c.project_id=? "
                "ORDER BY s.order_index,s.id,c.order_index,c.id",
                (project_id,),
            ).fetchall():
                try:
                    validate_value_manifest(
                        service.public_shot(conn, row[0]),
                        service.shot_context(conn, row[0]),
                        expected_values,
                    )
                except ValueError as exc:
                    conn.execute(
                        "UPDATE studio_productions SET status='paused',message=? "
                        "WHERE project_id=?",
                        (f"Plan {row[0]}: {exc}", project_id),
                    )
                    conn.commit()
                    raise ProductionPausedError() from None
        await ensure_entities(job, should_stop)
        board = service.board(project_id)
        shots = [s for scene in board["scenes"] for s in scene["shots"]]
        for index, initial in enumerate(shots):
            await checkpoint(
                job,
                "images",
                0.2 + index / len(shots) * 0.12,
                f"Image du plan {index + 1}/{len(shots)}",
                should_stop,
            )
            with service.database() as conn:
                shot = service.public_shot(conn, initial["id"])
            if not shot["selected_image_id"] and not shot["continue_from_previous"]:
                if not reuse_reference(project_id, shot):
                    shot = await generate_take(job, shot, "image", should_stop)
        with service.database() as conn:
            for initial in shots:
                shot = service.public_shot(conn, initial["id"])
                if shot["continue_from_previous"]:
                    continue
                source = conn.execute(
                    "SELECT * FROM studio_takes WHERE id=?", (shot["selected_image_id"],)
                ).fetchone()
                try:
                    service.require_source_visual_review(
                        source, shot, service.shot_context(conn, shot["id"])
                    )
                except service.HTTPException as exc:
                    conn.execute(
                        "UPDATE studio_productions SET status='paused',message=? "
                        "WHERE project_id=?",
                        (exc.detail, project_id),
                    )
                    conn.commit()
                    raise ProductionPausedError() from None
        await render_videos(job, shots, should_stop)
        message = "Musique, bruitages et assemblage du MP4"
        await checkpoint(job, "mixing", 0.9, message, should_stop)
        revision = service.board(project_id)["project"]["revision"]
        queued = await service.enqueue_export(
            project_id, f"auto-{job['id']}-export-{revision}", parent_job_id=job["id"]
        )
        outputs = await run_child(queued["job_id"], should_stop)
        with service.database() as conn:
            conn.execute(
                (
                    "UPDATE studio_productions SET status='done',stage='done',progress=1,me"
                    "ssage='Épisode prêt',error=NULL WHERE project_id=?"
                ),
                (project_id,),
            )
            conn.commit()
        return outputs
    except ProductionPausedError:
        with service.database() as conn:
            conn.execute("UPDATE studio_productions SET status='paused' WHERE project_id=?",
                         (project_id,))
            conn.commit()
        return []
    except Exception as exc:
        with service.database() as conn:
            conn.execute(
                (
                    "UPDATE studio_productions SET status='failed',error=?,message='Product"
                    "ion interrompue' WHERE project_id=?"
                ),
                (service.safe_error(exc), project_id),
            )
            conn.commit()
        raise RuntimeError(service.safe_error(exc)) from None


async def commit_episode(project_id, revision):
    with service.database() as conn:
        row = conn.execute(
            "SELECT * FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            return
        episode = dict(row)
        current = conn.execute(
            "SELECT revision FROM studio_projects WHERE project_id=?", (project_id,)
        ).fetchone()[0]
        if current != revision:
            return
        plan = json.loads(episode["plan_json"])
        actual = [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT c.description,ss.dialogue,ss.prompt FROM clips c JOIN studio_sh"
                    "ots ss ON ss.clip_id=c.id JOIN scenes s ON s.id=c.scene_id WHERE c.pro"
                    "ject_id=? ORDER BY s.order_index,s.id,c.order_index,c.id"
                ),
                (project_id,),
            )
        ]
    original = [
        {k: str(shot.get(k) or "") for k in ("description", "dialogue", "prompt")}
        for scene in plan.get("scenes", [])
        for shot in scene["shots"]
    ]
    if actual != original:
        from calliope.agent.llm import generate_structured

        recap = await generate_structured(
            [
                {
                    "role": "system",
                    ("content"): (
                        "Summarize ONLY events shown in these final episode shot"
                        "s. Do not invent resolutions or future events. Return J"
                        'SON {"summary":"factual recap","events":["event"],"ongo'
                        'ing_plot":"unresolved question"}.'
                    ),
                },
                {"role": "user", "content": json.dumps(actual)},
            ],
            temperature=0.2,
        )
    else:
        recap = {
            "summary": episode["summary"],
            "events": json.loads(episode["events_json"]),
            "ongoing_plot": episode["ongoing_plot"],
        }
    if not isinstance(recap.get("events"), list) or not str(recap.get("summary") or "").strip():
        raise ValueError("Le récapitulatif de continuité est incomplet. Reprenez l'export.")
    with service.database() as conn:
        conn.execute(
            (
                "UPDATE studio_episodes SET summary=?,events_json=?,ongoing_plot=?,canon_re"
                "vision=? WHERE project_id=? AND EXISTS(SELECT 1 FROM studio_projects WHERE"
                " project_id=? AND revision=?)"
            ),
            (
                recap["summary"],
                json.dumps(recap["events"]),
                recap.get("ongoing_plot", ""),
                revision,
                project_id,
                project_id,
                revision,
            ),
        )
        conn.execute(
            "UPDATE studio_series SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (episode["series_id"],),
        )
        conn.commit()
