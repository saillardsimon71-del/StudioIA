import asyncio
import base64
import hashlib
import io
import json
import secrets
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from PIL import Image, ImageOps

from calliope.config import settings
from calliope.db import ensure_default_clip, get_db
from calliope.events.bus import event_bus
from calliope.queue.manager import queue_manager


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


def public_job(row):
    job = dict(row)
    payload = json.loads(job.pop("payload_json", None) or "{}")
    job.pop("output_paths_json", None)
    job["take_id"] = payload.get("studio_take_id")
    if job["status"] == "failed" and job.get("error") == "cancelled":
        job["status"] = "cancelled"
    return job


def public_take(conn, row):
    take = dict(row)
    metadata = json.loads(take.pop("metadata_json") or "{}")
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
    clip["takes"] = [
        public_take(conn, r)
        for r in conn.execute(
            "SELECT * FROM studio_takes WHERE clip_id=? ORDER BY id DESC", (shot_id,)
        )
    ]
    return clip


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
                jobs.append(public_job(row))
        exports = []
        for row in conn.execute(
            "SELECT * FROM studio_exports WHERE project_id=? ORDER BY id DESC", (project_id,)
        ):
            export = dict(row)
            job = conn.execute(
                "SELECT status,error FROM jobs WHERE id=?", (row["job_id"],)
            ).fetchone()
            export.update(
                status=job["status"] if job else "done",
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
            "SELECT * FROM projects WHERE status!='system' ORDER BY updated_at DESC,id DESC"
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
        "INSERT INTO studio_shots(clip_id,prompt,image_prompt,dialogue,audio_mode) "
        "VALUES(?,?,?,?,?)",
        (
            cur.lastrowid,
            str(data.get("prompt") or description),
            str(data.get("image_prompt") or description),
            str(data.get("dialogue") or ""),
            data.get("audio_mode") or "native",
        ),
    )
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
        changed = any(current.get(k) != v for k, v in data.items())
        for key in ("description", "duration_sec", "order_index"):
            if key in data:
                conn.execute(f"UPDATE clips SET {key}=? WHERE id=?", (data[key], shot_id))
        for key in ("prompt", "image_prompt", "dialogue", "audio_mode", "continue_from_previous"):
            if key in data:
                conn.execute(
                    f"UPDATE studio_shots SET {key}=? WHERE clip_id=?", (data[key], shot_id)
                )
        if changed:
            conn.execute("UPDATE studio_shots SET approved=0 WHERE clip_id=?", (shot_id,))
            touch(conn, project_id)
        result = public_shot(conn, shot_id)
        conn.commit()
        return result


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
        result = public_shot(conn, row["clip_id"])
        conn.commit()
        return result


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
        "SELECT c.id FROM clips c JOIN scenes s ON s.id=c.scene_id WHERE c.project_id=? "
        "ORDER BY s.order_index,s.id,c.order_index,c.id",
        (clip["project_id"],),
    ).fetchall()
    previous = None
    for row in rows:
        if row["id"] == shot_id:
            break
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
        project = require_project(conn, shot["project_id"])
        aspect = conn.execute(
            "SELECT aspect_ratio FROM studio_projects WHERE project_id=?", (project["id"],)
        ).fetchone()[0]
        kind = data["kind"]
        from calliope.studio.production import image_references
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
        if kind == "video" and shot["dialogue"].strip() and shot["audio_mode"] == "native":
            prompt += "\n<d>" + shot["dialogue"].strip() + "</d>"
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
        references = image_references(conn, shot_id) if kind == "image" else []
        if references:
            parameters["reference_paths"] = references
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
                raise HTTPException(
                    409, "Cet identifiant de génération correspond déjà à une autre demande."
                )
            return {
                "takes": [public_take(conn, t) for t in existing],
                "jobs": [
                    public_job(
                        conn.execute("SELECT * FROM jobs WHERE id=?", (t["job_id"],)).fetchone()
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
                    else settings.studio_image_model,
                    source["id"] if source else None,
                    data["request_id"],
                    index,
                    fingerprint,
                    json.dumps(request),
                ),
            )
            take_id = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO jobs(project_id,scene_id,clip_id,kind,payload_json) VALUES(?,?,?,?,?)",
                (
                    project["id"],
                    shot["scene_id"],
                    shot_id,
                    kind,
                    json.dumps({"studio_take_id": take_id}),
                ),
            )
            job_id = cur.lastrowid
            conn.execute("UPDATE studio_takes SET job_id=? WHERE id=?", (job_id, take_id))
            takes.append(
                public_take(
                    conn,
                    conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone(),
                )
            )
            jobs.append(
                public_job(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            )
        conn.commit()
        return {"takes": takes, "jobs": jobs}


def enqueue_export(project_id, request_id):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_project(conn, project_id)
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
        for row in conn.execute(
            "SELECT c.id,s.heading,c.order_index FROM clips c JOIN scenes s ON s.id=c.scene_id "
            "WHERE c.project_id=? ORDER BY s.order_index,s.id,c.order_index,c.id",
            (project_id,),
        ):
            shot = public_shot(conn, row["id"])
            take = conn.execute(
                "SELECT * FROM studio_takes WHERE id=? AND clip_id=?",
                (shot["selected_video_id"], row["id"]),
            ).fetchone()
            if not shot["approved"] or not take or not Path(take["output_path"] or "").is_file():
                missing.append(f"{row['heading']} / plan {row['order_index']}")
                continue
            effective = json.loads(take["metadata_json"] or "{}")
            clips.append(
                {
                    "clip_id": row["id"],
                    "take_id": take["id"],
                    "path": take["output_path"],
                    "duration_sec": effective.get("duration_sec") or shot["duration_sec"],
                    "audio_mode": shot["audio_mode"],
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


async def _generate_image(request):
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
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
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
            response = await client.post(
                request["image_base_url"].rstrip("/") + "/images/generations",
                headers=headers, json=payload,
            )
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
        conn.execute("UPDATE studio_takes SET status='running',error=NULL WHERE id=?", (take_id,))
        conn.commit()
    directory = settings.assets_dir / str(take["project_id"]) / "studio" / "takes" / str(take_id)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        if take["kind"] == "image":
            content, metadata = await _generate_image(request)
            path = directory / "image.png"
            if queue_manager.is_cancelled(job["id"]):
                raise StudioCancelledError()
            path.write_bytes(content)
        else:
            from calliope.engines.modal_h3 import ModalH3Engine, build_request
            from calliope.studio.export import inspect_media

            engine = ModalH3Engine(request["modal_app"], request["modal_function"])
            call_id = take["modal_call_id"]
            if not call_id:
                if queue_manager.is_cancelled(job["id"]):
                    raise StudioCancelledError()
                if take["submission_state"] == "submitting":
                    raise ValueError(
                        "Soumission Modal interrompue avant sa confirmation. "
                        "Vérifiez les appels Modal avant de relancer ce plan."
                    )
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
                )
                if should_stop():
                    raise asyncio.CancelledError()
                if queue_manager.is_cancelled(job["id"]):
                    raise StudioCancelledError()
                with database() as conn:
                    conn.execute(
                        "UPDATE studio_takes SET submission_state='submitting' WHERE id=?",
                        (take_id,),
                    )
                    conn.commit()
                call_id = await asyncio.to_thread(engine.spawn, prepared)
                with database() as conn:
                    conn.execute(
                        "UPDATE studio_takes SET modal_call_id=?,"
                        "submission_state='submitted' WHERE id=?",
                        (call_id, take_id),
                    )
                    conn.commit()
            while True:
                if should_stop():
                    raise asyncio.CancelledError()
                if queue_manager.is_cancelled(job["id"]):
                    await asyncio.to_thread(engine.cancel, call_id)
                    raise StudioCancelledError()
                result = await asyncio.to_thread(engine.poll, call_id)
                if result is not None:
                    break
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
            content = base64.b64decode(result["video_base64"], validate=True)
            path = directory / "video.mp4"
            temporary = directory / "download.mp4"
            temporary.write_bytes(content)
            try:
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
        return [str(path)]
    except StudioCancelledError:
        with database() as conn:
            conn.execute(
                "UPDATE studio_takes SET status='cancelled',error='cancelled' WHERE id=?",
                (take_id,),
            )
            conn.commit()
        raise
    except Exception as exc:
        with database() as conn:
            conn.execute(
                "UPDATE studio_takes SET status='failed',error=? WHERE id=?",
                (safe_error(exc), take_id),
            )
            conn.commit()
        raise RuntimeError(safe_error(exc)) from None


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
