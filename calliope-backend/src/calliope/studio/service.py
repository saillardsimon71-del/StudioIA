import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import secrets
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps

from calliope.config import settings
from calliope.db import ensure_default_clip, get_db
from calliope.events.bus import event_bus
from calliope.queue.manager import modal_call_unfinished, queue_manager

logger = logging.getLogger(__name__)


class StudioCancelledError(RuntimeError):
    pass


@contextmanager
def database():
    conn = get_db(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def media_url(path):
    return "/api/file?path=" + quote(str(path), safe="") if path else None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def shot_context(conn, shot_id):
    row = conn.execute(
        "SELECT e.context_json,e.plan_json,e.series_id,sc.context_json AS shot_json FROM clips c "
        "LEFT JOIN studio_episodes e ON e.project_id=c.project_id "
        "LEFT JOIN studio_shot_context sc ON sc.clip_id=c.id WHERE c.id=?",
        (shot_id,),
    ).fetchone()
    context = json.loads(row["context_json"] or "{}") if row else {}
    plan = json.loads(row["plan_json"] or "{}") if row else {}
    entities = {e["key"]: e for e in plan.get("entities", [])}
    entities.update({e["key"]: e for e in context.get("entities", [])})
    if row and row["series_id"]:
        from calliope.studio.series import entities as series_entities

        for entity in series_entities(conn, row["series_id"]):
            if entity["key"] in entities:
                entities[entity["key"]].update(
                    identity=entity["identity"],
                    reference_path=entity.get("reference_path"),
                )
    context["entities"] = list(entities.values())
    context["bible"] = {
        **plan.get("bible", {}),
        **{k: v for k, v in context.get("bible", {}).items() if v},
    }
    context["shot"] = json.loads(row["shot_json"] or "{}") if row else {}
    contract = conn.execute('SELECT contract_json FROM studio_director_shots WHERE clip_id=?',
                            (shot_id,)).fetchone()
    if contract:
        context['director_contract'] = json.loads(contract[0])
    context["prompt_contract_version"] = plan.get("prompt_contract_version", 1)
    context["render_canon"] = plan.get("render_canon", {})
    if context["prompt_contract_version"] == 2:
        for entity in context["entities"]:
            identity = entity.get("identity") or {}
            description = str(identity.get("visual_description") or "").strip()
            if not identity.get("reference_approved") or not entity.get("reference_path") or not description:
                continue
            canon = context["render_canon"].get(
                "characters" if entity["kind"] == "character" else "locations", {}
            )
            if entity["key"] in canon:
                if entity["kind"] == "character":
                    canon[entity["key"]]["appearance"] = description
                else:
                    canon[entity["key"]] = description
    context["speaker_ids"] = (plan.get("speaker_ids") or {}) if context["prompt_contract_version"] == 2 else (
        (plan.get("speaker_ids") or {}) | (context.get("speaker_ids") or {})
    )
    return context


def preview_prompts(project_id, shot_id):
    from calliope.studio.h3_prompt import compile_video_prompt
    from calliope.studio.visual_identity import compile_image_request

    with database() as conn:
        require_shot(conn, shot_id, project_id)
        shot = public_shot(conn, shot_id)
        context = shot_context(conn, shot_id)
        source_status = "pending_source"
        if shot["continue_from_previous"]:
            try:
                _previous_video(conn, shot_id)
                source_status = "selected_previous_video"
            except HTTPException:
                pass
        elif shot["selected_image_id"]:
            source_status = "selected_image"
        try:
            return {
                "image_prompt": compile_image_request(shot, context)["prompt"],
                "video_prompt": compile_video_prompt(shot | context["shot"], context,
                    first_frame=source_status != "pending_source", video_profile=settings.studio_video_profile),
                "prompt_contract_version": context["prompt_contract_version"],
                "source_status": source_status,
            }
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, f"Demande incomplète : {exc}") from None


def render_event(take_id, phase=None, detail="", snapshot=None, **updates):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT render_state_json FROM studio_takes WHERE id=?", (take_id,)
        ).fetchone()
        state = json.loads(row[0] or "{}")
        events = state.setdefault("events", [])
        if snapshot:
            for event in snapshot.get("events", []):
                observed = {k: event[k] for k in ("phase", "at", "detail") if k in event}
                observed['source'] = 'remote'
                if observed not in events:
                    events.append(observed)
            for key in ("container_id", "comfy_prompt_id", "sampling"):
                if key in snapshot:
                    state[key] = snapshot[key]
            phase = snapshot.get("phase") or phase
        elif phase and state.get("phase") != phase:
            events.append({"phase": phase, "at": utc_now(), "detail": detail, 'source': 'local'})
        if phase:
            state["phase"] = phase
        state.update(updates)
        conn.execute(
            "UPDATE studio_takes SET render_state_json=? WHERE id=?", (json.dumps(state), take_id)
        )
        conn.commit()
        return state


def safe_error(error):
    text = str(error) or type(error).__name__
    for key in (settings.llm_api_key, settings.studio_image_api_key):
        if key:
            text = text.replace(key, "[masqué]")
    return text[:2000]


def require_project(conn, project_id):
    row = conn.execute(
        "SELECT * FROM projects WHERE id=? AND status!=?", (project_id, "system")
    ).fetchone()
    if not row:
        raise HTTPException(404, "Projet introuvable.")
    conn.execute("INSERT OR IGNORE INTO studio_projects(project_id) VALUES(?)", (project_id,))
    return dict(row)


def require_shot(conn, shot_id, project_id=None):
    row = conn.execute("SELECT * FROM clips WHERE id=?", (shot_id,)).fetchone()
    if not row or (project_id is not None and row["project_id"] != project_id):
        raise HTTPException(404, "Plan introuvable dans ce projet.")
    conn.execute(
        "INSERT OR IGNORE INTO studio_shots(clip_id,prompt,image_prompt) VALUES(?,?,?)",
        (shot_id, row["description"] or "", row["description"] or ""),
    )
    return dict(row)


def touch(conn, project_id):
    conn.execute("UPDATE studio_projects SET revision=revision+1 WHERE project_id=?", (project_id,))
    conn.execute(
        "UPDATE projects SET updated_at=CURRENT_TIMESTAMP,status='draft' WHERE id=?", (project_id,)
    )


def public_job(row, conn=None):
    if conn is None:
        with database() as reader:
            return public_job(row, reader)
    job = dict(row)
    payload = json.loads(job.pop("payload_json", None) or "{}")
    job.pop("output_paths_json", None)
    job["take_id"] = payload.get("studio_take_id")
    job['parent_job_id'] = payload.get('parent_job_id')
    job["can_resume"] = (
        job["status"] == "failed" and job.get("error") != "cancelled"
        and not job['parent_job_id']
    )
    job['can_cancel'] = job['status'] in ('pending', 'running')
    if job["take_id"]:
        take = conn.execute(
            "SELECT * FROM studio_takes WHERE id=?", (job["take_id"],)
        ).fetchone()
        if take:
            job["render_state"] = json.loads(take["render_state_json"] or "{}")
            job["modal_call_id"] = take["modal_call_id"]
            job['can_cancel'] = job['can_cancel'] or modal_call_unfinished(take)
            if take["submission_state"] == "submitting" and not take["modal_call_id"]:
                job["can_resume"] = False
    if payload.get('studio_production_id'):
        children = conn.execute(
            "SELECT t.* FROM studio_takes t JOIN jobs j ON j.id=t.job_id "
            "WHERE json_extract(j.payload_json,'$.parent_job_id')=?", (job['id'],),
        ).fetchall()
        job['can_cancel'] = job['can_cancel'] or any(modal_call_unfinished(t) for t in children)
    if job["status"] == "failed" and job.get("error") == "cancelled":
        job["status"] = "cancelled"
    return job


async def cancel_modal_take(take_id):
    with database() as conn:
        take = conn.execute('SELECT * FROM studio_takes WHERE id=?', (take_id,)).fetchone()
    if not take or not modal_call_unfinished(take):
        return
    from calliope.engines.modal_h3 import ModalH3Engine

    request = json.loads(take['request_json'])
    engine = ModalH3Engine(request['modal_app'], request['modal_function'])
    try:
        await asyncio.to_thread(engine.cancel, take['modal_call_id'])
    except Exception as exc:
        render_event(take_id, cancel_error=safe_error(exc))
        raise
    render_event(
        take_id, remote_terminal=True, remote_terminal_reason='cancel_confirmed', cancel_error=None,
    )


