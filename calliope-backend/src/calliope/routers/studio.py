import asyncio
import json
import shutil
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from calliope.config import settings
from calliope.queue.manager import queue_manager
from calliope.routers.studio_series import router as series_router
from calliope.studio import series, service
from calliope.studio.planner import parse_script, plan_with_llm

router = APIRouter()
router.include_router(series_router)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    idea: str = Field(default="", max_length=200000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"


class PlanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    mode: Literal["script", "idea"] = "script"
    use_llm: bool = False


class SceneData(BaseModel):
    heading: str = Field(default="", max_length=1000)
    action: str = Field(default="", max_length=100000)
    dialog: str = Field(default="", max_length=100000)


class SceneUpdate(BaseModel):
    heading: str | None = Field(default=None, max_length=1000)
    action: str | None = Field(default=None, max_length=100000)
    dialog: str | None = Field(default=None, max_length=100000)


class ShotCreate(BaseModel):
    description: str = Field(default="", max_length=50000)
    prompt: str = Field(default="", max_length=50000)
    image_prompt: str = Field(default="", max_length=50000)
    dialogue: str = Field(default="", max_length=50000)
    duration_sec: float = Field(default=5, ge=1, le=15)
    trim_start_sec: float | None = Field(default=None, ge=0, le=900)
    trim_end_sec: float | None = Field(default=None, ge=0, le=900)
    audio_mode: Literal["native", "silent"] = "native"


class ShotUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=50000)
    prompt: str | None = Field(default=None, max_length=50000)
    image_prompt: str | None = Field(default=None, max_length=50000)
    dialogue: str | None = Field(default=None, max_length=50000)
    duration_sec: float | None = Field(default=None, ge=1, le=15)
    trim_start_sec: float | None = Field(default=None, ge=0, le=900)
    trim_end_sec: float | None = Field(default=None, ge=0, le=900)
    audio_mode: Literal["native", "silent"] | None = None
    continue_from_previous: bool | None = None
    order_index: int | None = Field(default=None, ge=1)


class GenerateRequest(BaseModel):
    kind: Literal["image", "video"] = "video"
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    n_takes: int = Field(default=1, ge=1, le=4)
    seed: int | None = Field(default=None, ge=0, lt=2**53)


class ApproveRequest(BaseModel):
    approved: bool = True


class ExportRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ReorderRequest(BaseModel):
    scene_ids: list[int] | None = None
    scene_id: int | None = None
    shot_ids: list[int] | None = None


class ConfigUpdate(BaseModel):
    use_agnes_for_text: bool = False
    modal_app: str | None = Field(default=None, min_length=1, max_length=200)
    modal_function: str | None = Field(default=None, min_length=1, max_length=200)
    llm_base_url: str | None = None
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_api_key: str | None = None
    image_base_url: str | None = None
    image_model: str | None = Field(default=None, min_length=1, max_length=200)
    image_api_key: str | None = None


def _validate_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(
            422, "Utilisez une URL HTTP(S) sans identifiant ni mot de passe dans son adresse."
        )


@router.get("/config")
async def get_config():
    return {
        "modal_app": settings.studio_modal_app,
        "modal_function": settings.studio_modal_function,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_configured": bool(settings.llm_api_key),
        "image_base_url": settings.studio_image_base_url,
        "image_model": settings.studio_image_model,
        "image_configured": bool(settings.studio_image_api_key),
        "dry_run": settings.dry_run,
    }


@router.patch("/config")
async def update_config(payload: ConfigUpdate):
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    use_agnes = data.pop("use_agnes_for_text", False)
    if use_agnes:
        image_key = data.get("image_api_key") or settings.studio_image_api_key
        if not image_key:
            raise HTTPException(422, "Enregistrez d'abord votre clé Agnes pour les images.")
        data.update(llm_base_url=data.get("image_base_url") or settings.studio_image_base_url,
                    llm_model="agnes-2.5-flash", llm_api_key=image_key)
    mapping = {
        "modal_app": "studio_modal_app",
        "modal_function": "studio_modal_function",
        "image_base_url": "studio_image_base_url",
        "image_model": "studio_image_model",
        "image_api_key": "studio_image_api_key",
    }
    for key in ("llm_base_url", "image_base_url"):
        if key in data:
            _validate_url(data[key])
    for key, value in data.items():
        setattr(settings, mapping.get(key, key), value.strip())
    if any(key.startswith("llm_") for key in data):
        settings.sync_legacy_llm_into_active()
    settings.save_config_file()
    return await get_config()


