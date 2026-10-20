import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import Field

from calliope.studio import director, series, service
from calliope.studio.director_contracts import Budget, ShotContract, StrictModel

router = APIRouter()


class Review(StrictModel):
    expected_hash: str = Field(min_length=1, max_length=64)


class ImageReview(Review):
    approved: bool


class Revision(Review):
    shot_ids: list[int] = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=12000)


class ExpertShot(StrictModel):
    id: int = Field(ge=1)
    contract: ShotContract


class ExpertEdit(Review):
    shots: list[ExpertShot] = Field(min_length=1, max_length=100)


class TakeReview(Review):
    approved: bool
    visual_ok: bool = False
    continuity_ok: bool = False
    speakers_ok: bool = False
    audio_ok: bool = False
    # Motif écrit d'une revue supervisée écartant un signalement OCR de texte
    # parasite (faux positif vérifié image par image). 12 caractères minimum.
    text_override_reason: str = Field(default="", max_length=600)


@router.get("/projects/{project_id}/director")
async def get_director(project_id: int):
    return director.state(project_id)


@router.post("/projects/{project_id}/director/prepare")
async def prepare(project_id: int):
    try:
        return await director.prepare(project_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, service.safe_error(exc)) from None


@router.post("/projects/{project_id}/director/approve")
async def approve(project_id: int, payload: Review):
    return director.approve(project_id, payload.expected_hash)


@router.post("/projects/{project_id}/director/revise")
async def revise(project_id: int, payload: Revision):
    try:
        return await director.revise(
            project_id, payload.expected_hash, payload.shot_ids, payload.instruction
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, service.safe_error(exc)) from None


@router.patch("/projects/{project_id}/director/contracts")
async def expert_edit(project_id: int, payload: ExpertEdit):
    return await director.revise(
        project_id,
        payload.expected_hash,
        [s.id for s in payload.shots],
        "",
        contracts=[s.model_dump() for s in payload.shots],
    )


@router.get("/projects/{project_id}/director/package")
async def package(project_id: int):
    content = await asyncio.to_thread(director.package, project_id)
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="chatgpt-episode-{project_id}.zip"'},
    )


@router.post("/projects/{project_id}/director/images")
async def images(
    project_id: int,
    expected_hash: str = Form(...),
    files: list[UploadFile] = File(...),
    shot_ids: str | None = Form(None),
):
    if len(files) > 100:
        raise HTTPException(413, "100 images maximum par lot.")
    ids = None
    if shot_ids is not None:
        try:
            ids = json.loads(shot_ids)
        except ValueError:
            raise HTTPException(422, "shot_ids doit etre une liste JSON.") from None
        if not isinstance(ids, list) or len(ids) != len(files):
            raise HTTPException(422, "Indiquez un plan par image.")
        if any(v is not None and (type(v) is not int or v <= 0) for v in ids):
            raise HTTPException(422, "Chaque plan doit etre un identifiant entier positif.")
    uploads, total = [], 0
    for file in files:
        content = await file.read(25 * 1024 * 1024 + 1)
        total += len(content)
        if len(content) > 25 * 1024 * 1024 or total > 100 * 1024 * 1024:
            raise HTTPException(413, "Image 25 Mo et lot 100 Mo maximum.")
        uploads.append((file.filename or "", content))
    return await asyncio.to_thread(
        director.import_images, project_id, expected_hash, uploads, ids
    )


@router.get("/projects/{project_id}/director/shots/{shot_id}/image-prompt")
async def image_prompt(project_id: int, shot_id: int):
    return await asyncio.to_thread(director.image_prompt, project_id, shot_id)


@router.post("/projects/{project_id}/director/images/{take_id}/review")
async def review_image(project_id: int, take_id: int, payload: ImageReview):
    return director.review_image(project_id, take_id, payload.expected_hash, payload.approved)


@router.get("/projects/{project_id}/director/preflight")
async def preflight(project_id: int):
    return await asyncio.to_thread(director.preflight, project_id)


@router.patch("/projects/{project_id}/director/budget")
async def budget(project_id: int, payload: Budget):
    series.require_editable(project_id)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not director.enabled(conn, project_id):
            raise HTTPException(404, "Director introuvable.")
        conn.execute(
            "UPDATE studio_director_projects SET budget_json=? WHERE project_id=?",
            (payload.model_dump_json(), project_id),
        )
        if conn.execute(
            "SELECT 1 FROM studio_director_reservations WHERE project_id=?", (project_id,)
        ).fetchone():
            director.guard_budget(conn, project_id)
        conn.commit()
    return director.state(project_id)


@router.post("/projects/{project_id}/director/takes/{take_id}/verify")
async def verify(project_id: int, take_id: int):
    series.require_editable(project_id)
    return await director.verify_take(project_id, take_id)


@router.post("/projects/{project_id}/director/takes/{take_id}/review")
async def review_take(project_id: int, take_id: int, payload: TakeReview):
    return director.review_take(project_id, take_id, payload.model_dump())