async def cancel_job(job_id):
    job = queue_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, 'Travail introuvable.')
    payload = json.loads(job['payload_json'] or '{}')
    if not any(payload.get(key) for key in (
        'studio_take_id', 'studio_export_id', 'studio_production_id',
    )):
        raise HTTPException(404, 'Travail Studio introuvable.')
    if not public_job(job)['can_cancel']:
        raise HTTPException(409, 'Ce travail est déjà terminé; aucun appel Modal à arrêter.')
    queue_manager.cancel(job_id)
    with database() as conn:
        takes = conn.execute(
            "SELECT t.id FROM studio_takes t JOIN jobs j ON j.id=t.job_id "
            "WHERE j.id=? OR json_extract(j.payload_json,'$.parent_job_id')=?",
            (job_id, job_id),
        ).fetchall()
    errors = []
    for take in takes:
        try:
            await cancel_modal_take(take['id'])
        except Exception as exc:
            errors.append(safe_error(exc))
    if errors:
        raise HTTPException(
            503, 'Annulé localement; arrêt Modal non confirmé: ' + '; '.join(errors),
        )
    return public_job(queue_manager.get_job(job_id))


def public_take(conn, row):
    take = dict(row)
    metadata = json.loads(take.pop("metadata_json") or "{}")
    take["render_state"] = json.loads(take.pop("render_state_json") or "{}")
    take.pop("request_json", None)
    take.pop("request_hash", None)
    take.pop("submission_state", None)
    take["url"] = media_url(take.get("output_path"))
    take["poster_url"] = media_url(metadata.pop("poster_path", None))
    take["metadata"] = metadata
    if take.get("job_id"):
        job = conn.execute("SELECT status,error FROM jobs WHERE id=?", (take["job_id"],)).fetchone()
        if job and take["status"] != "done":
            take["status"] = "cancelled" if job["error"] == "cancelled" else job["status"]
            take["error"] = job["error"] or take["error"]
    return take


def public_shot(conn, shot_id):
    clip = require_shot(conn, shot_id)
    extra = dict(conn.execute("SELECT * FROM studio_shots WHERE clip_id=?", (shot_id,)).fetchone())
    extra.pop("clip_id")
    clip.update(extra)
    clip["approved"] = bool(clip["approved"])
    clip["continue_from_previous"] = bool(clip["continue_from_previous"])
    clip["duration_sec"] = clip["duration_sec"] or 5
    context = shot_context(conn, shot_id)["shot"]
    clip["dialogue_turns"] = context.get("dialogue_turns", [])
    clip["soundscape"] = context.get("soundscape", "")
    clip["screen_text"] = context.get("screen_text", [])
    clip["character_keys"] = context.get("character_keys", [])
    clip["purpose"] = context.get("purpose", "")
    clip.update({key: context[key] for key in (
        "prop_keys", "start_state", "end_state", "cut_reason", "action", "reaction",
        "framing", "camera", "blocking"
    ) if key in context})
    clip["prompt_contract_version"] = shot_context(conn, shot_id)["prompt_contract_version"]
    if clip["continue_from_previous"]:
        try:
            previous = _previous_video(conn, shot_id)
            clip["continuation_source"] = public_take(conn, previous)
            end = conn.execute(
                "SELECT trim_end_sec FROM studio_shots WHERE clip_id=?", (previous["clip_id"],)
            ).fetchone()[0]
            meta = json.loads(previous["metadata_json"] or "{}")
            from calliope.engines.h3_graph import H3_FPS

            fps = float(meta.get("fps") or H3_FPS)
            clip["continuation_frame_index"] = (
                max(0, math.ceil(max(0, end - 1 / H3_FPS) * fps - 1e-6))
                if end is not None
                else max(0, round(float(meta.get("duration_sec") or 0) * fps) - 1)
            )
        except HTTPException:
            clip["continuation_source"] = None
    clip["takes"] = [
        public_take(conn, r)
        for r in conn.execute(
            "SELECT * FROM studio_takes WHERE clip_id=? ORDER BY id DESC", (shot_id,)
        )
    ]
    return clip


def archive_shot(conn, shot_id):
    shot = public_shot(conn, shot_id)
    data = {
        "archived_at": utc_now(), "shot": shot,
        "context": shot_context(conn, shot_id),
        "takes": [dict(row) for row in conn.execute(
            "SELECT * FROM studio_takes WHERE clip_id=? ORDER BY id", (shot_id,)
        )],
        "jobs": [dict(row) for row in conn.execute(
            "SELECT * FROM jobs WHERE clip_id=? ORDER BY id", (shot_id,)
        )],
    }
    directory = settings.assets_dir / str(shot["project_id"]) / "studio/diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"deleted-shot-{shot_id}-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def board(project_id):
    with database() as conn:
        project = require_project(conn, project_id)
        meta = dict(
            conn.execute(
                "SELECT * FROM studio_projects WHERE project_id=?", (project_id,)
            ).fetchone()
        )
        project.update({k: v for k, v in meta.items() if k != "project_id"})
        scenes = []
        for row in conn.execute(
            "SELECT * FROM scenes WHERE project_id=? ORDER BY order_index,id", (project_id,)
        ):
            scene = dict(row)
            ensure_default_clip(conn, scene["id"], project_id)
            scene["shots"] = [
                public_shot(conn, r["id"])
                for r in conn.execute(
                    "SELECT id FROM clips WHERE scene_id=? ORDER BY order_index,id", (scene["id"],)
                )
            ]
            scenes.append(scene)
        jobs = []
        for row in conn.execute(
            "SELECT * FROM jobs WHERE project_id=? ORDER BY id DESC LIMIT 250", (project_id,)
        ):
            payload = json.loads(row["payload_json"] or "{}")
            if payload.get("studio_take_id") or payload.get("studio_export_id"):
                jobs.append(public_job(row, conn))
        exports = []
        for row in conn.execute(
            "SELECT * FROM studio_exports WHERE project_id=? ORDER BY id DESC", (project_id,)
        ):
            export = dict(row)
            job = conn.execute(
                "SELECT status,error FROM jobs WHERE id=?", (row["job_id"],)
            ).fetchone()
            export.update(
                status=(
                    ("cancelled" if job["error"] == "cancelled" else job["status"])
                    if job else "done"
                ),
                error=job["error"] if job else None,
                url=media_url(export["output_path"]),
                stale=meta["revision"] != export["revision"],
                metadata=json.loads(export.pop("metadata_json") or "{}"),
            )
            export.pop("snapshot_json", None)
            exports.append(export)
        conn.commit()
        from calliope.studio.audio import get_audio
        from calliope.studio.series import episode_meta
        production = conn.execute(
            "SELECT * FROM studio_productions WHERE project_id=?", (project_id,)
        ).fetchone()
        return {
            "project": project, "scenes": scenes, "jobs": jobs, "exports": exports,
            "episode": episode_meta(conn, project_id),
            "production": dict(production) if production else None,
            "audio": get_audio(conn, project_id),
        }


def list_projects():
    with database() as conn:
        result = []
        for row in conn.execute(
            "SELECT p.* FROM projects p LEFT JOIN studio_episodes e ON e.project_id=p.id "
            "LEFT JOIN studio_series s ON s.id=e.series_id WHERE p.status!='system' "
            "AND COALESCE(s.archived,0)=0 ORDER BY p.updated_at DESC,p.id DESC"
        ):
            project = require_project(conn, row["id"])
            project["aspect_ratio"] = conn.execute(
                "SELECT aspect_ratio FROM studio_projects WHERE project_id=?", (row["id"],)
            ).fetchone()[0]
            project["scene_count"] = conn.execute(
                "SELECT count(*) FROM scenes WHERE project_id=?", (row["id"],)
            ).fetchone()[0]
            project["shot_count"] = conn.execute(
                "SELECT count(*) FROM clips WHERE project_id=?", (row["id"],)
            ).fetchone()[0]
            project["approved_count"] = conn.execute(
                "SELECT count(*) FROM studio_shots ss JOIN clips c ON c.id=ss.clip_id "
                "WHERE c.project_id=? AND ss.approved=1",
                (row["id"],),
            ).fetchone()[0]
            cover = conn.execute(
                "SELECT t.output_path FROM studio_takes t WHERE t.project_id=? AND kind=? "
                "AND output_path IS NOT NULL ORDER BY id DESC LIMIT 1",
                (row["id"], "image"),
            ).fetchone()
            project["cover_url"] = media_url(cover[0]) if cover else None
            result.append(project)
        conn.commit()
        return result


def create_project(data):
    with database() as conn:
        cur = conn.execute(
            "INSERT INTO projects(title,idea) VALUES(?,?)", (data["title"], data.get("idea", ""))
        )
        project_id = cur.lastrowid
        conn.execute(
            "INSERT INTO studio_projects(project_id,aspect_ratio) VALUES(?,?)",
            (project_id, data.get("aspect_ratio", "16:9")),
        )
        conn.commit()
    return board(project_id)["project"]


