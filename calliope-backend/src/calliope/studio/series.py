import json
import shutil
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
        item["url"] = service.media_url(item.get("reference_path"))
        result.append(item)
    return result


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


def list_series():
    with service.database() as conn:
        ids = [
            r[0]
            for r in conn.execute("SELECT id FROM studio_series ORDER BY updated_at DESC,id DESC")
        ]
    return [get_series(identifier) for identifier in ids]


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
        current = require_series(conn, series_id)
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
    result["history"] = []
    for row in conn.execute(
        "SELECT e.*,p.title FROM studio_episodes e JOIN projects p ON p.id=e.project_id "
        "WHERE e.series_id=? AND e.canon_revision IS NOT NULL ORDER BY e.number",
        (series_id,),
    ):
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
        row = conn.execute(
            "SELECT * FROM studio_productions WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Production introuvable.")
        if row["status"] in ("pending", "running"):
            return service.board(project_id)
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
    return service.board(project_id)


def episode_meta(conn, project_id):
    row = conn.execute(
        "SELECT e.series_id,e.number,e.summary,e.ongoing_plot,s.title AS series_title "
        "FROM studio_episodes e JOIN studio_series s ON s.id=e.series_id WHERE e.project_id=?",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None
