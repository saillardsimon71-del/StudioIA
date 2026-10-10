import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from calliope.config import settings
from calliope.studio import service
from calliope.studio.h3_prompt import directing_profile

BIBLE_FIELDS = (
    "universe",
    "tone",
    "visual_style",
    "characters",
    "relationships",
    "locations",
    "world_rules",
    "past_events",
    "ongoing_plot",
    "continuity",
)


def require_series(conn, series_id):
    row = conn.execute("SELECT * FROM studio_series WHERE id=?", (series_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Série introuvable.")
    result = dict(row)
    result["archived"] = bool(result["archived"])
    result["bible"] = {key: "" for key in BIBLE_FIELDS} | json.loads(result.pop("bible_json"))
    result["directing_profile"] = directing_profile(
        json.loads(result.pop("directing_profile_json"))
    )
    return result


def entities(conn, series_id):
    result = []
    for row in conn.execute(
        "SELECT * FROM studio_entities WHERE series_id=? ORDER BY id", (series_id,)
    ):
        item = dict(row)
        item["key"] = item.pop("entity_key")
        item["identity"] = {
            "visual_description": "",
            "variant_of": "",
            "reference_approved": False,
            "reference_sha256": "",
            "attached_value": None,
            "label_key": "",
        } | json.loads(item.pop("identity_json"))
        item["reference_request"] = json.loads(item.pop("reference_request_json"))
        item["url"] = service.media_url(item.get("reference_path"))
        result.append(item)
    return result


def require_references_editable(conn, series_id):
    require_series(conn, series_id)
    if conn.execute(
        "SELECT 1 FROM studio_productions p JOIN studio_episodes e ON e.project_id=p.project_id "
        "WHERE e.series_id=? AND p.status IN ('pending','running')",
        (series_id,),
    ).fetchone():
        raise HTTPException(409, "Mettez la production en pause avant de modifier ses références.")


def invalidate_reference_dependents(conn, series_id, key):
    changed = {key}
    pending = entities(conn, series_id)
    while pending:
        affected = [entity for entity in pending if entity["identity"].get("variant_of") in changed]
        if not affected:
            break
        for entity in affected:
            identity = entity["identity"] | {"reference_approved": False, "reference_sha256": ""}
            conn.execute(
                "UPDATE studio_entities SET identity_json=? WHERE id=?",
                (json.dumps(identity), entity["id"]),
            )
            changed.add(entity["key"])
        pending = [entity for entity in pending if entity not in affected]


def update_entity(series_id, entity_id, data):
    from calliope.studio.visual_identity import file_hash, ordered_entities

    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_references_editable(conn, series_id)
        current = next(
            (entity for entity in entities(conn, series_id) if entity["id"] == entity_id), None
        )
        if not current:
            raise HTTPException(404, "Référence introuvable dans cette série.")
        identity = current["identity"]
        patch = data.get("identity") or {}
        identity_changed = any(identity.get(key) != value for key, value in patch.items())
        identity.update(patch)
        if (
            identity_changed
            or data.get("reset_reference")
            or data.get("reference_approved") is False
        ):
            identity.update(reference_approved=False, reference_sha256="")
        if data.get("reset_reference"):
            current["reference_path"] = None
        try:
            ordered_entities(
                [
                    entity if entity["id"] != entity_id else current
                    for entity in entities(conn, series_id)
                ]
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if data.get("reference_approved"):
            if not current.get("reference_path") or not Path(current["reference_path"]).is_file():
                raise HTTPException(422, "Importez ou préparez une référence avant de l'approuver.")
            if not str(identity.get("visual_description") or "").strip():
                raise HTTPException(
                    422, "Renseignez l'identité visuelle explicite avant d'approuver."
                )
            identity.update(
                reference_approved=True, reference_sha256=file_hash(current["reference_path"])
            )
        conn.execute(
            "UPDATE studio_entities SET identity_json=?,reference_path=? "
            "WHERE id=? AND series_id=?",
            (json.dumps(identity), current.get("reference_path"), entity_id, series_id),
        )
        if (
            identity_changed
            or data.get("reset_reference")
            or data.get("reference_approved") is False
        ):
            invalidate_reference_dependents(conn, series_id, current["key"])
        conn.commit()
    return get_series(series_id)


def refresh_visual_contract(project_id):
    from calliope.studio.visual_identity import validate_value_manifest

    require_editable(project_id)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        episode = conn.execute(
            "SELECT series_id,context_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        if not episode:
            raise HTTPException(404, "Épisode de série introuvable.")
        current = require_series(conn, episode["series_id"])
        canon = entities(conn, episode["series_id"])
        by_key = {entity["key"]: entity for entity in canon}
        frozen = json.loads(episode["context_json"])
        expected = {
            key: int(re.sub(r"[ ,]", "", str(value)))
            for key, value in frozen.get("display_values", {}).items()
            if re.fullmatch(r"-?[\d ,]+", str(value))
        }
        shots = conn.execute(
            "SELECT c.id,c.duration_sec,sc.context_json FROM clips c "
            "JOIN scenes s ON s.id=c.scene_id "
            "JOIN studio_shot_context sc ON sc.clip_id=c.id "
            "WHERE c.project_id=? ORDER BY s.order_index,s.id,c.order_index,c.id",
            (project_id,),
        ).fetchall()
        for row in shots:
            info = json.loads(row["context_json"])
            visible = list(info.get("character_keys", []))
            if info.get("location_key"):
                visible.append(info["location_key"])
            for key in visible:
                entity = by_key.get(key)
                identity = (entity or {}).get("identity") or {}
                if not entity or (
                    current["directing_profile"]["require_reference_approval"]
                    and not identity.get("visual_description")
                ):
                    raise HTTPException(
                        422, f"Plan {row['id']}: identité visuelle explicite manquante pour {key}."
                    )
            try:
                validate_value_manifest(
                    {"duration_sec": row["duration_sec"]},
                    {
                        "entities": canon,
                        "directing_profile": current["directing_profile"],
                        "shot": info,
                    },
                    expected,
                )
            except ValueError as exc:
                raise HTTPException(422, f"Plan {row['id']}: {exc}") from None
        frozen["entities"] = canon
        frozen["directing_profile"] = current["directing_profile"]
        frozen.setdefault("bible", {})["visual_style"] = current["bible"]["visual_style"]
        conn.execute(
            "UPDATE studio_episodes SET context_json=? WHERE project_id=?",
            (json.dumps(frozen), project_id),
        )
        for row in shots:
            info = json.loads(row["context_json"])
            for label in info.get("screen_text", []):
                label.pop("anchor_binding", None)
            conn.execute(
                "UPDATE studio_shot_context SET context_json=? WHERE clip_id=?",
                (json.dumps(info), row["id"]),
            )
        for take in conn.execute(
            "SELECT id,metadata_json FROM studio_takes WHERE project_id=?", (project_id,)
        ).fetchall():
            metadata = json.loads(take["metadata_json"] or "{}")
            metadata.pop("visual_review", None)
            metadata.pop("continuation_reviews", None)
            conn.execute(
                "UPDATE studio_takes SET metadata_json=? WHERE id=?",
                (json.dumps(metadata), take["id"]),
            )
        conn.execute(
            "UPDATE studio_shots SET approved=0 "
            "WHERE clip_id IN (SELECT id FROM clips WHERE project_id=?)",
            (project_id,),
        )
        conn.execute(
            "UPDATE studio_productions SET status='paused',message=? WHERE project_id=?",
            (
                "Contrat visuel préparé. Vérifiez les références et les ancrages "
                "avant de reprendre.",
                project_id,
            ),
        )
        service.touch(conn, project_id)
        conn.commit()
    return service.board(project_id)


def get_series(series_id):
    with service.database() as conn:
        result = require_series(conn, series_id)
        result["entities"] = entities(conn, series_id)
        result["episodes"] = []
        for row in conn.execute(
            (
                "SELECT e.*,p.title,sp.revision,pr.status,pr.stage,pr.progress,pr.message,p"
                "r.error,pr.job_id FROM studio_episodes e JOIN projects p ON p.id=e.project"
                "_id JOIN studio_projects sp ON sp.project_id=e.project_id LEFT JOIN studio"
                "_productions pr ON pr.project_id=e.project_id WHERE e.series_id=? ORDER BY"
                " e.number"
            ),
            (series_id,),
        ):
            item = dict(row)
            item.pop("context_json")
            item.pop("plan_json")
            item["events"] = json.loads(item.pop("events_json"))
            item["continuity_stale"] = (
                item["canon_revision"] is not None and item["canon_revision"] != item["revision"]
            )
            exported = conn.execute(
                (
                    "SELECT output_path FROM studio_exports WHERE project_id=? AND output_p"
                    "ath IS NOT NULL ORDER BY id DESC LIMIT 1"
                ),
                (item["project_id"],),
            ).fetchone()
            item["url"] = service.media_url(exported[0]) if exported else None
            result["episodes"].append(item)
        return result


def list_series(archived=False):
    with service.database() as conn:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM studio_series WHERE archived=? ORDER BY updated_at DESC,id DESC",
                (int(archived),),
            )
        ]
    return [get_series(identifier) for identifier in ids]


def require_active_project(conn, project_id):
    row = conn.execute(
        "SELECT s.archived FROM studio_series s JOIN studio_episodes e ON e.series_id=s.id "
        "WHERE e.project_id=?",
        (project_id,),
    ).fetchone()
    if row and row[0]:
        raise HTTPException(409, "Restaurez la série archivée avant de lancer une production.")


def require_lifecycle_editable(conn, series_id):
    from calliope.agent.harness.runner import runner

    require_references_editable(conn, series_id)
    jobs = conn.execute(
        "SELECT j.status,p.status AS parent_status FROM jobs j "
        "JOIN studio_episodes e ON e.project_id=j.project_id "
        "LEFT JOIN jobs p ON p.id=json_extract(j.payload_json,'$.parent_job_id') "
        "WHERE e.series_id=? AND j.status IN ('pending','running')",
        (series_id,),
    ).fetchall()
    if any(
        job["status"] == "running" or job["parent_status"] not in ("done", "failed") for job in jobs
    ):
        raise HTTPException(
            409,
            "Mettez les productions en pause et annulez les tâches en attente "
            "avant de gérer cette série.",
        )
    takes = conn.execute(
        "SELECT t.* FROM studio_takes t JOIN studio_episodes e ON e.project_id=t.project_id "
        "WHERE e.series_id=?",
        (series_id,),
    ).fetchall()
    if any(
        service.modal_call_unfinished(take)
        or (take["submission_state"] == "submitting" and not take["modal_call_id"])
        for take in takes
    ):
        raise HTTPException(
            409,
            "Un appel vidéo reste actif ou sa soumission est ambiguë. "
            "Vérifiez-le avant de gérer cette série.",
        )
    sessions = conn.execute(
        "SELECT a.id FROM agent_sessions a JOIN studio_episodes e ON e.project_id=a.project_id "
        "WHERE e.series_id=?",
        (series_id,),
    ).fetchall()
    if any(runner.is_running(session[0]) for session in sessions):
        raise HTTPException(409, "Arrêtez la tâche d'écriture en cours avant de gérer cette série.")
    return [
        row[0]
        for row in conn.execute(
            "SELECT project_id FROM studio_episodes WHERE series_id=?", (series_id,)
        )
    ]


def delete_series(series_id):
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_series(conn, series_id)
        project_ids = require_lifecycle_editable(conn, series_id)
        conn.executemany(
            "DELETE FROM projects WHERE id=?", [(identifier,) for identifier in project_ids]
        )
        conn.execute("DELETE FROM studio_series WHERE id=?", (series_id,))
        conn.commit()
    return {"ok": True}


def create_series(data):
    with service.database() as conn:
        cur = conn.execute(
            (
                "INSERT INTO studio_series(title,concept,bible_json,aspect_ratio,language,t"
                "arget_seconds,directing_profile_json) VALUES(?,?,?,?,?,?,?)"
            ),
            (
                data["title"].strip(),
                data["concept"].strip(),
                json.dumps(data.get("bible") or {}),
                data.get("aspect_ratio", "9:16"),
                data.get("language", "en"),
                data.get("target_seconds", 65),
                json.dumps(directing_profile(data.get("directing_profile"))),
            ),
        )
        conn.commit()
        identifier = cur.lastrowid
    return get_series(identifier)


def update_series(series_id, data):
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_series(conn, series_id)
        if "archived" in data:
            archived = data.pop("archived")
            if archived != current["archived"]:
                if archived:
                    require_lifecycle_editable(conn, series_id)
                conn.execute(
                    "UPDATE studio_series SET archived=? WHERE id=?", (int(archived), series_id)
                )
        if "directing_profile" in data:
            patch = data.pop("directing_profile")
            base = {} if "preset" in patch else current["directing_profile"]
            profile = directing_profile(base | patch)
            conn.execute(
                "UPDATE studio_series SET directing_profile_json=? WHERE id=?",
                (json.dumps(profile), series_id),
            )
        if "bible" in data:
            bible = current["bible"] | data.pop("bible")
            if bible.get("visual_style") != current["bible"].get("visual_style"):
                require_references_editable(conn, series_id)
                for entity in entities(conn, series_id):
                    identity = entity["identity"] | {
                        "reference_approved": False,
                        "reference_sha256": "",
                    }
                    conn.execute(
                        "UPDATE studio_entities SET identity_json=? WHERE id=?",
                        (json.dumps(identity), entity["id"]),
                    )
            conn.execute(
                "UPDATE studio_series SET bible_json=? WHERE id=?", (json.dumps(bible), series_id)
            )
        for key, value in data.items():
            if key in ("title", "concept", "aspect_ratio", "language", "target_seconds"):
                conn.execute(f"UPDATE studio_series SET {key}=? WHERE id=?", (value, series_id))
        conn.execute(
            "UPDATE studio_series SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (series_id,)
        )
        conn.commit()
    return get_series(series_id)


def require_services():
    if settings.dry_run:
        raise HTTPException(
            422, "Désactivez la simulation dans Services pour produire un épisode réel."
        )
    missing = []
    for label, url, key in (
        ("texte", settings.llm_base_url, settings.llm_api_key),
        ("images", settings.studio_image_base_url, settings.studio_image_api_key),
    ):
        if urlsplit(url).hostname not in ("localhost", "127.0.0.1", "::1") and not key:
            missing.append(label)
    if missing:
        raise HTTPException(
            422,
            "Configurez le service "
            + " et ".join(missing)
            + " dans Services avant de créer l'épisode.",
        )
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise HTTPException(422, "L'assemblage est indisponible : FFmpeg est requis.")


def context(conn, series_id):
    result = require_series(conn, series_id)
    result["entities"] = entities(conn, series_id)
    result["speaker_ids"] = {
        entity["key"]: f"S{index + 1}"
        for index, entity in enumerate(e for e in result["entities"] if e["kind"] == "character")
    }
    result["history"] = []
    result["display_values"] = {}
    for row in conn.execute(
        "SELECT e.*,p.title FROM studio_episodes e JOIN projects p ON p.id=e.project_id "
        "WHERE e.series_id=? AND e.canon_revision IS NOT NULL ORDER BY e.number",
        (series_id,),
    ):
        exported = conn.execute(
            "SELECT snapshot_json FROM studio_exports WHERE project_id=? AND revision=? "
            "AND output_path IS NOT NULL ORDER BY id DESC LIMIT 1",
            (row["project_id"], row["canon_revision"]),
        ).fetchone()
        if exported:
            for clip in json.loads(exported[0]).get("clips", []):
                for label in clip.get("screen_text", []):
                    result["display_values"][label["key"]] = label["text"]
        shots = [
            dict(r)
            for r in conn.execute(
                (
                    "SELECT ss.prompt,ss.dialogue,c.description FROM clips c JOIN studio_sh"
                    "ots ss ON ss.clip_id=c.id JOIN scenes s ON s.id=c.scene_id WHERE c.pro"
                    "ject_id=? ORDER BY s.order_index,s.id,c.order_index,c.id"
                ),
                (row["project_id"],),
            )
        ]
        result["history"].append(
            {
                "number": row["number"],
                "title": row["title"],
                "summary": row["summary"],
                "events": json.loads(row["events_json"]),
                "ongoing_plot": row["ongoing_plot"],
                "shots": shots,
            }
        )
    return result


def create_episode(series_id, data):
    require_services()
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_series(conn, series_id)
        if current["archived"]:
            raise HTTPException(409, "Restaurez la série archivée avant de créer un épisode.")
        old = conn.execute(
            "SELECT * FROM studio_episodes WHERE series_id=? AND request_id=?",
            (series_id, data["request_id"]),
        ).fetchone()
        if old:
            if (
                old["idea"] != data["idea"].strip()
                or old["objective"] != data.get("objective", "").strip()
            ):
                raise HTTPException(409, "Cette demande correspond déjà à une autre idée.")
            production = conn.execute(
                "SELECT job_id FROM studio_productions WHERE project_id=?", (old["project_id"],)
            ).fetchone()
            return {"project_id": old["project_id"], "job_id": production[0]}
        previous = conn.execute(
            "SELECT e.canon_revision,sp.revision FROM studio_episodes e "
            "JOIN studio_projects sp ON sp.project_id=e.project_id "
            "WHERE e.series_id=? ORDER BY e.number DESC LIMIT 1",
            (series_id,),
        ).fetchone()
        if previous and (previous[0] is None or previous[0] != previous[1]):
            raise HTTPException(
                409, "Terminez ou réexportez l'épisode précédent avant de poursuivre sa continuité."
            )
        number = conn.execute(
            "SELECT COALESCE(MAX(number),0)+1 FROM studio_episodes WHERE series_id=?", (series_id,)
        ).fetchone()[0]
        frozen = context(conn, series_id)
        cur = conn.execute(
            "INSERT INTO projects(title,idea) VALUES(?,?)",
            (f"{current['title']} - Épisode {number}", data["idea"].strip()),
        )
        project_id = cur.lastrowid
        conn.execute(
            "INSERT INTO studio_projects(project_id,aspect_ratio) VALUES(?,?)",
            (project_id, current["aspect_ratio"]),
        )
        conn.execute(
            (
                "INSERT INTO studio_episodes(project_id,series_id,number,idea,objective,req"
                "uest_id,context_json) VALUES(?,?,?,?,?,?,?)"
            ),
            (
                project_id,
                series_id,
                number,
                data["idea"].strip(),
                data.get("objective", "").strip(),
                data["request_id"],
                json.dumps(frozen),
            ),
        )
        cur = conn.execute(
            "INSERT INTO jobs(project_id,kind,payload_json) VALUES(?,?,?)",
            (project_id, "production", json.dumps({"studio_production_id": project_id})),
        )
        job_id = cur.lastrowid
        conn.execute(
            "INSERT INTO studio_productions(project_id,job_id) VALUES(?,?)", (project_id, job_id)
        )
        conn.commit()
    return {"project_id": project_id, "job_id": job_id}


def require_editable(project_id):
    with service.database() as conn:
        row = conn.execute(
            "SELECT status FROM studio_productions WHERE project_id=?", (project_id,)
        ).fetchone()
        if row and row[0] in ("pending", "running"):
            raise HTTPException(409, "Mettez la production en pause avant de modifier l'épisode.")


def pause(project_id):
    with service.database() as conn:
        row = conn.execute(
            "SELECT * FROM studio_productions WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Production introuvable.")
        if row["status"] in ("pending", "running"):
            conn.execute(
                "UPDATE studio_productions SET pause_requested=1,message=? WHERE project_id=?",
                ("Pause demandée. La prise en cours sera conservée.", project_id),
            )
        conn.commit()
    return service.board(project_id)


def resume(project_id):
    require_services()
    with service.database() as conn:
        require_active_project(conn, project_id)
        row = conn.execute(
            "SELECT * FROM studio_productions WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Production introuvable.")
        if row["status"] in ("pending", "running"):
            return service.board(project_id)
        if row["status"] == "done":
            raise HTTPException(409, "Cette production est déjà terminée.")
        job = conn.execute("SELECT status,error FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
        if job["error"] == "cancelled":
            raise HTTPException(409, "Cette production a été annulée.")
        children = conn.execute(
            "SELECT t.* FROM studio_takes t JOIN jobs j ON j.id=t.job_id "
            "WHERE t.project_id=? AND j.status!='done' AND j.error IS NOT 'cancelled'",
            (project_id,),
        ).fetchall()
        if any(t["submission_state"] == "submitting" and not t["modal_call_id"] for t in children):
            raise HTTPException(
                409, "Soumission Modal ambiguë. Vérifiez le tableau Modal avant de reprendre."
            )
        if row["status"] == "paused" and job["status"] == "done":
            conn.execute(
                "UPDATE jobs SET status='pending',error=NULL,completed_at=NULL "
                "WHERE id=? AND status='done'",
                (row["job_id"],),
            )
        conn.execute(
            "UPDATE jobs SET status='pending' WHERE status='running' AND project_id=? "
            "AND (json_extract(payload_json,'$.studio_take_id') IS NOT NULL "
            "OR json_extract(payload_json,'$.studio_export_id') IS NOT NULL)",
            (project_id,),
        )
        conn.execute(
            (
                "UPDATE studio_productions SET status='pending',pause_requested=0,error=NUL"
                "L WHERE project_id=?"
            ),
            (project_id,),
        )
        conn.commit()
        job_id = row["job_id"]
    from calliope.queue.manager import queue_manager

    queue_manager.retry(job_id)
    for take in children:
        if take["modal_call_id"]:
            service.render_event(
                take["id"],
                "RESUMING",
                "Reprise explicite de la production",
                resume_started_at=service.utc_now(),
            )
    return service.board(project_id)


def episode_meta(conn, project_id):
    row = conn.execute(
        "SELECT e.series_id,e.number,e.summary,e.ongoing_plot,s.title AS series_title "
        "FROM studio_episodes e JOIN studio_series s ON s.id=e.series_id WHERE e.project_id=?",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None