async def _endpoint_check(base_url, api_key, label):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(base_url.rstrip("/") + "/models", headers=headers)
        if response.status_code >= 400:
            return {
                "ok": False,
                "message": f"{label}: HTTP {response.status_code}. Vérifiez les réglages.",
            }
        return {"ok": True, "message": f"{label} accessible."}
    except Exception:
        return {
            "ok": False,
            "message": f"{label} inaccessible. Vérifiez son adresse ou démarrez le service.",
        }


@router.get("/preflight")
async def preflight():
    from calliope.engines.modal_h3 import ModalH3Engine

    engine = ModalH3Engine(settings.studio_modal_app, settings.studio_modal_function)
    modal, llm, image = await asyncio.gather(
        asyncio.to_thread(engine.check_available),
        _endpoint_check(settings.llm_base_url, settings.llm_api_key, "Service texte"),
        _endpoint_check(
            settings.studio_image_base_url, settings.studio_image_api_key, "Service images"
        ),
    )
    binaries = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    return {
        "modal": modal,
        "llm": llm,
        "image": image,
        "engine": "FastH3 8-Step V2 / MiniMax H3",
        "ffmpeg": {
            "ok": binaries,
            "message": "Assemblage disponible." if binaries else "Installez FFmpeg et ffprobe.",
        },
    }


@router.get("/projects")
async def get_projects():
    return {"projects": service.list_projects()}


@router.post("/projects")
async def new_project(payload: ProjectCreate):
    data = payload.model_dump()
    data["title"] = data["title"].strip()
    if not data["title"]:
        raise HTTPException(422, "Donnez un titre au projet.")
    return service.create_project(data)


@router.get("/projects/{project_id}")
async def get_board(project_id: int):
    return service.board(project_id)


@router.post("/projects/{project_id}/plan")
async def prepare(project_id: int, payload: PlanRequest):
    series.require_editable(project_id)
    existing = service.board(project_id)
    if existing["scenes"]:
        raise HTTPException(
            409, "Ce projet contient déjà des plans. Créez un projet pour un nouveau découpage."
        )
    if not payload.text.strip():
        raise HTTPException(422, "Saisissez une idée ou un script.")
    try:
        if payload.use_llm:
            scenes = await plan_with_llm(payload.text, payload.mode)
        else:
            scenes = parse_script(payload.text)
    except Exception as exc:
        raise HTTPException(422, "Préparation interrompue: " + service.safe_error(exc)) from None
    return service.save_plan(project_id, payload.text, scenes)


@router.post("/projects/{project_id}/scenes")
async def new_scene(project_id: int, payload: SceneData):
    series.require_editable(project_id)
    with service.database() as conn:
        scene_id = service.add_scene(conn, project_id, payload.model_dump())
        conn.commit()
    return next(s for s in service.board(project_id)["scenes"] if s["id"] == scene_id)


@router.patch("/projects/{project_id}/scenes/{scene_id}")
async def edit_scene(project_id: int, scene_id: int, payload: SceneUpdate):
    series.require_editable(project_id)
    with service.database() as conn:
        service.require_project(conn, project_id)
        if not conn.execute(
            "SELECT id FROM scenes WHERE id=? AND project_id=?", (scene_id, project_id)
        ).fetchone():
            raise HTTPException(404, "Scène introuvable.")
        data = payload.model_dump(exclude_unset=True, exclude_none=True)
        for key, value in data.items():
            conn.execute(f"UPDATE scenes SET {key}=? WHERE id=?", (value, scene_id))
        if data:
            service.touch(conn, project_id)
        conn.commit()
    return next(s for s in service.board(project_id)["scenes"] if s["id"] == scene_id)


