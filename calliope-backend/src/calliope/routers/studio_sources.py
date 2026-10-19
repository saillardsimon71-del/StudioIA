import hashlib
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from calliope.config import settings
from calliope.studio import series, service, source_video

router = APIRouter()


class SourceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: str = Field(min_length=64, max_length=64)
    shots: list[dict] | None = Field(default=None, max_length=80)


class SourceCuts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_hash: str = Field(min_length=64, max_length=64)
    cuts: list[float] = Field(max_length=79)


@router.post("/series/{series_id}/video-sources")
async def upload_source(
    series_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    source_language: Literal["en", "fr"] = Form("en"),
):
    with service.database() as conn:
        current = series.require_series(conn, series_id)
        if current["archived"]:
            raise HTTPException(409, "Restaurez la série avant un nouvel import.")
    if not str(file.filename or "").lower().endswith(".mp4"):
        raise HTTPException(422, "Importez un fichier MP4.")
    source_id = uuid.uuid4().hex
    directory = settings.assets_dir / "studio-sources" / source_id
    directory.mkdir(parents=True)
    path = directory / "source.mp4"
    sha = hashlib.sha256()
    try:
        size = 0
        with path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > source_video.MAX_BYTES:
                    raise HTTPException(413, "Le MP4 dépasse 250 Mo.")
                destination.write(chunk)
                sha.update(chunk)
        await run_in_threadpool(source_video.probe, path)
        with service.database() as conn:
            conn.execute(
                "INSERT INTO studio_video_sources "
                "(id,series_id,filename,path,sha256,source_language,status,worker_token) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    series_id,
                    Path(file.filename.replace("\\", "/")).name,
                    str(path),
                    sha.hexdigest(),
                    source_language,
                    "analyzing",
                    source_video.WORKER_TOKEN,
                ),
            )
            conn.commit()
    except Exception as exc:
        path.unlink(missing_ok=True)
        directory.rmdir()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, "MP4 invalide ou analyse locale indisponible.") from None
    background.add_task(source_video.run_analysis, series_id, source_id)
    return source_video.get_source(series_id, source_id)


@router.get("/series/{series_id}/video-sources/{source_id}")
async def get_source(series_id: int, source_id: str):
    return source_video.get_source(series_id, source_id)


@router.patch("/series/{series_id}/video-sources/{source_id}")
async def edit_source(series_id: int, source_id: str, payload: SourceReview):
    if payload.shots is None:
        raise HTTPException(422, "La revue des plans est requise.")
    return source_video.review_source(series_id, source_id, payload.expected_hash, payload.shots)


@router.post("/series/{series_id}/video-sources/{source_id}/approve")
async def approve_source(series_id: int, source_id: str, payload: SourceReview):
    return source_video.review_source(series_id, source_id, payload.expected_hash, approve=True)


@router.post("/series/{series_id}/video-sources/{source_id}/retry")
async def retry_source(series_id: int, source_id: str, background: BackgroundTasks):
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = source_video.require_source(conn, series_id, source_id)
        if source_video.public_source(row)["status"] != "failed":
            raise HTTPException(409, "Cette analyse est déjà en cours ou terminée.")
        conn.execute(
            "UPDATE studio_video_sources SET status='analyzing',error=NULL,worker_token=? "
            "WHERE id=?",
            (source_video.WORKER_TOKEN, source_id),
        )
        conn.commit()
    background.add_task(source_video.run_analysis, series_id, source_id)
    return source_video.get_source(series_id, source_id)


@router.post("/series/{series_id}/video-sources/{source_id}/cuts")
async def recut_source(series_id: int, source_id: str, payload: SourceCuts):
    return await run_in_threadpool(
        source_video.recut_source, series_id, source_id, payload.expected_hash, payload.cuts
    )


@router.get("/projects/{project_id}/source-video")
async def project_source(project_id: int):
    with service.database() as conn:
        service.require_project(conn, project_id)
        row = conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        return {"source": json.loads(row[0]).get("source_video") if row else None}
