import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path

from calliope.config import settings
from calliope.events.bus import event_bus
from calliope.queue.manager import queue_manager
from calliope.studio import audio, series, service
from calliope.studio.h3_prompt import directing_profile
from calliope.studio.series_planner import _midroll_spec, insert_midroll, normalize_plan, plan_episode, prepare_native_states
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


async def run_child(job_id, should_stop, *, video_preparation=None):
    job = queue_manager.get_job(job_id)
    if video_preparation:
        job['video_preparation'] = video_preparation
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
                    "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?) "
                    "ON CONFLICT(clip_id) DO UPDATE SET context_json=excluded.context_json",
                    (
                        row[0],
                        json.dumps(
                            {
                                **{
                                    key: shot[key]
                                    for key in ('action', 'reaction', 'framing', 'camera',
                                                'language', 'purpose', 'dialogue_turns',
                                                'soundscape', 'screen_text', 'blocking',
                                                'listener_keys', 'prop_keys', 'start_state',
                                                'end_state', 'cut_reason', 'source_beat_index',
                                                'post_dub_turns')
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
        plan["editorial_script"] = series.editorial_script(conn, project_id, plan.get("prompt_contract_version") == 2)
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


def editorial_review(project_id):
    """Contrôle éditorial exécuté avant tout rendu et avant l'export.

    Le linter existait mais n'était appelé par personne. Il vérifie les coupes,
    la durée du CTA, les répliques ciblées du post-doublage et les états
    déclarés entre deux plans. Ses avertissements sont conservés dans le plan.
    """
    from calliope.studio import editorial

    with service.database() as conn:
        row = conn.execute(
            "SELECT plan_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        plan = json.loads(row[0] or "{}") if row else {}
    board = service.board(project_id)
    review = editorial.validate_plan({
        "post_dub": bool(plan.get("post_dub")),
        "scenes": board["scenes"],
    })
    if review.get("warnings") or review.get("errors"):
        plan["editorial_review"] = review
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_episodes SET plan_json=? WHERE project_id=?",
                (json.dumps(plan), project_id),
            )
            conn.commit()
    return review


def ensure_midroll(project_id):
    from calliope.studio import director
    with service.database() as conn:
        if director.enabled(conn, project_id):
            return
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = conn.execute(
            "SELECT context_json,plan_json,series_id FROM studio_episodes WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if not episode:
            return
        context = json.loads(episode["context_json"])
        if directing_profile(context.get("directing_profile"))["cta_placement"] == "none":
            return
        by_key = {e["key"]: e for e in series.entities(conn, episode["series_id"])}
        by_key.update({e["key"]: e for e in context.get("entities", [])})
        context["entities"] = list(by_key.values())
        plan = json.loads(episode["plan_json"] or "{}")
        if plan.get("prompt_contract_version", 1) == 1 and any(
            shot.get("purpose") == "midroll"
            for scene in plan.get("scenes", []) for shot in scene.get("shots", [])
        ) and directing_profile(context.get("directing_profile"))["cta_placement"] != "end":
            return
        if "editorial_script" not in plan:
            plan["editorial_script"] = [
                shot for scene in plan.get("scenes", []) for shot in scene["shots"]
                if shot.get("purpose") not in ("midroll", "outro")
            ]
        rows = conn.execute(
            "SELECT c.id,c.scene_id,c.order_index FROM clips c JOIN scenes s ON s.id=c.scene_id "
            "WHERE c.project_id=? ORDER BY s.order_index,s.id,c.order_index,c.id", (project_id,),
        ).fetchall()
        fields = ("action", "reaction", "framing", "camera", "language", "purpose",
                  "speaker", "character_keys", "location_key", "reference_key", "dialogue_turns",
                  "soundscape", "screen_text", "blocking", "listener_keys", "prop_keys",
                  "start_state", "end_state", "cut_reason")
        live_scenes = []
        for scene in conn.execute(
            "SELECT * FROM scenes WHERE project_id=? ORDER BY order_index,id", (project_id,)
        ).fetchall():
            shots = []
            for row in rows:
                if row["scene_id"] != scene["id"]:
                    continue
                public = service.public_shot(conn, row["id"])
                info = service.shot_context(conn, row["id"])["shot"]
                shots.append({key: public[key] for key in (
                    "id", "description", "prompt", "image_prompt", "dialogue", "duration_sec",
                    "audio_mode", "continue_from_previous",
                )} | info)
            live_scenes.append({"heading": scene["heading"], "action": scene["action"],
                                "dialog": scene["dialog"], "shots": shots})
        plan["scenes"] = live_scenes
        plan["audio"] = audio.get_audio(conn, project_id)
        plan["audio"].pop("assets")
        old_shots = [shot for scene in live_scenes for shot in scene["shots"]]
        promotions = [shot for shot in old_shots if shot.get("purpose") in ("outro", "midroll")]
        if len(promotions) > 1:
            raise ValueError(
                "Plusieurs incitations existent : conservez un seul plan d'incitation."
            )
        profile = directing_profile(context.get("directing_profile"))
        if plan.get("prompt_contract_version") == 2 and promotions:
            prepared = promotions[0]
            spec = _midroll_spec(context)
            if (profile["cta_placement"] == "end" and profile["camera"] == "static"
                and prepared is old_shots[-1] and prepared.get("purpose") == "midroll"
                and prepared.get("continue_from_previous")
                and prepared.get("dialogue") == spec["dialogue"]
                and prepared.get("dialogue_turns") == [{"speaker": prepared.get("speaker"), "text": spec["dialogue"]}]
                and prepared.get("speaker") in by_key
                and prepared.get("speaker") in prepared.get("character_keys", [])
                and float(prepared["duration_sec"]) >= spec["duration_sec"]):
                return
        insert_midroll(plan, context)
        shots = [shot for scene in plan["scenes"] for shot in scene["shots"]]
        index = next(i for i, shot in enumerate(shots) if shot.get("purpose") == "midroll")
        shot = shots[index]
        previous = next(row for row in rows if row["id"] == shots[index - 1]["id"])
        old = promotions[0] if promotions else None
        if old and old.get("purpose") == "midroll" and all(
            old.get(key) == shot.get(key) for key in fields + ("dialogue", "duration_sec")
        ) and old_shots.index(old) == index:
            return
        conn.execute(
            "UPDATE clips SET order_index=order_index+1 WHERE scene_id=? AND order_index>?",
            (previous["scene_id"], previous["order_index"]),
        )
        if old:
            clip_id = old["id"]
            conn.execute(
                "UPDATE clips SET scene_id=?,order_index=?,description=?,duration_sec=?,"
                "clip_path=NULL WHERE id=?",
                (previous["scene_id"], previous["order_index"] + 1, shot["description"],
                 shot["duration_sec"], clip_id),
            )
            conn.execute(
                "UPDATE studio_shots SET dialogue=?,prompt=?,image_prompt=?,audio_mode='native',"
                "selected_video_id=NULL,selected_image_id=NULL,approved=0,"
                "trim_start_sec=NULL,trim_end_sec=NULL,continue_from_previous=0 WHERE clip_id=?",
                (shot["dialogue"], shot["prompt"], shot["image_prompt"], clip_id),
            )
            conn.execute("DELETE FROM scenes WHERE project_id=? AND NOT EXISTS "
                         "(SELECT 1 FROM clips WHERE scene_id=scenes.id)", (project_id,))
        else:
            clip_id = service.add_shot(conn, project_id, previous["scene_id"], shot)
            conn.execute("UPDATE clips SET order_index=? WHERE id=?",
                         (previous["order_index"] + 1, clip_id))
        conn.execute(
            "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?) "
            "ON CONFLICT(clip_id) DO UPDATE SET context_json=excluded.context_json",
            (clip_id, json.dumps({key: shot[key] for key in fields if key in shot})),
        )
        shot["id"] = clip_id
        for cue in plan["audio"]["sfx"] + plan["audio"]["holds"]:
            cue["shot_id"] = shots[cue["shot_index"]]["id"]
        conn.execute("UPDATE studio_audio SET settings_json=? WHERE project_id=?",
                     (json.dumps(plan["audio"]), project_id))
        conn.execute(
            "UPDATE studio_episodes SET plan_json=? WHERE project_id=?",
            (json.dumps(plan), project_id),
        )
        service.touch(conn, project_id)
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
        settings.studio_video_profile if kind == "video" else None,
    ]
    if kind == "image":
        inputs.append(settings.studio_image_provider)
        with service.database() as conn:
            inputs.append(compile_image_request(shot, service.shot_context(conn, shot["id"])))
    else:
        from calliope.studio.h3_prompt import compile_video_prompt

        with service.database() as conn:
            context = service.shot_context(conn, shot["id"])
            inputs.append(
                compile_video_prompt(
                    shot | context.get("shot", {}),
                    context,
                    first_frame=bool(shot["selected_image_id"] or shot["continue_from_previous"]),
                    video_profile=settings.studio_video_profile,
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
        await run_child(previous["job_id"], should_stop, **(
            {'video_preparation': job['video_preparation']} if job.get('video_preparation') else {}
        ))
        return service.select_take(previous["id"])
    request = {
        "kind": kind,
        "request_id": request_id,
        "n_takes": 1,
        "parent_job_id": job["id"],
        "video_batch_last": kind == 'video' and job.get('video_batch_last', False),
    }
    queued = service.enqueue_takes(shot["id"], request)
    take = queued["takes"][0]
    await run_child(take["job_id"], should_stop, **(
        {'video_preparation': job['video_preparation']} if job.get('video_preparation') else {}
    ))
    return service.select_take(take["id"])


def selected_video(shot):
    return next((t for t in shot["takes"] if t["id"] == shot["selected_video_id"]), None)


def reusable_video(shot):
    from calliope.studio.h3_prompt import compile_video_prompt

    chosen = selected_video(shot)
    if chosen and not shot["approved"] and chosen["engine"] != "import":
        with service.database() as conn:
            context = service.shot_context(conn, shot["id"])
        expected = compile_video_prompt(
            shot | context.get("shot", {}), context,
            first_frame=bool(shot["selected_image_id"] or shot["continue_from_previous"]),
            video_profile=settings.studio_video_profile,
        )
        legacy = shot["prompt"] + (
            "\n<d>" + shot["dialogue"].strip() + "</d>"
            if shot["dialogue"].strip() and shot["audio_mode"] == "native" else ""
        )
        if chosen["prompt"] not in (expected, legacy):
            return None
    return chosen


def apply_loudness(verdict, loudness):
    """Ajoute la mesure EBU R128 sans transformer un pic natif en defaut de parole.

    Un pic proche de 0 dBFS est normal pour l'audio natif et l'export le
    normalise deja (-23 LUFS / -1 dBTP). Seul un niveau inexploitable bloque.
    """
    if not loudness:
        return verdict
    verdict["loudness"] = loudness
    if (
        verdict.get("status") in ("ok", "unknown")
        and loudness["integrated_lufs"] < -30
    ):
        verdict.update(
            status="audio_level",
            reason=f"Loudness trop faible ({loudness['integrated_lufs']:.1f} LUFS).",
        )
    return verdict


def apply_tts_status(verdict, metadata):
    """Rend explicite ce qu'un post-doublage ne prouve pas.

    Le texte transcrit peut etre exact alors que la voix, le locuteur et le
    lip-sync ont change. Ces dimensions restent non verifiees et ne doivent
    jamais etre presentees comme validees par le QC automatique.
    """
    tts = (metadata or {}).get("tts") or {}
    replaced = list(tts.get("replaced_turns") or [])
    if not replaced:
        return verdict
    verdict["replaced_turns"] = replaced
    verdict["voice_identity_status"] = "unverified"
    verdict["voice_identity_reason"] = tts.get("voice_identity_reason") or (
        "Post-doublage : identite vocale non verifiee."
    )
    verdict["lip_sync_status"] = tts.get("lip_sync_status") or "unverified"
    return verdict


async def assess_take(shot):
    """Enregistre un diagnostic de parole pour la relecture de la prise."""
    from calliope.studio import pacing, transcription
    from calliope.studio.h3_prompt import spoken_dialogue

    take = selected_video(shot)
    metadata = (take or {}).get("metadata") or {}
    if take and not settings.dry_run and Path(take.get('output_path') or '').is_file():
        from calliope.studio import generated_text

        stat = Path(take['output_path']).stat()
        signature = [stat.st_size, stat.st_mtime_ns]
        text_qc = metadata.get('text_qc') or {}
        if text_qc.get('signature') != signature or text_qc.get('status') == 'unverified':
            text_qc = await asyncio.to_thread(generated_text.scan, take['output_path'])
            metadata['text_qc'] = text_qc | {'signature': signature}
            with service.database() as conn:
                conn.execute('UPDATE studio_takes SET metadata_json=? WHERE id=?',
                             (json.dumps(metadata), take['id']))
                conn.commit()
    override = metadata.get("dialogue_qc_override")
    if take and override:
        # Une validation supervisee enregistree ne doit pas etre ecrasee par
        # la re-evaluation automatique qui precede l'export.
        verdict = dict(metadata.get("dialogue_qc") or {})
        verdict["status"] = "unknown"
        verdict["reason"] = override.get("reason") or "Validation supervisee."
        apply_tts_status(verdict, metadata)
        metadata["dialogue_qc"] = verdict
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_takes SET metadata_json=? WHERE id=?",
                (json.dumps(metadata), take["id"]),
            )
            conn.commit()
        return verdict
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
    dialogue = spoken_dialogue(shot | context.get("shot", {}), context)
    if take and not dialogue:
        metadata = take.get("metadata") or {}
        metadata.pop("dialogue_qc", None)
        verdict = None
        has_audio = metadata.get("has_audio") is not False
        output = Path(take.get("output_path") or "")
        if shot["audio_mode"] == "native" and not has_audio:
            verdict = {
                "status": "inaudible",
                "reason": "La piste sonore native attendue est absente.",
                "voice": None,
            }
        elif (
            shot["audio_mode"] == "native"
            and settings.studio_asr_checkpoint
            and output.is_file()
        ):
            language = context.get("shot", {}).get("language") or context.get("language", "fr")
            transcript = await asyncio.to_thread(
                transcription.transcribe_dialogue,
                str(output),
                settings.studio_asr_checkpoint,
                language,
            )
            heard = [
                float(word.get("probability", 0))
                for segment in transcript.get("segments") or []
                for word in segment.get("words") or []
            ]
            mean = sum(heard) / len(heard) if heard else None
            text = str(transcript.get("text") or "").strip()
            if transcript.get("status") != "ok":
                verdict = {
                    "status": "unknown",
                    "reason": transcript.get("reason") or "Parole non vérifiée.",
                    "mean_word_probability": mean,
                }
            elif text and (mean is None or mean >= 0.75):
                verdict = {
                    "status": "unknown",
                    "reason": f"Parole détectée dans un plan sans dialogue : {text[:160]}",
                    "transcript": text,
                    "mean_word_probability": mean,
                }
            else:
                verdict = {
                    "status": "ok",
                    "reason": "Aucune parole détectée.",
                    "mean_word_probability": mean,
                }
        if verdict:
            metadata["dialogue_qc"] = verdict
            service.render_event(take["id"], "DONE", verdict["reason"])
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
        service.render_event(take["id"], "DONE", verdict["reason"])
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
    if (cached.get("signature") == signature
            and (cached.get('transcript') or {}).get('status') == 'ok'):
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
    with service.database() as conn:
        request_row = conn.execute(
            "SELECT request_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()
    request_data = json.loads(request_row[0] or "{}") if request_row else {}
    timing_check = not (
        request_data.get("video_profile") == "ltx"
        and int(request_data.get("prompt_compiler_version") or 0) >= 3
    )
    turns = []
    contract_lines = (context.get("director_contract") or {}).get("dialogue") or []
    for index, turn in enumerate(context.get("shot", {}).get("dialogue_turns") or []):
        line = contract_lines[index] if index < len(contract_lines) else {}
        turns.append({
            **turn,
            "start_sec": line.get("start_sec"),
            "end_sec": line.get("end_sec"),
        })
    legacy = transcription.assess_transcript(dialogue, transcript, language=language)
    verdict = pacing.assess_dialogue(dialogue, shot["duration_sec"], probe, voice)
    if verdict["status"] not in ("missing", "inaudible"):
        if legacy.get("status") == "ok" and legacy.get("trim_end_limit_sec") is not None:
            verdict = legacy
        else:
            verdict = transcription.assess_transcript_v2(
                dialogue, transcript, language=language
            )
    has_word_timings = any(
        segment.get("words") for segment in transcript.get("segments") or []
    )
    if turns and has_word_timings and verdict["status"] not in (
        "missing",
        "extra",
        "inaudible",
        "unknown",
    ):
        turns_verdict = transcription.assess_turns_v2(
            turns, transcript, language=language, check_timing=timing_check
        )
        verdict["turns"] = turns_verdict.get("turns", [])
        verdict["speaker_identity_status"] = turns_verdict.get(
            "speaker_identity_status", "unverified"
        )
        verdict["speaker_identity_reason"] = turns_verdict.get("speaker_identity_reason", "")
        verdict["timing_drift_sec"] = turns_verdict.get("timing_drift_sec")
        verdict["mean_word_probability"] = turns_verdict.get("mean_word_probability")
        if turns_verdict["status"] != "ok":
            verdict.update(status=turns_verdict["status"], reason=turns_verdict["reason"])
    verdict.setdefault("voice", (
        transcript.get("speech_intervals") if transcript.get("status") == "ok" else None
    ))
    loudness = await asyncio.to_thread(pacing.measure_loudness, take["output_path"])
    apply_loudness(verdict, loudness)
    compiled = request_data.get("prompt") or take.get("prompt", "")
    diagnosis = transcription.assess_prompt_recitation(
        dialogue, transcript, compiled, language=language
    )
    if diagnosis:
        verdict.update(status="extra", reason=diagnosis["reason"])
        verdict["diagnostic"] = diagnosis
    apply_tts_status(verdict, metadata)
    metadata["dialogue_qc"] = verdict
    service.render_event(
        take["id"],
        "DONE",
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
    if shot.get("prompt_contract_version") == 2 and shot["dialogue"].strip() and voice:
        aside = shot.get("purpose") == "midroll"
        start, end = pacing.speech_window(
            probe, voice, pacing.ASIDE_POST_ROLL_SEC if aside else pacing.POST_ROLL_SEC,
            pre_roll_sec=pacing.ASIDE_PRE_ROLL_SEC if aside else pacing.PRE_ROLL_SEC,
        )
    else:
        start, end = pacing.useful_window(shot["duration_sec"], probe, voice)
    if end_limit is not None:
        end = min(end, pacing.floor_grid(end_limit))
    if start <= 1e-9 and end >= pacing.floor_grid(probe) - 1e-9:
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
        items = [entity for entity in items if entity["key"] in canon
                 and entity["key"] != "studio_cta_host"]
        context = frozen | {
            "entities": items,
            "prompt_contract_version": planned.get("prompt_contract_version", 1),
            "render_canon": planned.get("render_canon", {}),
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
        path = directory / f"entity-{entity['id']}-{uuid.uuid4().hex}.png"
        try:
            compiled = compile_image_request({}, context, canonical_entity=entity)
        except ValueError:
            if not strict:
                raise
            pending.append(entity["name"])
            continue
        request = compiled | {
            "aspect_ratio": frozen["aspect_ratio"],
            "image_provider": settings.studio_image_provider,
            "image_base_url": settings.studio_image_base_url,
            "image_model": "codex-imagegen" if settings.studio_image_provider == "codex"
            else settings.studio_image_model,
        }
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_entities SET reference_request_json=? WHERE id=?",
                (json.dumps(request), entity["id"]),
            )
            conn.commit()
        async def check_image_cancel():
            await checkpoint(
                job, "images", 0.12 + index / max(1, len(items)) * 0.08,
                f"Référence : {entity['name']}", should_stop,
            )

        content, _ = await service._generate_image(request, check_cancel=check_image_cancel)
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
    if should_stop():
        raise asyncio.CancelledError()
    if queue_manager.is_cancelled(job["id"]):
        raise ProductionPausedError()
    shot = load_shot(initial["id"])
    chosen = reusable_video(shot)
    from calliope.studio import director
    uses_director = False
    if job.get('project_id'):
        with service.database() as conn:
            uses_director = director.enabled(conn, job['project_id'])
    if uses_director and chosen and shot['approved']:
        return
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
    if job.get('video_ready') and not uses_director:
        job['video_ready']()
    if settings.studio_tts_enabled and shot.get("selected_video_id"):
        from calliope.studio import tts
        shot = await asyncio.to_thread(tts.apply_to_shot, shot)
    verdict = await assess_take(shot)
    if uses_director:
        retries = 0
        while (
            verdict
            and verdict.get("status") in ("extra", "missing", "inaudible")
            and retries < settings.studio_director_auto_retries
        ):
            retries += 1
            shot = await generate_take(
                job, load_shot(shot["id"]), "video", should_stop, attempt=retries
            )
            if settings.studio_tts_enabled and shot.get("selected_video_id"):
                from calliope.studio import tts
                shot = await asyncio.to_thread(tts.apply_to_shot, shot)
            verdict = await assess_take(shot)
        text_qc = ((selected_video(shot) or {}).get('metadata') or {}).get('text_qc') or {}
        if verdict and verdict['status'] == 'ok' and text_qc.get('status') != 'unexpected_text':
            trim_take(shot, verdict.get('voice'), verdict.get('trim_end_limit_sec'))
        message = ('Texte parasite détecté dans la prise native. Prise conservée, non approuvée.'
                   if text_qc.get('status') == 'unexpected_text' else
                   'Prise conservee. Verifiez les paroles, locuteurs, image et raccords avant de continuer.')
        with service.database() as conn:
            conn.execute("UPDATE studio_productions SET status='paused',stage='qc',message=? WHERE project_id=?",
                (message, job['project_id']))
            conn.commit()
        raise ProductionPausedError()
    clear = not verdict or verdict.get("status") == "ok"
    if clear:
        trim_take(shot, (verdict or {}).get("voice"), (verdict or {}).get("trim_end_limit_sec"))
    if verdict and verdict.get("replaced_turns"):
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_productions SET status='paused',stage='qc',message=? "
                "WHERE project_id=?",
                (
                    "Voix remplacées en post-doublage : écoutez la prise, vérifiez "
                    "locuteurs et synchronisation, puis validez ce plan avant le montage.",
                    job["project_id"],
                ),
            )
            conn.commit()
        raise ProductionPausedError()
    if should_stop():
        raise asyncio.CancelledError()
    if queue_manager.is_cancelled(job["id"]):
        raise ProductionPausedError()
    if not clear:
        # Le QC n'a pas établi la conformité : la prise reste non approuvée
        # et le montage s'arrêtera avant l'export. Aucun rerender automatique.
        return
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


async def prepare_local_audio():
    if settings.dry_run or not settings.studio_asr_checkpoint:
        return
    from calliope.studio.transcription import warm_transcriber
    try:
        await asyncio.to_thread(warm_transcriber, settings.studio_asr_checkpoint)
    except Exception:
        # ASR remains advisory when its local checkpoint cannot be loaded.
        pass


async def render_videos(job, shots, should_stop):
    from calliope.studio.video_preparation import prepare_video_batch

    if should_stop():
        raise asyncio.CancelledError()
    concurrency = settings.studio_video_concurrency
    from calliope.studio import director
    director_mode = False
    if job.get('project_id'):
        with service.database() as conn:
            if director.enabled(conn, job['project_id']):
                director_mode = True
                concurrency = 1
                shots = [s for s in shots if not s['approved']]
    if not shots:
        return
    semaphore = asyncio.Semaphore(concurrency)
    aborted = asyncio.Event()
    failure = None
    tasks = []
    ready = [asyncio.Event() for _ in shots]

    async def render(index, shot):
        nonlocal failure
        acquired = released = False

        def video_ready():
            nonlocal released
            if acquired and not released:
                released = True
                semaphore.release()
            ready[index].set()

        try:
            if shot["continue_from_previous"] and index:
                previous = next((i for i in range(index - 1, -1, -1)
                                 if shots[i].get("purpose") not in ("midroll", "outro")), None)
                if previous is not None:
                    await tasks[previous]
            await semaphore.acquire()
            acquired = True
            if aborted.is_set():
                return
            await checkpoint(
                job,
                "video",
                0.33 + index / len(shots) * 0.55,
                f"{settings.studio_video_profile} : plan {index + 1}/{len(shots)}",
                should_stop,
            )
            await render_shot(job | {
                'video_batch_last': director_mode or index == len(shots) - 1 and concurrency == 1,
                'video_preparation': preparation,
                'video_ready': video_ready,
            }, shot, should_stop)
        except BaseException as exc:
            if not aborted.is_set():
                failure = exc
            aborted.set()
            for signal in ready:
                signal.set()
            raise
        finally:
            video_ready()

    await checkpoint(job, 'images', 0.32, 'Préparation des rendus avant allocation GPU', should_stop)
    await prepare_local_audio()
    preparation = await prepare_video_batch(shots, should_stop)
    if preparation:
        preparation['concurrency'] = concurrency
    if should_stop():
        raise asyncio.CancelledError()
    control = {
        'engine':preparation['engine'], 'app_name':preparation['modal_app'],
        'function_name':preparation['modal_function'],
    } if preparation else {}
    if preparation:
        await service.video_idle_window(60, job_id=job['id'], **control)
    try:
        for index, shot in enumerate(shots):
            tasks.append(asyncio.create_task(render(index, shot)))
            if concurrency == 1:
                await ready[index].wait()
                if aborted.is_set():
                    raise failure
                if tasks[-1].done():
                    await tasks[-1]
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except Exception:
        aborted.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        if preparation and not preparation.get('gpu_released'):
            await service.video_idle_window(2, job_id=job['id'], **control)


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
                | {"prompt_contract_version": 2, "_planner_trace": {"project_id": project_id, "job_id": job["id"]}},
                episode["idea"],
                episode["objective"],
            )
            if plan.get("prompt_contract_version") == 2 and not plan.pop("_states_prepared", False):
                await checkpoint(job, "writing", 0.07, "Préparation des raccords entre les plans", should_stop)
                plan = normalize_plan(await prepare_native_states(plan, {"project_id": project_id, "job_id": job["id"]}),
                                      json.loads(episode["context_json"]))
            await checkpoint(job, "writing", 0.1, "Scénario préparé", should_stop)
            save_episode_plan(project_id, plan)
        ensure_midroll(project_id)
        from calliope.studio import director
        with service.database() as conn:
            wants_director = json.loads(episode['context_json']).get('studio_director')
            has_director = director.enabled(conn, project_id)
            contracts_ready = has_director and all(s['contract'] for s in director._state(conn, project_id)['shots'])
        if wants_director and not contracts_ready:
            await director.prepare(project_id, internal=True)
            has_director = True
        if payload.get("prepare_only"):
            with service.database() as conn:
                conn.execute(
                    "UPDATE studio_productions SET stage='review',message=? WHERE project_id=?",
                    ("Scénario préparé. Vérifiez l'entrée, la récompense et la suite "
                     "avant les rendus.", project_id),
                )
                conn.commit()
            raise ProductionPausedError()
        if has_director:
            check = director.preflight(project_id, payload.get('target_shot_ids'))
            if not check['ok']:
                with service.database() as conn:
                    conn.execute("UPDATE studio_productions SET status='paused',message=? WHERE project_id=?",
                                 (' '.join(check['errors']), project_id))
                    conn.commit()
                raise ProductionPausedError()
        review = editorial_review(project_id)
        if not has_director and not review["ok"]:
            with service.database() as conn:
                conn.execute(
                    "UPDATE studio_productions SET status='paused',stage='review',message=? "
                    "WHERE project_id=?",
                    ("Montage refusé : " + " ".join(review["errors"]), project_id),
                )
                conn.commit()
            raise ProductionPausedError()
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
        image_mode = json.loads(episode["context_json"]).get("image_mode", "generated")
        if image_mode == "generated":
            await ensure_entities(job, should_stop)
        else:
            await checkpoint(
                job, "images", 0.18,
                "Mode texte vers vidéo : aucune image n'est générée avec Codex",
                should_stop,
            )
        board = service.board(project_id)
        shots = [s for scene in board["scenes"] for s in scene["shots"]]
        if payload.get('target_shot_ids'):
            shots = [s for s in shots if s['id'] in payload['target_shot_ids']]
        for index, initial in enumerate(shots):
            await checkpoint(
                job,
                "images",
                0.2 + index / len(shots) * 0.12,
                (f"Image du plan {index + 1}/{len(shots)}" if image_mode == "generated"
                 else f"Préparation du plan {index + 1}/{len(shots)} sans image Codex"),
                should_stop,
            )
            with service.database() as conn:
                shot = service.public_shot(conn, initial["id"])
            if image_mode == "generated" and not shot["selected_image_id"] and not shot["continue_from_previous"]:
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
                if image_mode == "text_to_video" and source is None:
                    context = service.shot_context(conn, shot["id"])
                    if context.get("directing_profile", {}).get("require_reference_approval") or any(
                        label.get("anchor") == "character" for label in shot.get("screen_text", [])
                    ):
                        conn.execute(
                            "UPDATE studio_productions SET status='paused',message=? WHERE project_id=?",
                            ("Ce plan exige une image de départ validée : utilisez le mode avec images "
                             "ou retirez l'ancrage de texte sur les personnages.", project_id),
                        )
                        conn.commit()
                        raise ProductionPausedError()
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
        board = service.board(project_id)
        waiting = [
            shot for scene in board["scenes"] for shot in scene["shots"]
            if not shot.get("approved")
        ]
        if waiting:
            details = []
            for item in waiting:
                take = selected_video(item)
                qc = ((take or {}).get("metadata") or {}).get("dialogue_qc") or {}
                details.append(
                    f"plan {item.get('order_index')}: {qc.get('status') or 'sans QC'}"
                )
            with service.database() as conn:
                conn.execute(
                    "UPDATE studio_productions SET status='paused',stage='qc',message=? "
                    "WHERE project_id=?",
                    (
                        f"{len(waiting)} plan(s) sans validation humaine "
                        f"({'; '.join(details[:6])}). Écoutez et validez les prises "
                        "avant l'assemblage : un rendu terminé ne prouve ni la voix, "
                        "ni le lip-sync, ni les raccords.",
                        project_id,
                    ),
                )
                conn.commit()
            raise ProductionPausedError()
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
                    "ject_id=? AND COALESCE((SELECT json_extract(context_json,'$.purpose') "
                    "FROM studio_shot_context WHERE clip_id=c.id),'') NOT IN ('midroll','outro') "
                    "ORDER BY s.order_index,s.id,c.order_index,c.id"
                ),
                (project_id,),
            )
        ]
    original = [
        {k: str(shot.get(k) or "") for k in ("description", "dialogue", "prompt")}
        for shot in plan.get("editorial_script", [
            shot for scene in plan.get("scenes", []) for shot in scene["shots"]
        ])
        if shot.get("purpose") not in ("midroll", "outro")
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