@router.post("/projects/{project_id}/scenes/{scene_id}/shots")
async def new_shot(project_id: int, scene_id: int, payload: ShotCreate):
    series.require_editable(project_id)
    with service.database() as conn:
        service.require_project(conn, project_id)
        if not conn.execute(
            "SELECT id FROM scenes WHERE id=? AND project_id=?", (scene_id, project_id)
        ).fetchone():
            raise HTTPException(404, "Scène introuvable.")
        shot_id = service.add_shot(conn, project_id, scene_id, payload.model_dump())
        service.touch(conn, project_id)
        conn.commit()
        return service.public_shot(conn, shot_id)


@router.patch("/projects/{project_id}/shots/{shot_id}")
async def edit_shot(project_id: int, shot_id: int, payload: ShotUpdate):
    series.require_editable(project_id)
    data = payload.model_dump(exclude_unset=True)
    # Seuls les points utiles peuvent être explicitement effacés par null.
    data = {
        key: value
        for key, value in data.items()
        if value is not None or key in ("trim_start_sec", "trim_end_sec")
    }
    return service.update_shot(project_id, shot_id, data)


@router.delete("/projects/{project_id}/shots/{shot_id}")
async def delete_shot(project_id: int, shot_id: int):
    series.require_editable(project_id)
    with service.database() as conn:
        shot = service.require_shot(conn, shot_id, project_id)
        if (
            conn.execute(
                "SELECT count(*) FROM clips WHERE scene_id=?", (shot["scene_id"],)
            ).fetchone()[0]
            <= 1
        ):
            raise HTTPException(409, "Chaque scène conserve au moins un plan.")
        if conn.execute(
            "SELECT 1 FROM jobs WHERE clip_id=? AND status IN ('pending','running')", (shot_id,)
        ).fetchone():
            raise HTTPException(409, "Annulez le rendu actif avant de supprimer ce plan.")
        conn.execute("DELETE FROM clips WHERE id=?", (shot_id,))
        service.touch(conn, project_id)
        conn.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/reorder")
async def reorder(project_id: int, payload: ReorderRequest):
    series.require_editable(project_id)
    with service.database() as conn:
        service.require_project(conn, project_id)
        if payload.scene_ids is not None:
            actual = {
                r["id"]
                for r in conn.execute("SELECT id FROM scenes WHERE project_id=?", (project_id,))
            }
            requested = payload.scene_ids
            table = "scenes"
        elif payload.shot_ids is not None and payload.scene_id is not None:
            actual = {
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM clips WHERE scene_id=? AND project_id=?",
                    (payload.scene_id, project_id),
                )
            }
            requested = payload.shot_ids
            table = "clips"
        else:
            raise HTTPException(422, "Indiquez l'ordre des scènes ou des plans d'une scène.")
        if len(requested) != len(set(requested)) or set(requested) != actual:
            raise HTTPException(
                422, "La liste doit contenir exactement les éléments de ce projet, une seule fois."
            )
        for index, identifier in enumerate(requested, 1):
            conn.execute(f"UPDATE {table} SET order_index=? WHERE id=?", (index, identifier))
        service.touch(conn, project_id)
        conn.commit()
    return service.board(project_id)


@router.post("/shots/{shot_id}/generate")
async def generate(shot_id: int, payload: GenerateRequest):
    with service.database() as conn:
        shot = service.require_shot(conn, shot_id)
    series.require_editable(shot["project_id"])
    return service.enqueue_takes(shot_id, payload.model_dump())


@router.post("/shots/{shot_id}/upload")
async def import_take(
    shot_id: int, file: UploadFile = File(...), kind: Literal["image", "video"] = Form("image")
):
    with service.database() as conn:
        shot = service.require_shot(conn, shot_id)
    series.require_editable(shot["project_id"])
    maximum = 25 * 1024 * 1024 if kind == "image" else 300 * 1024 * 1024
    content = await file.read(maximum + 1)
    if len(content) > maximum:
        raise HTTPException(413, "Fichier trop volumineux (image 25 Mo, vidéo 300 Mo maximum).")
    try:
        return await asyncio.to_thread(service.store_upload, shot_id, content, kind)
    except ValueError as exc:
        raise HTTPException(422, service.safe_error(exc)) from None