def add_shot(conn, project_id, scene_id, data):
    index = conn.execute(
        "SELECT COALESCE(MAX(order_index),0)+1 FROM clips WHERE scene_id=?", (scene_id,)
    ).fetchone()[0]
    description = str(data.get("description") or data.get("prompt") or "")
    cur = conn.execute(
        "INSERT INTO clips(scene_id,project_id,order_index,description,duration_sec) "
        "VALUES(?,?,?,?,?)",
        (scene_id, project_id, index, description, data.get("duration_sec") or 5),
    )
    conn.execute(
        "INSERT INTO studio_shots(clip_id,prompt,image_prompt,dialogue,audio_mode,"
        "trim_start_sec,trim_end_sec,continue_from_previous) VALUES(?,?,?,?,?,?,?,?)",
        (
            cur.lastrowid,
            str(data.get("prompt") or description),
            str(data.get("image_prompt") or description),
            str(data.get("dialogue") or ""),
            data.get("audio_mode") or "native",
            data.get("trim_start_sec"),
            data.get("trim_end_sec"),
            int(data.get("continue_from_previous", False)),
        ),
    )
    context_fields = ("dialogue_turns", "prop_keys", "start_state", "end_state", "cut_reason",
                      "action", "reaction", "framing", "camera", "blocking")
    context = {key: data[key] for key in context_fields if key in data}
    if context:
        conn.execute("INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?)",
                     (cur.lastrowid, json.dumps(context)))
    return cur.lastrowid


