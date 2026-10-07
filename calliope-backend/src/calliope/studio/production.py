import asyncio
import hashlib
import json
from pathlib import Path

from calliope.config import settings
from calliope.events.bus import event_bus
from calliope.queue.manager import queue_manager
from calliope.studio import audio, series, service
from calliope.studio.series_planner import plan_episode


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
        conn.execute(
            "UPDATE jobs SET status='running',error=NULL,started_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )
        conn.commit()
    try:
        outputs = await service.run_studio_job(job, json.loads(job["payload_json"]), should_stop)
        queue_manager.mark_done(job_id, outputs)
        return outputs
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
                    "escription) VALUES(?,?,?,?,?)"
                ),
                (
                    episode["series_id"],
                    entity["key"],
                    entity["kind"],
                    entity.get("name") or entity["key"],
                    entity["description"],
                ),
            )
        script = []
        for scene in plan["scenes"]:
            scene_id = service.add_scene(conn, project_id, scene)
            shots = conn.execute(
                "SELECT id FROM clips WHERE scene_id=? ORDER BY order_index,id", (scene_id,)
            ).fetchall()
            script.append(scene.get("heading", ""))
            for row, shot in zip(shots, scene["shots"]):
                conn.execute(
                    "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?)",
                    (
                        row[0],
                        json.dumps(
                            {
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
        conn.execute(
            "INSERT INTO studio_audio(project_id,settings_json) VALUES(?,?)",
            (project_id, json.dumps(plan.get("audio") or audio.AudioSettings().model_dump())),
        )
        conn.commit()


def image_references(conn, shot_id):
    row = conn.execute(
        (
            "SELECT e.series_id,sc.context_json FROM clips c JOIN studio_episodes e ON e.pr"
            "oject_id=c.project_id JOIN studio_shot_context sc ON sc.clip_id=c.id WHERE c.i"
            "d=?"
        ),
        (shot_id,),
    ).fetchone()
    if not row:
        return []
    info = json.loads(row["context_json"])
    keys = info.get("character_keys", []) + (
        [info["location_key"]] if info.get("location_key") else []
    )
    paths = []
    for key in dict.fromkeys(keys):
        entity = conn.execute(
            "SELECT reference_path FROM studio_entities WHERE series_id=? AND entity_key=?",
            (row["series_id"], key),
        ).fetchone()
        if entity and entity[0] and Path(entity[0]).is_file():
            paths.append(entity[0])
    return paths[:4]


def generation_id(job, shot, kind):
    inputs = [
        shot["prompt"] if kind == "video" else shot["image_prompt"],
        shot["dialogue"],
        shot["duration_sec"],
        shot["selected_image_id"] if kind == "video" else None,
        settings.studio_image_model,
        settings.studio_modal_app,
        settings.studio_modal_function,
    ]
    if kind == "image":
        with service.database() as conn:
            inputs.append(image_references(conn, shot["id"]))
    digest = hashlib.sha256(json.dumps(inputs).encode()).hexdigest()[:16]
    return f"auto-{job['id']}-{shot['id']}-{kind}-{digest}"


async def generate_take(job, shot, kind, should_stop):
    request = {"kind": kind, "request_id": generation_id(job, shot, kind), "n_takes": 1}
    queued = service.enqueue_takes(shot["id"], request)
    take = queued["takes"][0]
    await run_child(take["job_id"], should_stop)
    return service.select_take(take["id"])


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
        current_refs = image_references(conn, shot["id"])
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
                and request.get("reference_paths", []) == current_refs
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
        current = series.require_series(conn, episode["series_id"])
        items = series.entities(conn, episode["series_id"])
    for index, entity in enumerate(items):
        await checkpoint(
            job,
            "images",
            0.12 + index / max(1, len(items)) * 0.08,
            f"Référence : {entity['name']}",
            should_stop,
        )
        if entity["reference_path"] and Path(entity["reference_path"]).is_file():
            continue
        directory = settings.assets_dir / "series" / str(episode["series_id"]) / "references"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"entity-{entity['id']}.png"
        if not path.is_file():
            style = frozen["bible"].get("visual_style") or current["bible"]["visual_style"]
            prompt = (
                f"Canonical visual reference for {entity['kind']}: {entity['description']}. "
                f"Visual style: {style}. Universe: {current['bible']['universe']}. "
                "A single consistent medium view, clean composition, no collage, no watermark. "
                + (
                    "The character's natural mouth is visible. Preserve costume and identity."
                    if entity["kind"] == "character"
                    else "An empty establishing view of the location, no people."
                )
            )
            content, _ = await service._generate_image(
                {
                    "prompt": prompt,
                    "aspect_ratio": frozen["aspect_ratio"],
                    "image_base_url": settings.studio_image_base_url,
                    "image_model": settings.studio_image_model,
                }
            )
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        with service.database() as conn:
            conn.execute(
                "UPDATE studio_entities SET reference_path=? WHERE id=? AND series_id=?",
                (str(path), entity["id"], episode["series_id"]),
            )
            conn.commit()


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
                json.loads(episode["context_json"]), episode["idea"], episode["objective"]
            )
            await checkpoint(job, "writing", 0.1, "Scénario préparé", should_stop)
            save_episode_plan(project_id, plan)
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
            if not shot["selected_image_id"]:
                if not reuse_reference(project_id, shot):
                    shot = await generate_take(job, shot, "image", should_stop)
        for index, initial in enumerate(shots):
            await checkpoint(
                job,
                "video",
                0.33 + index / len(shots) * 0.55,
                f"FastH3 : plan {index + 1}/{len(shots)}",
                should_stop,
            )
            with service.database() as conn:
                shot = service.public_shot(conn, initial["id"])
            expected = shot["prompt"] + (
                "\n<d>" + shot["dialogue"].strip() + "</d>" if shot["dialogue"].strip() else ""
            )
            chosen = next((t for t in shot["takes"] if t["id"] == shot["selected_video_id"]), None)
            if not chosen or (
                not shot["approved"]
                and chosen["engine"] != "import"
                and chosen["prompt"] != expected
            ):
                shot = await generate_take(job, shot, "video", should_stop)
            service.approve_shot(shot["id"], True)
        await checkpoint(job, "mixing", 0.9, "Musique, bruitages et assemblage du MP4", should_stop)
        revision = service.board(project_id)["project"]["revision"]
        queued = service.enqueue_export(project_id, f"auto-{job['id']}-export-{revision}")
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