@router.post("/takes/{take_id}/select")
async def choose(take_id: int):
    with service.database() as conn:
        take = conn.execute("SELECT project_id FROM studio_takes WHERE id=?", (take_id,)).fetchone()
    if take:
        series.require_editable(take[0])
    return service.select_take(take_id)


@router.post("/shots/{shot_id}/approve")
async def approve(shot_id: int, payload: ApproveRequest):
    with service.database() as conn:
        shot = service.require_shot(conn, shot_id)
    series.require_editable(shot["project_id"])
    return service.approve_shot(shot_id, payload.approved)


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: int):
    job = queue_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Travail introuvable.")
    payload = json.loads(job["payload_json"] or "{}")
    if not (payload.get("studio_take_id") or payload.get("studio_export_id")):
        raise HTTPException(404, "Travail Studio introuvable.")
    queue_manager.cancel(job_id)
    if payload.get("studio_take_id") and queue_manager.get_job(job_id)["error"] == "cancelled":
        with service.database() as conn:
            take = conn.execute(
                "SELECT * FROM studio_takes WHERE id=?", (payload["studio_take_id"],)
            ).fetchone()
        if take and take["modal_call_id"]:
            from calliope.engines.modal_h3 import ModalH3Engine

            request = json.loads(take["request_json"])
            engine = ModalH3Engine(request["modal_app"], request["modal_function"])
            try:
                await asyncio.to_thread(engine.cancel, take["modal_call_id"])
            except Exception as exc:
                raise HTTPException(
                    503, "Annulé localement; arrêt Modal non confirmé: " + service.safe_error(exc)
                ) from None
    return service.public_job(queue_manager.get_job(job_id))


@router.post("/jobs/{job_id}/resume")
async def resume(job_id: int):
    job = queue_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Travail introuvable.")
    payload = json.loads(job["payload_json"] or "{}")
    if job["status"] != "failed" or job["error"] == "cancelled":
        raise HTTPException(
            409, "Reprenez un travail échoué. Une prise annulée doit être générée à nouveau."
        )
    if not (payload.get("studio_take_id") or payload.get("studio_export_id")):
        raise HTTPException(404, "Travail Studio introuvable.")
    series.require_editable(job["project_id"])
    return service.public_job(queue_manager.retry(job_id))


@router.post("/projects/{project_id}/export")
async def export(project_id: int, payload: ExportRequest):
    series.require_editable(project_id)
    return service.enqueue_export(project_id, payload.request_id)


@router.post("/examples")
async def create_example():
    project = service.create_project(
        {
            "title": "La lumière du matin",
            "idea": "Deux plans de nature, sans dialogue.",
            "aspect_ratio": "16:9",
        }
    )
    scenes = [
        {
            "heading": "EXT. LAC - AUBE",
            "action": "La lumière traverse la brume sur un lac.",
            "shots": [
                {
                    "description": "Brume au-dessus du lac",
                    "prompt": "A cinematic wide shot of a calm alpine lake at dawn. "
                    "Soft golden light passes through delicate mist above the water. "
                    "Pine trees reflect on the still surface. The camera slowly glides forward. "
                    "Photorealistic natural landscape, no people, no text. "
                    "Sound of gentle water and distant birds, no music and no speech.",
                    "image_prompt": "Photorealistic calm alpine lake at sunrise, soft golden "
                    "mist, pine forest, no people or text.",
                    "duration_sec": 4.2,
                    "audio_mode": "native",
                }
            ],
        },
        {
            "heading": "EXT. FORÊT - MATIN",
            "action": "La lumière éclaire une petite cascade.",
            "shots": [
                {
                    "description": "Cascade dans la forêt",
                    "prompt": "A cinematic close shot of a small waterfall over moss-covered "
                    "rocks in a pine forest. Warm morning sunlight, clear flowing water. "
                    "The camera gently pans right. Photorealistic peaceful landscape, "
                    "no people, no text. Sound of flowing water and birds, no speech or music.",
                    "image_prompt": "A photorealistic small waterfall on mossy rocks "
                    "in a sunlit pine forest, no people or text.",
                    "duration_sec": 3.4,
                    "audio_mode": "native",
                }
            ],
        },
    ]
    return service.save_plan(
        project["id"], "Deux plans de nature sans dialogue, avec leurs sons ambiants.", scenes
    )