def add_scene(conn, project_id, data):
    require_project(conn, project_id)
    index = conn.execute(
        "SELECT COALESCE(MAX(order_index),0)+1 FROM scenes WHERE project_id=?", (project_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO scenes(project_id,order_index,heading,action,dialog) VALUES(?,?,?,?,?)",
        (
            project_id,
            index,
            data.get("heading") or f"Scène {index}",
            data.get("action") or "",
            data.get("dialog") or "",
        ),
    )
    scene_id = cur.lastrowid
    for shot in data.get("shots") or [
        {
            "description": data.get("action") or data.get("heading") or "",
            "dialogue": data.get("dialog") or "",
        }
    ]:
        add_shot(conn, project_id, scene_id, shot)
    touch(conn, project_id)
    return scene_id


def save_plan(project_id, text, scenes):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_project(conn, project_id)
        if conn.execute(
            "SELECT 1 FROM scenes WHERE project_id=? LIMIT 1", (project_id,)
        ).fetchone():
            raise HTTPException(
                409, "Ce projet contient déjà des plans. Créez un projet pour un nouveau découpage."
            )
        conn.execute(
            "UPDATE studio_projects SET script_source=? WHERE project_id=?", (text, project_id)
        )
        for scene in scenes:
            add_scene(conn, project_id, scene)
        conn.commit()
    return board(project_id)


def update_shot(project_id, shot_id, data):
    with database() as conn:
        require_shot(conn, shot_id, project_id)
        current = public_shot(conn, shot_id)
        if "dialogue_turns" in data:
            from calliope.studio.series_planner import canonical_key, normalize_dialogue

            context = shot_context(conn, shot_id)
            for turn in data["dialogue_turns"]:
                speaker = canonical_key(turn["speaker"], context["entities"], "character")
                if not any(
                    e["key"] == speaker and e["kind"] == "character" for e in context["entities"]
                ):
                    raise HTTPException(422, "Le locuteur de cette réplique est inconnu.")
                try:
                    text, speaker = normalize_dialogue(turn["text"], speaker, context["entities"])
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                if not text:
                    raise HTTPException(422, "Cette réplique est vide.")
                turn.update(speaker=speaker, text=text)
            if data["dialogue_turns"]:
                data["dialogue"] = " ".join(t["text"] for t in data["dialogue_turns"])
        elif "dialogue" in data and current.get("dialogue_turns"):
            data["dialogue_turns"] = []
        if "screen_text" in data:
            from calliope.studio.h3_prompt import directing_profile
            from calliope.studio.screen_text import ScreenText

            profile = directing_profile(shot_context(conn, shot_id).get("directing_profile"))
            data["screen_text"] = [
                ScreenText.model_validate(label | {"number_format": profile["number_format"]})
                .model_dump() for label in data["screen_text"]
            ]
        changed = any(current.get(k) != v for k, v in data.items())
        trim = {
            key: data.get(key, current.get(key))
            for key in ("trim_start_sec", "trim_end_sec")
        }
        if trim != {key: current.get(key) for key in trim}:
            start, end = trim["trim_start_sec"], trim["trim_end_sec"]
            if start is not None and not 0 <= float(start) < 900:
                raise HTTPException(422, "Le point d'entrée utile doit être positif.")
            if end is not None and float(end) <= float(start or 0):
                raise HTTPException(422, "Le point de sortie utile doit suivre l'entrée.")
        for key in ("description", "duration_sec", "order_index"):
            if key in data:
                conn.execute(f"UPDATE clips SET {key}=? WHERE id=?", (data[key], shot_id))
        for key in (
            "prompt",
            "image_prompt",
            "dialogue",
            "audio_mode",
            "continue_from_previous",
            "trim_start_sec",
            "trim_end_sec",
        ):
            if key in data:
                conn.execute(
                    f"UPDATE studio_shots SET {key}=? WHERE clip_id=?", (data[key], shot_id)
                )
        if changed:
            conn.execute("UPDATE studio_shots SET approved=0 WHERE clip_id=?", (shot_id,))
            touch(conn, project_id)
        context_data = shot_context(conn, shot_id)["shot"]
        context_fields = ("dialogue_turns", "soundscape", "screen_text", "prop_keys",
                          "start_state", "end_state", "cut_reason", "action", "reaction",
                          "framing", "camera", "blocking")
        for key in context_fields:
            if key in data:
                context_data[key] = data[key]
        if data.get("dialogue_turns"):
            speakers = {t["speaker"] for t in data["dialogue_turns"]}
            context_data["speaker"] = next(iter(speakers)) if len(speakers) == 1 else ""
            context_data["character_keys"] = list(dict.fromkeys(
                context_data.get("character_keys", [])
                + [t["speaker"] for t in data["dialogue_turns"]]
            ))
        if any(key in data for key in context_fields):
            conn.execute(
                "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?) "
                "ON CONFLICT(clip_id) DO UPDATE SET context_json=excluded.context_json",
                (shot_id, json.dumps(context_data)),
            )
        if changed and shot_context(conn, shot_id)["prompt_contract_version"] == 2:
            from calliope.studio.series_planner import _speaker_ids_for_plan, _validate_native_contract

            row = conn.execute("SELECT plan_json FROM studio_episodes WHERE project_id=?", (project_id,)).fetchone()
            plan = json.loads(row[0])
            live = [public_shot(conn, item[0]) | shot_context(conn, item[0])["shot"] for item in conn.execute(
                "SELECT c.id FROM clips c JOIN scenes s ON s.id=c.scene_id WHERE c.project_id=? "
                "ORDER BY s.order_index,s.id,c.order_index,c.id", (project_id,)
            ).fetchall()]
            live_plan = plan | {"scenes": [{"shots": live}]}
            try:
                _validate_native_contract(live_plan, shot_context(conn, shot_id)["entities"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            plan["speaker_ids"] = _speaker_ids_for_plan(live_plan)
            conn.execute("UPDATE studio_episodes SET plan_json=? WHERE project_id=?", (json.dumps(plan), project_id))
        result = public_shot(conn, shot_id)
        conn.commit()
        return result


def save_trim(conn, shot_id, start, end):
    """Enregistre les points utiles calculés pour une prise validée.

    N'écrase jamais une coupe choisie à la main et ne touche pas à
    l'approbation : la validation de la prise reste acquise.
    """
    row = conn.execute(
        "SELECT trim_start_sec,trim_end_sec FROM studio_shots WHERE clip_id=?", (shot_id,)
    ).fetchone()
    if not row or row["trim_start_sec"] is not None or row["trim_end_sec"] is not None:
        return False
    conn.execute(
        "UPDATE studio_shots SET trim_start_sec=?,trim_end_sec=? WHERE clip_id=?",
        (round(float(start), 3), round(float(end), 3), shot_id),
    )
    project = conn.execute("SELECT project_id FROM clips WHERE id=?", (shot_id,)).fetchone()
    if project:
        touch(conn, project["project_id"])
    return True


def normalized_image(content):
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 32_000_000:
                raise ValueError("Image supérieure à 32 mégapixels.")
            image = ImageOps.exif_transpose(image).convert("RGB")
            output = io.BytesIO()
            image.save(output, "PNG")
            return output.getvalue(), {"width": image.width, "height": image.height}
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Image illisible. Importez un PNG, JPEG ou WebP.") from exc


def store_upload(shot_id, content, kind):
    from calliope.studio.export import inspect_media

    with database() as conn:
        shot = public_shot(conn, shot_id)
        token = uuid.uuid4().hex
        directory = settings.assets_dir / str(shot["project_id"]) / "studio" / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (token + (".png" if kind == "image" else ".mp4"))
        if kind == "image":
            content, metadata = normalized_image(content)
            path.write_bytes(content)
        else:
            path.write_bytes(content)
            try:
                metadata = inspect_media(path)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        cur = conn.execute(
            "INSERT INTO studio_takes(project_id,clip_id,kind,status,prompt,engine,source_take_id,"
            "request_id,request_hash,output_path,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                shot["project_id"],
                shot_id,
                kind,
                "done",
                "Import manuel",
                "import",
                shot["selected_image_id"] if kind == "video" else None,
                token,
                token,
                str(path),
                json.dumps(metadata),
            ),
        )
        result = public_take(
            conn, conn.execute("SELECT * FROM studio_takes WHERE id=?", (cur.lastrowid,)).fetchone()
        )
        conn.commit()
        return result


def select_take(take_id):
    with database() as conn:
        row = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Prise introuvable.")
        take = public_take(conn, row)
        if (
            take["status"] != "done"
            or not take["output_path"]
            or not Path(take["output_path"]).is_file()
        ):
            raise HTTPException(409, "Cette prise ne possède pas encore de média lisible.")
        shot = public_shot(conn, row["clip_id"])
        key = "selected_image_id" if row["kind"] == "image" else "selected_video_id"
        if shot[key] != take_id:
            if row["kind"] == "image":
                conn.execute(
                    "UPDATE studio_shots SET selected_image_id=?,selected_video_id=NULL,"
                    "approved=0 WHERE clip_id=?",
                    (take_id, row["clip_id"]),
                )
                conn.execute("UPDATE clips SET clip_path=NULL WHERE id=?", (row["clip_id"],))
            else:
                source = conn.execute(
                    "SELECT id,kind FROM studio_takes WHERE id=?", (row["source_take_id"],)
                ).fetchone()
                conn.execute(
                    "UPDATE studio_shots SET selected_video_id=?,selected_image_id=?,"
                    "approved=0 WHERE clip_id=?",
                    (
                        take_id,
                        source["id"] if source and source["kind"] == "image" else None,
                        row["clip_id"],
                    ),
                )
                conn.execute(
                    "UPDATE clips SET clip_path=? WHERE id=?", (row["output_path"], row["clip_id"])
                )
            touch(conn, row["project_id"])
            from calliope.studio import director

            if director.enabled(conn, row['project_id']):
                conn.execute('UPDATE studio_shots SET trim_start_sec=NULL,trim_end_sec=NULL WHERE clip_id=?', (row['clip_id'],))
            director.invalidate_dependents(conn, row['project_id'], {row['clip_id']})
        result = public_shot(conn, row["clip_id"])
        conn.commit()
        return result


def visual_review_contract(context, shot):
    from calliope.studio.visual_identity import contract_hash, scene_direction

    info = context.get("shot") or {}
    keys = list(info.get("character_keys") or [])
    if info.get("location_key"):
        keys.append(info["location_key"])
    identities = [
        {"key": entity["key"], "identity": entity.get("identity") or {}}
        for entity in context["entities"]
        if entity["key"] in keys
    ]
    labels = [
        {k: v for k, v in label.items() if k != "anchor_binding"}
        for label in shot.get("screen_text", [])
    ]
    return contract_hash(
        {
            "identities": identities,
            "labels": labels,
            "visual_style": context.get("bible", {}).get("visual_style", ""),
            "scene_direction": scene_direction(shot, context),
        }
    )


def review_take_visuals(take_id, approved, frame_index=0, target_shot_id=None):
    from calliope.studio.screen_text import bind_anchor
    from calliope.studio.visual_identity import file_hash

    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        take = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone()
        if not take:
            raise HTTPException(404, "Prise introuvable.")
        shot = public_shot(conn, target_shot_id or take["clip_id"])
        selected = shot["selected_image_id" if take["kind"] == "image" else "selected_video_id"]
        continuation = bool(target_shot_id and target_shot_id != take["clip_id"])
        if continuation:
            previous = _previous_video(conn, shot["id"]) if shot["continue_from_previous"] else None
            selected = previous["id"] if previous else None
        if (
            selected != take_id
            or take["status"] != "done"
            or not Path(take["output_path"] or "").is_file()
        ):
            raise HTTPException(
                409, "Choisissez cette prise terminée avant de vérifier son identité."
            )
        context = shot_context(conn, shot["id"])
        metadata = json.loads(take["metadata_json"] or "{}")
        if approved:
            try:
                bindings = [
                    bind_anchor(take["output_path"], label, take_id, frame_index)
                    for label in shot.get("screen_text", [])
                    if label.get("anchor") == "character"
                ]
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            review = {
                "approved": True,
                "source_sha256": file_hash(take["output_path"]),
                "contract_hash": visual_review_contract(context, shot),
                "anchor_bindings": bindings,
                "frame_index": frame_index,
            }
            if take["kind"] == "video":
                from calliope.studio.screen_text import _anchor_frame

                frame = _anchor_frame(take["output_path"], frame_index)
                review["frame_pixel_sha256"] = hashlib.sha256(
                    frame[:, :, ::-1].tobytes()
                ).hexdigest()
            if continuation:
                metadata.setdefault("continuation_reviews", {})[str(shot["id"])] = review
            else:
                metadata["visual_review"] = review
        else:
            if continuation:
                metadata.setdefault("continuation_reviews", {}).pop(str(shot["id"]), None)
            else:
                metadata.pop("visual_review", None)
        if take["kind"] == "video" and not continuation:
            info = context["shot"]
            binding_iter = iter((metadata.get("visual_review") or {}).get("anchor_bindings", []))
            for label in info.get("screen_text", []):
                if label.get("anchor") == "character":
                    label["anchor_binding"] = next(binding_iter) if approved else None
            conn.execute(
                "UPDATE studio_shot_context SET context_json=? WHERE clip_id=?",
                (json.dumps(info), shot["id"]),
            )
        conn.execute(
            "UPDATE studio_takes SET metadata_json=? WHERE id=?", (json.dumps(metadata), take_id)
        )
        conn.execute("UPDATE studio_shots SET approved=0 WHERE clip_id=?", (shot["id"],))
        touch(conn, shot["project_id"])
        conn.commit()
        return public_take(
            conn, conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone()
        )


def require_source_visual_review(source, shot, context, previous_video_time=None):
    from calliope.studio.visual_identity import file_hash, validate_value_manifest

    try:
        validate_value_manifest(shot, context)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    labels = [label for label in shot.get("screen_text", []) if label.get("anchor") == "character"]
    strict = context.get("directing_profile", {}).get("require_reference_approval") or context.get('studio_director')
    if not strict and not labels:
        return None
    if not source:
        raise HTTPException(
            422, "Choisissez une image de départ et approuvez son identité avant la vidéo."
        )
    metadata = json.loads(source["metadata_json"] or "{}")
    review = (
        (
            (metadata.get("continuation_reviews") or {}).get(str(shot["id"]))
            if source["kind"] == "video"
            else None
        )
        or metadata.get("visual_review")
        or {}
    )
    if (
        not review.get("approved")
        or review.get("source_sha256") != file_hash(source["output_path"])
        or review.get("contract_hash") != visual_review_contract(context, shot)
    ):
        raise HTTPException(
            422,
            "Vérifiez et approuvez l'identité et les zones sur l'image de départ avant la vidéo.",
        )
    if len(review.get("anchor_bindings", [])) != len(labels):
        raise HTTPException(422, "Les zones de texte de l'image de départ doivent être approuvées.")
    if source["kind"] == "video":
        from calliope.engines.modal_h3 import _last_frame_png

        content = _last_frame_png(Path(source["output_path"]), previous_video_time)
        with Image.open(io.BytesIO(content)) as image:
            digest = hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
        if review.get("frame_pixel_sha256") != digest:
            raise HTTPException(
                422, "Approuvez la dernière frame conservée du plan précédent avant de poursuivre."
            )
        review = review | {
            "source_frame_sha256": hashlib.sha256(content).hexdigest(),
            "previous_video_time": previous_video_time,
        }
    return review


def approve_shot(shot_id, approved):
    with database() as conn:
        shot = public_shot(conn, shot_id)
        if approved:
            take = conn.execute(
                "SELECT * FROM studio_takes WHERE id=? AND clip_id=? AND kind=?",
                (shot["selected_video_id"], shot_id, "video"),
            ).fetchone()
            if (
                not take
                or take["status"] != "done"
                or not Path(take["output_path"] or "").is_file()
            ):
                raise HTTPException(
                    409, "Choisissez une prise vidéo terminée avant de valider ce plan."
                )
            metadata = json.loads(take["metadata_json"] or "{}")
            from calliope.studio import director

            director.guard_approval(conn, shot, take)
            if shot["audio_mode"] == "native" and metadata.get("has_audio") is False:
                raise HTTPException(409, "La piste sonore native attendue est absente.")
            context = shot_context(conn, shot_id)
            if context.get("directing_profile", {}).get("require_reference_approval") or any(
                label.get("anchor") == "character" for label in shot.get("screen_text", [])
            ):
                from calliope.studio.visual_identity import file_hash

                review = json.loads(take["metadata_json"] or "{}").get("visual_review") or {}
                if (
                    not review.get("approved")
                    or review.get("source_sha256") != file_hash(take["output_path"])
                    or review.get("contract_hash") != visual_review_contract(context, shot)
                ):
                    raise HTTPException(
                        422,
                        "Vérifiez et approuvez l'identité et les zones "
                        "de la vidéo réelle avant le montage.",
                    )
        if shot["approved"] != approved:
            conn.execute(
                "UPDATE studio_shots SET approved=? WHERE clip_id=?", (int(approved), shot_id)
            )
            touch(conn, shot["project_id"])
        result = public_shot(conn, shot_id)
        conn.commit()
        return result


def _previous_video(conn, shot_id):
    clip = require_shot(conn, shot_id)
    rows = conn.execute(
        "SELECT c.id,sc.context_json FROM clips c JOIN scenes s ON s.id=c.scene_id "
        "LEFT JOIN studio_shot_context sc ON sc.clip_id=c.id WHERE c.project_id=? "
        "ORDER BY s.order_index,s.id,c.order_index,c.id",
        (clip["project_id"],),
    ).fetchall()
    previous = None
    for row in rows:
        if row["id"] == shot_id:
            break
        if json.loads(row["context_json"] or "{}").get("purpose") not in ("midroll", "outro"):
            previous = row["id"]
    if previous is None:
        raise HTTPException(422, "Le premier plan ne possède pas de plan précédent.")
    result = conn.execute(
        "SELECT t.* FROM studio_shots s JOIN studio_takes t ON t.id=s.selected_video_id "
        "WHERE s.clip_id=?",
        (previous,),
    ).fetchone()
    if not result or result["status"] != "done":
        raise HTTPException(409, "Choisissez une vidéo pour le plan précédent avant de poursuivre.")
    return result


def enqueue_takes(shot_id, data):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        shot = public_shot(conn, shot_id)
        from calliope.studio import director

        director.guard_generation(conn, shot, data['kind'])
        project = require_project(conn, shot["project_id"])
        from calliope.studio.series import require_active_project

        require_active_project(conn, project["id"])
        aspect = conn.execute(
            "SELECT aspect_ratio FROM studio_projects WHERE project_id=?", (project["id"],)
        ).fetchone()[0]
        kind = data["kind"]
        from calliope.studio.visual_identity import (
            compile_image_request,
            file_hash,
            reference_bindings,
            validate_value_manifest,
        )

        source = None
        if kind == "video" and shot["continue_from_previous"]:
            source = _previous_video(conn, shot_id)
        elif kind == "video" and shot["selected_image_id"]:
            source = conn.execute(
                "SELECT * FROM studio_takes WHERE id=? AND clip_id=? AND kind=?",
                (shot["selected_image_id"], shot_id, "image"),
            ).fetchone()
        prompt = shot["image_prompt"] if kind == "image" else shot["prompt"]
        if not prompt.strip():
            raise HTTPException(422, "Saisissez un prompt pour ce plan avant de générer.")
        context = shot_context(conn, shot_id)
        try:
            validate_value_manifest(shot, context)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        image_inputs = None
        if kind == "image":
            try:
                image_inputs = compile_image_request(shot, context)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            prompt = image_inputs["prompt"]
        if kind == "video":
            from calliope.studio.h3_prompt import compile_video_prompt, spoken_dialogue

            prompt = compile_video_prompt(
                shot | context.get("shot", {}), context, first_frame=bool(source),
                video_profile=settings.studio_video_profile,
            )
        parameters = {
            "prompt": prompt,
            "aspect_ratio": aspect,
            "duration_sec": shot["duration_sec"],
            "audio_mode": shot["audio_mode"],
            "image_path": source["output_path"] if source and source["kind"] == "image" else None,
            "previous_video_path": source["output_path"]
            if source and source["kind"] == "video"
            else None,
            "modal_app": settings.studio_modal_app,
            "modal_function": settings.studio_modal_function,
            "image_base_url": settings.studio_image_base_url,
            "image_model": settings.studio_image_model,
        }
        if director.enabled(conn, shot['project_id']):
            entry = next(s for s in director._state(conn, shot['project_id'])['shots'] if s['id'] == shot_id)
            parameters['director_shot_hash'] = entry['hash']
        if kind == "video":
            parameters.update(
                video_profile=settings.studio_video_profile,
                context=context,
                expected_dialogue=spoken_dialogue(shot | context.get("shot", {}), context),
            )
            if context.get("directing_profile", {}).get("require_reference_approval"):
                info = context.get("shot") or {}
                keys = (info.get("character_keys") or []) + (
                    [info["location_key"]] if info.get("location_key") else []
                )
                try:
                    parameters["reference_bindings"] = reference_bindings(
                        context["entities"], keys, True
                    )
                    parameters["reference_paths"] = [
                        binding["path"] for binding in parameters["reference_bindings"]
                    ]
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
            if source and source["output_path"]:
                parameters["source_sha256"] = file_hash(source["output_path"])
            if source and source["kind"] == "video":
                previous = conn.execute(
                    "SELECT trim_end_sec FROM studio_shots WHERE clip_id=?", (source["clip_id"],)
                ).fetchone()
                parameters["previous_video_time"] = previous[0] if previous else None
            review = require_source_visual_review(
                source, shot, context, parameters.get("previous_video_time")
            )
            if review:
                parameters["source_visual_review"] = review
                if review.get("source_frame_sha256"):
                    parameters["source_frame_sha256"] = review["source_frame_sha256"]
        if image_inputs:
            parameters.update(image_inputs)
            parameters["image_provider"] = settings.studio_image_provider
            if settings.studio_image_provider == "codex":
                parameters["image_model"] = "codex-imagegen"
        fingerprint = hashlib.sha256(
            json.dumps(
                {"parameters": parameters, "n_takes": data["n_takes"], "seed": data.get("seed")},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = conn.execute(
            "SELECT * FROM studio_takes WHERE clip_id=? AND kind=? AND request_id=? "
            "ORDER BY request_index",
            (shot_id, kind, data["request_id"]),
        ).fetchall()
        if existing:
            if existing[0]["request_hash"] != fingerprint:
                legacy = {
                    k: v
                    for k, v in parameters.items()
                    if k not in ("context", "expected_dialogue", "previous_video_time")
                }
                legacy["prompt"] = shot["prompt"] + (
                    "\n<d>" + shot["dialogue"].strip() + "</d>"
                    if shot["dialogue"].strip() and shot["audio_mode"] == "native"
                    else ""
                )
                legacy_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "parameters": legacy,
                            "n_takes": data["n_takes"],
                            "seed": data.get("seed"),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                if kind != "video" or existing[0]["request_hash"] != legacy_hash:
                    raise HTTPException(
                        409, "Cet identifiant de génération correspond déjà à une autre demande."
                    )
            return {
                "takes": [public_take(conn, t) for t in existing],
                "jobs": [
                    public_job(
                        conn.execute("SELECT * FROM jobs WHERE id=?", (t["job_id"],)).fetchone(),
                        conn,
                    )
                    for t in existing
                ],
            }
        active = conn.execute(
            "SELECT 1 FROM studio_takes t JOIN jobs j ON j.id=t.job_id "
            "WHERE t.clip_id=? AND t.kind=? AND j.status IN ('pending','running') LIMIT 1",
            (shot_id, kind),
        ).fetchone()
        if active:
            raise HTTPException(
                409,
                "Une génération est déjà active pour ce plan. Attendez son résultat ou annulez-la.",
            )
        takes, jobs = [], []
        for index in range(data["n_takes"]):
            seed = (
                (data["seed"] + index) % (2**53)
                if data.get("seed") is not None
                else secrets.randbelow(2**53)
            )
            request = {**parameters, "seed": seed}
            state = {
                "phase": "QUEUED",
                "events": [
                    {
                        "phase": "QUEUED",
                        "at": utc_now(),
                        "detail": "Demande enregistrée",
                        "source": "local",
                    }
                ],
            }
            if kind == "video":
                request["trace_id"] = uuid.uuid4().hex
                state["trace_id"] = request["trace_id"]
            cur = conn.execute(
                "INSERT INTO studio_takes(project_id,clip_id,kind,prompt,seed,engine,"
                "source_take_id,"
                "request_id,request_index,request_hash,request_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project["id"],
                    shot_id,
                    kind,
                    prompt,
                    seed,
                    "FastH3 8-Step V2 / MiniMax H3"
                    if kind == "video"
                    else "Codex imagegen" if settings.studio_image_provider == "codex"
                    else settings.studio_image_model,
                    source["id"] if source else None,
                    data["request_id"],
                    index,
                    fingerprint,
                    json.dumps(request),
                ),
            )
            take_id = cur.lastrowid
            if kind == 'video':
                director.reserve(conn, shot, take_id)
            cur = conn.execute(
                "INSERT INTO jobs(project_id,scene_id,clip_id,kind,payload_json) VALUES(?,?,?,?,?)",
                (
                    project["id"],
                    shot["scene_id"],
                    shot_id,
                    kind,
                    json.dumps(
                        {
                            "studio_take_id": take_id,
                            "video_batch_last": bool(data.get('video_batch_last')),
                            **(
                                {"parent_job_id": data["parent_job_id"]}
                                if data.get("parent_job_id")
                                else {}
                            ),
                        }
                    ),
                ),
            )
            job_id = cur.lastrowid
            conn.execute(
                "UPDATE studio_takes SET job_id=?,render_state_json=? WHERE id=?",
                (job_id, json.dumps(state), take_id),
            )
            takes.append(
                public_take(
                    conn,
                    conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone(),
                )
            )
            jobs.append(
                public_job(
                    conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), conn
                )
            )
        conn.commit()
        return {"takes": takes, "jobs": jobs}


async def enqueue_export(project_id, request_id, parent_job_id=None):
    from calliope.studio.production import assess_take, selected_video
    from calliope.studio.series import require_active_project

    with database() as conn:
        require_project(conn, project_id)
        require_active_project(conn, project_id)
        existing = conn.execute(
            'SELECT job_id FROM studio_exports WHERE project_id=? AND request_id=?',
            (project_id, request_id),
        ).fetchone()
        if existing:
            return {'job_id': existing['job_id']}
        shots = [public_shot(conn, row['id']) for row in conn.execute(
            'SELECT c.id FROM clips c JOIN scenes s ON s.id=c.scene_id WHERE c.project_id=? '
            'ORDER BY s.order_index,s.id,c.order_index,c.id', (project_id,),
        ).fetchall()]
        episode = conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        if episode:
            at_end = json.loads(episode["context_json"]).get("directing_profile", {}).get("cta_placement") == "end"
            promotions = [i for i, shot in enumerate(shots) if shot.get("purpose") == "midroll"]
            valid_position = len(promotions) == 1 and (
                promotions[0] == len(shots) - 1 if at_end else 0 < promotions[0] < len(shots) - 1
            )
            if not valid_position or any(
                shot.get("purpose") == "outro" for shot in shots
            ):
                position = "à la fin du récit" if at_end else "au milieu du récit"
                raise HTTPException(
                    409, f"L'épisode doit contenir une seule incitation {position}. "
                    "Préparez l'incitation intégrée avant d'exporter.",
                )
    evaluated = {}
    for shot in shots:
        take = selected_video(shot)
        if shot['approved'] and take:
            await assess_take(shot)
            evaluated[shot['id']] = take['id']
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_project(conn, project_id)
        require_active_project(conn, project_id)
        existing = conn.execute(
            "SELECT * FROM studio_exports WHERE project_id=? AND request_id=?",
            (project_id, request_id),
        ).fetchone()
        if existing:
            return {"job_id": existing["job_id"]}
        meta = conn.execute(
            "SELECT * FROM studio_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        clips, missing = [], []
        from calliope.studio import director
        for row in conn.execute(
            "SELECT c.id,c.scene_id,s.heading,c.order_index FROM clips c "
            "JOIN scenes s ON s.id=c.scene_id "
            "WHERE c.project_id=? ORDER BY s.order_index,s.id,c.order_index,c.id",
            (project_id,),
        ):
            shot = public_shot(conn, row["id"])
            take = conn.execute(
                "SELECT * FROM studio_takes WHERE id=? AND clip_id=?",
                (shot["selected_video_id"], row["id"]),
            ).fetchone()
            if shot['approved'] and take:
                director.guard_approval(conn, shot, take)
            if (
                not shot["approved"] or not take or not Path(take["output_path"] or "").is_file()
                or evaluated.get(row['id']) != take['id']
            ):
                missing.append(f"{row['heading']} / plan {row['order_index']}")
                continue
            effective = json.loads(take["metadata_json"] or "{}")
            if shot["audio_mode"] == "native" and effective.get("has_audio") is False:
                missing.append(f"{row['heading']} / plan {row['order_index']} (piste sonore absente)")
                continue
            clips.append(
                {
                    "clip_id": row["id"],
                    "scene_id": row["scene_id"],
                    "take_id": take["id"],
                    "path": take["output_path"],
                    "duration_sec": effective.get("duration_sec") or shot["duration_sec"],
                    "beat_sec": shot["duration_sec"],
                    "dialogue": shot["dialogue"],
                    "trim_start_sec": shot.get("trim_start_sec"),
                    "trim_end_sec": shot.get("trim_end_sec"),
                    "audio_mode": shot["audio_mode"],
                    "screen_text": shot.get("screen_text", []),
                    "purpose": shot.get("purpose", ""),
                    "prompt_contract_version": shot["prompt_contract_version"],
                    "action": shot.get("action", ""),
                    "reaction": shot.get("reaction", ""),
                    "dialogue_turns": shot.get("dialogue_turns", []),
                    "transcript": (
                        (effective.get('transcription_cache') or {}).get('transcript')
                        if (effective.get("dialogue_qc") or {}).get("status") == "ok" else None
                    ),
                }
            )
        if missing or not clips:
            raise HTTPException(
                409,
                "Choisissez et validez chaque plan avant l'export. Plans restants: "
                + ", ".join(missing[:12]),
            )
        snapshot = {
            "aspect_ratio": meta["aspect_ratio"],
            "clips": clips,
            "revision": meta["revision"],
        }
        if parent_job_id:
            snapshot["parent_job_id"] = parent_job_id
        episode = conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        if episode:
            frozen = json.loads(episode[0])
            snapshot["target_seconds"] = frozen.get("target_seconds")
            from calliope.studio.h3_prompt import directing_profile
            snapshot["transitions"] = directing_profile(
                frozen.get("directing_profile")
            )["transitions"]
        from calliope.studio.audio import snapshot_audio
        snapshot["audio"] = snapshot_audio(conn, project_id)
        cur = conn.execute(
            "INSERT INTO studio_exports(project_id,request_id,revision,aspect_ratio,snapshot_json) "
            "VALUES(?,?,?,?,?)",
            (project_id, request_id, meta["revision"], meta["aspect_ratio"], json.dumps(snapshot)),
        )
        export_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO jobs(project_id,kind,payload_json) VALUES(?,?,?)",
            (project_id, "export", json.dumps({**snapshot, "studio_export_id": export_id})),
        )
        conn.execute("UPDATE studio_exports SET job_id=? WHERE id=?", (cur.lastrowid, export_id))
        conn.commit()
        return {"job_id": cur.lastrowid}


async def _generate_image(request, *, check_cancel=None):
    if not settings.dry_run:
        raise ValueError('Images manuelles uniquement: utilisez le paquet pour ChatGPT puis importez les images.')
    from calliope.studio.visual_identity import verify_reference_inputs

    verify_reference_inputs(request)
    if request.get("image_provider", "api") != settings.studio_image_provider:
        raise ValueError("Le générateur d'images a changé. Créez une nouvelle prise avec les réglages actuels.")
    if request.get("image_provider") == "codex":
        from calliope.studio.codex_image import generate
        content, metadata = await generate(request, check_cancel=check_cancel)
        content, dimensions = normalized_image(content)
        return content, metadata | dimensions
    if request["image_base_url"].rstrip("/") != settings.studio_image_base_url.rstrip("/"):
        raise ValueError(
            "Le service d'images a changé depuis cette demande. "
            "Créez une nouvelle prise pour utiliser les réglages actuels."
        )
    if (
        not settings.studio_image_api_key
        and "127.0.0.1" not in request["image_base_url"]
        and "localhost" not in request["image_base_url"]
    ):
        raise ValueError(
            "Renseignez la clé du service d'images dans les réglages, ou importez une image."
        )
    headers = (
        {"Authorization": "Bearer " + settings.studio_image_api_key}
        if settings.studio_image_api_key
        else {}
    )
    size = {"16:9": "1792x1024", "9:16": "1024x1792", "1:1": "1024x1024"}[request["aspect_ratio"]]
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, read=600), follow_redirects=True) as client:
        payload = {
            "model": request["image_model"], "prompt": request["prompt"], "size": size, "n": 1,
        }
        references = request.get("reference_paths", [])
        if request["image_model"].startswith("agnes-image-"):
            payload.update(size="1K", ratio=request["aspect_ratio"])
            payload["extra_body"] = {"response_format": "b64_json"}
            if references:
                payload["extra_body"]["image"] = [
                    "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()
                    for path in references
                ]
            else:
                payload["return_base64"] = True
            for attempt in range(4):
                if check_cancel:
                    await check_cancel()
                try:
                    response = await client.post(
                        request["image_base_url"].rstrip("/") + "/images/generations",
                        headers=headers, json=payload,
                    )
                except httpx.ReadTimeout as exc:
                    raise ValueError(
                        "Le service d'images est resté sans réponse pendant dix minutes. "
                        "Reprenez la production pour réessayer."
                    ) from exc
                except httpx.ReadError as exc:
                    if attempt == 3:
                        raise ValueError(
                            "Connexion au service d'images interrompue après quatre tentatives. "
                            "Reprenez la production pour réessayer."
                        ) from exc
                else:
                    if (
                        response.status_code != 503
                        or "image queue is full" not in response.text.lower()
                        or attempt == 3
                    ):
                        break
                for _ in range(10 * 2 ** attempt):
                    await asyncio.sleep(1)
                    if check_cancel:
                        await check_cancel()
        elif references:
            files = [
                ("image", (Path(path).name, Path(path).read_bytes(), "image/png"))
                for path in references
            ]
            response = await client.post(
                request["image_base_url"].rstrip("/") + "/images/edits", headers=headers,
                data={key: str(value) for key, value in payload.items()}, files=files,
            )
        else:
            response = await client.post(
                request["image_base_url"].rstrip("/") + "/images/generations",
                headers=headers, json=payload,
            )
        if response.status_code >= 400:
            raise ValueError(
                f"Service d'images HTTP {response.status_code}: {safe_error(response.text)}"
            )
        data = response.json().get("data") or []
        if not data or not isinstance(data[0], dict):
            raise ValueError("Le service d'images n'a renvoyé aucun asset.")
        item = data[0]
        if item.get("b64_json"):
            content = base64.b64decode(item["b64_json"], validate=True)
        elif isinstance(item.get("url"), str) and item["url"].startswith(("https://", "http://")):
            async with client.stream("GET", item["url"]) as download:
                download.raise_for_status()
                chunks, size = [], 0
                async for chunk in download.aiter_bytes():
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:
                        raise ValueError("Image générée supérieure à 25 Mo.")
                    chunks.append(chunk)
                content = b"".join(chunks)
        else:
            raise ValueError("Réponse image incompatible: une URL ou b64_json est requis.")
    return normalized_image(content)


async def video_idle_window(seconds, *, job_id=-1, app_name=None, function_name=None, engine=None):
    if settings.dry_run or settings.studio_video_profile != 'ltx':
        return False
    app_name = app_name or settings.studio_modal_app
    function_name = function_name or settings.studio_modal_function
    try:
        if seconds == 2:
            with database() as conn:
                pending = conn.execute(
                    "SELECT 1 FROM studio_takes t JOIN jobs j ON j.id=t.job_id "
                    "WHERE t.kind='video' AND j.status IN ('pending','running') "
                    "AND j.id<>? AND COALESCE(json_extract(j.payload_json,'$.parent_job_id'),-1)<>? "
                    "AND json_extract(t.request_json,'$.modal_app')=? "
                    "AND json_extract(t.request_json,'$.modal_function')=? LIMIT 1",
                    (job_id, job_id, app_name, function_name),
                ).fetchone()
                other_batch = conn.execute(
                    "SELECT 1 FROM studio_productions WHERE status='running' AND stage='video' "
                    "AND job_id<>? LIMIT 1", (job_id,),
                ).fetchone()
            if pending or other_batch:
                return False
        from calliope.engines.modal_h3 import ModalH3Engine
        engine = engine or ModalH3Engine(app_name, function_name)
        await asyncio.wait_for(asyncio.to_thread(engine.set_idle_window, seconds), timeout=5)
        return True
    except Exception:
        logger.warning('Unable to adjust Modal idle window to %ss', seconds, exc_info=True)
        return False


async def run_studio_job(job, payload, should_stop):
    if payload.get("studio_export_id"):
        return await _run_export(job, payload, should_stop)
    take_id = payload["studio_take_id"]
    with database() as conn:
        row = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone()
        if not row:
            raise ValueError("Le plan ou sa prise a été supprimé.")
        take = dict(row)
        if take["output_path"] and Path(take["output_path"]).is_file() and take["status"] == "done":
            return [take["output_path"]]
        request = json.loads(take["request_json"])
        from calliope.studio import director

        director.guard_generation(conn, public_shot(conn, take['clip_id']), take['kind'])
        if director.enabled(conn, take['project_id']):
            director.guard_budget(conn, take['project_id'])
            current = director._state(conn, take['project_id'])
            entry = next(s for s in current['shots'] if s['id'] == take['clip_id'])
            if request.get('director_shot_hash') != entry['hash']:
                raise ValueError('Le plan a change depuis la preparation de cette prise.')
        conn.execute("UPDATE studio_takes SET status='running',error=NULL WHERE id=?", (take_id,))
        conn.commit()
    directory = settings.assets_dir / str(take["project_id"]) / "studio" / "takes" / str(take_id)
    directory.mkdir(parents=True, exist_ok=True)
    standalone_video = take['kind'] == 'video' and not payload.get('parent_job_id')
    telemetry = None
    control_engine = None
    gpu_released = False
    try:
        if standalone_video:
            await video_idle_window(60, job_id=job['id'], app_name=request['modal_app'],
                                    function_name=request['modal_function'])
        if not take["modal_call_id"]:
            from calliope.studio.visual_identity import (
                compile_image_request,
                file_hash,
                verify_reference_inputs,
            )

            verify_reference_inputs(request)
            if take["kind"] == "image" and request.get("require_reference_approval"):
                with database() as conn:
                    current = public_shot(conn, take["clip_id"])
                    compiled = compile_image_request(current, shot_context(conn, take["clip_id"]))
                if compiled["reference_bindings"] != request.get("reference_bindings") or compiled[
                    "input_hashes"
                ] != request.get("input_hashes"):
                    raise ValueError("Le contrat visuel a changé. Créez une nouvelle demande.")
            if request.get("source_sha256"):
                source_path = request.get("image_path") or request.get("previous_video_path")
                if not source_path or file_hash(source_path) != request["source_sha256"]:
                    raise ValueError(
                        "L'image de départ a changé depuis sa préparation. "
                        "Créez une nouvelle demande."
                    )
            if take["kind"] == "video":
                with database() as conn:
                    current = public_shot(conn, take["clip_id"])
                    context = shot_context(conn, take["clip_id"])
                    source = conn.execute(
                        "SELECT * FROM studio_takes WHERE id=?", (take["source_take_id"],)
                    ).fetchone()
                    selected_source = (
                        _previous_video(conn, current["id"])
                        if current["continue_from_previous"]
                        else None
                    )
                    if request.get("source_visual_review") and (
                        not source
                        or (
                            source["kind"] == "image"
                            and current["selected_image_id"] != source["id"]
                        )
                        or (
                            source["kind"] == "video"
                            and (not selected_source or selected_source["id"] != source["id"])
                        )
                    ):
                        raise ValueError(
                            "L'image de départ choisie a changé. Créez une nouvelle demande."
                        )
                    source_time = None
                    if source and source["kind"] == "video":
                        source_time = conn.execute(
                            "SELECT trim_end_sec FROM studio_shots WHERE clip_id=?",
                            (source["clip_id"],),
                        ).fetchone()[0]
                    current_review = require_source_visual_review(
                        source, current, context, source_time
                    )
                    if (
                        request.get("source_visual_review")
                        and current_review != request["source_visual_review"]
                    ):
                        raise ValueError(
                            "La référence approuvée a changé. Créez une nouvelle demande."
                        )
        if take["kind"] == "image":
            async def check_image_cancel():
                if should_stop():
                    raise asyncio.CancelledError()
                if queue_manager.is_cancelled(job["id"]):
                    raise StudioCancelledError()

            content, metadata = await _generate_image(request, check_cancel=check_image_cancel)
            path = directory / "image.png"
            if queue_manager.is_cancelled(job["id"]):
                raise StudioCancelledError()
            path.write_bytes(content)
        else:
            from calliope.engines.modal_h3 import ModalH3Engine, build_request
            from calliope.studio.export import inspect_media

            batch = job.get('video_preparation')
            if batch and (batch['modal_app'], batch['modal_function']) != (
                request['modal_app'], request['modal_function'],
            ):
                raise ValueError('Le moteur vidéo a changé après la préparation.')
            engine = batch['engine'] if batch and batch.get('concurrency', settings.studio_video_concurrency) == 1 else (
                ModalH3Engine(request["modal_app"], request["modal_function"])
            )
            control_engine = engine
            call_id = take["modal_call_id"]
            state = json.loads(take["render_state_json"] or "{}")
            if "identity" not in state and hasattr(engine, "identity"):
                render_event(take_id, identity=batch['identity'] if batch else
                             await asyncio.to_thread(engine.identity))
            started_at = state.get("resume_started_at") or state.get("started_at") or utc_now()
            render_event(
                take_id,
                started_at=state.get("started_at") or started_at,
                progress_limited=not bool(request.get("trace_id")),
            )
            if not call_id:
                if queue_manager.is_cancelled(job["id"]):
                    raise StudioCancelledError()
                if take["submission_state"] == "submitting":
                    raise ValueError(
                        "Soumission Modal interrompue avant sa confirmation. "
                        "Vérifiez les appels Modal avant de relancer ce plan."
                    )
                if batch:
                    from calliope.studio.video_preparation import prepared_request

                    prepared = await asyncio.to_thread(
                        prepared_request, request, batch['shots'][take['clip_id']],
                    )
                    render_event(take_id, inputs_prepared_before_gpu=True,
                                 batch_preparation_seconds=batch['preparation_seconds'])
                else:
                    prepared = await asyncio.to_thread(
                        build_request,
                        prompt=request["prompt"],
                        duration_sec=request["duration_sec"],
                        aspect_ratio=request["aspect_ratio"],
                        seed=request["seed"],
                        image_path=Path(request["image_path"]) if request["image_path"] else None,
                        previous_video_path=Path(request["previous_video_path"])
                        if request["previous_video_path"]
                        else None,
                        audio_mode=request["audio_mode"],
                        trace_id=request.get("trace_id"),
                        previous_video_time=request.get("previous_video_time"),
                        video_profile=request.get("video_profile", "fast"),
                    )
                if should_stop():
                    raise asyncio.CancelledError()
                if queue_manager.is_cancelled(job["id"]):
                    raise StudioCancelledError()
                if request.get("source_frame_sha256"):
                    frame_digest = hashlib.sha256(
                        base64.b64decode(prepared["image_base64"])
                    ).hexdigest()
                    if frame_digest != request["source_frame_sha256"]:
                        raise ValueError("La frame préparée diffère de la frame approuvée.")
                with database() as conn:
                    conn.execute(
                        "UPDATE studio_takes SET submission_state='submitting' WHERE id=?",
                        (take_id,),
                    )
                    conn.commit()
                submission = asyncio.create_task(asyncio.to_thread(engine.spawn, prepared))
                try:
                    call_id = await asyncio.shield(submission)
                except asyncio.CancelledError:
                    call_id = await submission
                    with database() as conn:
                        conn.execute(
                            "UPDATE studio_takes SET modal_call_id=?,"
                            "submission_state='submitted' WHERE id=?",
                            (call_id, take_id),
                        )
                        conn.commit()
                    render_event(take_id, "SUBMITTED", "Appel confirmé pendant arrêt")
                    if queue_manager.is_cancelled(job["id"]):
                        await cancel_modal_take(take_id)
                        raise StudioCancelledError() from None
                    raise
                with database() as conn:
                    conn.execute(
                        "UPDATE studio_takes SET modal_call_id=?,"
                        "submission_state='submitted' WHERE id=?",
                        (call_id, take_id),
                    )
                    conn.commit()
                render_event(take_id, "SUBMITTED", "Appel Modal confirmé")
            render_event(
                take_id,
                "WAITING_FOR_GPU",
                "Appel soumis; allocation et démarrage non distingués",
            )

            async def fetch_progress():
                snapshot = await asyncio.to_thread(engine.progress, request['trace_id'])
                if snapshot and not getattr(engine, 'remote_terminal', False):
                    render_event(take_id, snapshot=snapshot)

            while True:
                if queue_manager.is_cancelled(job["id"]):
                    await cancel_modal_take(take_id)
                    raise StudioCancelledError()
                if should_stop():
                    raise asyncio.CancelledError()
                elapsed = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(started_at)
                ).total_seconds()
                if elapsed > settings.studio_render_timeout_sec:
                    raise TimeoutError(
                        "Délai global dépassé. Reprenez le même appel Modal "
                        "pour récupérer son résultat."
                    )
                try:
                    result = await asyncio.to_thread(engine.poll, call_id)
                except Exception as exc:
                    from calliope.engines.modal_h3 import is_poll_network_error

                    if getattr(engine, "remote_terminal", False):
                        render_event(
                            take_id,
                            remote_terminal=True,
                            remote_terminal_reason="sdk_result",
                        )
                    if not is_poll_network_error(exc):
                        raise
                    render_event(take_id, poll_error=safe_error(exc))
                    await asyncio.sleep(max(1, settings.queue_poll_interval_sec))
                    continue
                if result is not None:
                    render_event(
                        take_id,
                        remote_terminal=True,
                        remote_terminal_reason="result_received",
                    )
                    if payload.get('video_batch_last') or standalone_video:
                        gpu_released = await video_idle_window(
                            2, job_id=payload.get('parent_job_id') or job['id'],
                            app_name=request['modal_app'], function_name=request['modal_function'],
                            engine=engine,
                        )
                        if batch:
                            batch['gpu_released'] = gpu_released
                    break
                if request.get("trace_id") and hasattr(engine, "progress"):
                    if telemetry and telemetry.done():
                        telemetry.result()
                        telemetry = None
                    if telemetry is None:
                        telemetry = asyncio.create_task(fetch_progress())
                await event_bus.publish(
                    "job.progress",
                    {
                        "job_id": job["id"],
                        "project_id": take["project_id"],
                        "message": "Initialisation GPU ou rendu FastH3 en cours. "
                        "Vous pouvez fermer puis reprendre le studio.",
                    },
                )
                await asyncio.sleep(max(1, settings.queue_poll_interval_sec))
            if queue_manager.is_cancelled(job["id"]):
                raise StudioCancelledError()
            render_event(take_id, "DOWNLOADING", "Résultat distant reçu", poll_error=None)
            content = base64.b64decode(result["video_base64"], validate=True)
            path = directory / "video.mp4"
            temporary = directory / "download.mp4"
            temporary.write_bytes(content)
            try:
                render_event(take_id, "VALIDATING", "Contrôle du média")
                info = await asyncio.to_thread(inspect_media, temporary)
                if request["audio_mode"] == "native" and not info["has_audio"]:
                    raise ValueError("Le rendu FastH3 n'a pas de piste audio native.")
                metadata = {**result.get("metadata", {}), **info}
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            poster = directory / "poster.jpg"
            await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(poster),
                ],
                capture_output=True,
                timeout=30,
            )
            if poster.is_file():
                metadata["poster_path"] = str(poster)
        with database() as conn:
            if queue_manager.is_cancelled(job["id"]):
                raise StudioCancelledError()
            conn.execute(
                "UPDATE studio_takes SET status='done',output_path=?,metadata_json=?,"
                "error=NULL WHERE id=?",
                (str(path), json.dumps(metadata), take_id),
            )
            conn.commit()
        render_event(take_id, "DONE", "Média enregistré")
        return [str(path)]
    except StudioCancelledError:
        render_event(take_id, "CANCELLED", "Annulation demandée")
        with database() as conn:
            conn.execute(
                "UPDATE studio_takes SET status='cancelled',error='cancelled' WHERE id=?",
                (take_id,),
            )
            conn.commit()
        raise
    except Exception as exc:
        render_event(take_id, "FAILED", safe_error(exc))
        with database() as conn:
            conn.execute(
                "UPDATE studio_takes SET status='failed',error=? WHERE id=?",
                (safe_error(exc), take_id),
            )
            conn.commit()
        raise RuntimeError(safe_error(exc)) from None
    finally:
        if telemetry:
            telemetry.cancel()
            await asyncio.gather(telemetry, return_exceptions=True)
        if standalone_video and not gpu_released:
            await video_idle_window(2, job_id=job['id'], app_name=request['modal_app'],
                                    function_name=request['modal_function'], engine=control_engine)


async def _run_export(job, payload, should_stop):
    from calliope.studio.export import assemble

    path = (
        settings.assets_dir
        / str(job["project_id"])
        / "studio"
        / "exports"
        / f"film-{payload['studio_export_id']}.mp4"
    )

    async def progress(data):
        await event_bus.publish(
            "job.progress", {"job_id": job["id"], "project_id": job["project_id"], **data}
        )

    try:
        metadata = await assemble(
            payload,
            path,
            progress_callback=progress,
            cancel_check=lambda: should_stop() or queue_manager.is_cancelled(job["id"]),
        )
    except InterruptedError:
        if should_stop():
            raise asyncio.CancelledError() from None
        raise StudioCancelledError() from None
    if queue_manager.is_cancelled(job["id"]):
        raise StudioCancelledError()
    with database() as conn:
        conn.execute(
            "UPDATE studio_exports SET output_path=?,metadata_json=? WHERE id=?",
            (str(path), json.dumps(metadata), payload["studio_export_id"]),
        )
        conn.execute(
            "UPDATE projects SET status='completed' WHERE id=? AND EXISTS "
            "(SELECT 1 FROM studio_projects WHERE project_id=? AND revision=?)",
            (job["project_id"], job["project_id"], payload["revision"]),
        )
        conn.commit()
    from calliope.studio.production import commit_episode
    await commit_episode(job["project_id"], payload["revision"])
    return [str(path)]
