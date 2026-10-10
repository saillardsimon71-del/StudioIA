import asyncio
import json
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from calliope.studio import audio, series, service
from calliope.studio.h3_prompt import DirectingProfile

router = APIRouter()


class BiblePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe: str | None = Field(default=None, max_length=20000)
    tone: str | None = Field(default=None, max_length=5000)
    visual_style: str | None = Field(default=None, max_length=10000)
    characters: str | None = Field(default=None, max_length=20000)
    relationships: str | None = Field(default=None, max_length=20000)
    locations: str | None = Field(default=None, max_length=20000)
    world_rules: str | None = Field(default=None, max_length=20000)
    past_events: str | None = Field(default=None, max_length=20000)
    ongoing_plot: str | None = Field(default=None, max_length=20000)
    continuity: str | None = Field(default=None, max_length=20000)


class SeriesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    concept: str = Field(min_length=1, max_length=30000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "9:16"
    language: str = Field(default="en", min_length=1, max_length=40)
    target_seconds: int = Field(default=65, ge=10, le=120)
    bible: BiblePatch = Field(default_factory=BiblePatch)
    directing_profile: DirectingProfile = Field(default_factory=DirectingProfile)

    @field_validator("title", "concept", "language")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Ce champ ne peut pas être vide.")
        return value.strip()


class SeriesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    concept: str | None = Field(default=None, min_length=1, max_length=30000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] | None = None
    language: str | None = Field(default=None, min_length=1, max_length=40)
    target_seconds: int | None = Field(default=None, ge=10, le=120)
    bible: BiblePatch | None = None
    directing_profile: DirectingProfile | None = None
    archived: bool | None = None

    @field_validator("title", "concept", "language")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Ce champ ne peut pas être vide.")
        return value.strip() if value is not None else value


class EpisodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idea: str = Field(min_length=1, max_length=20000)
    objective: str = Field(default="", max_length=10000)
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("idea")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Saisissez une idée directrice.")
        return value.strip()


class EpisodeRestart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ScriptShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    description: str | None = Field(default=None, max_length=50000)
    dialogue: str | None = Field(default=None, max_length=50000)
    prompt: str | None = Field(default=None, max_length=50000)
    image_prompt: str | None = Field(default=None, max_length=50000)


class ScriptEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shots: list[ScriptShot] = Field(min_length=1, max_length=1000)


class EntityIdentityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visual_description: str | None = Field(default=None, max_length=10000)
    variant_of: str | None = Field(default=None, max_length=100)
    attached_value: int | None = None
    label_key: str | None = Field(default=None, max_length=80)


class EntityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: EntityIdentityPatch | None = None
    reference_approved: bool | None = None
    reset_reference: bool = False


class VisualReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    frame_index: int = Field(default=0, ge=0)
    target_shot_id: int | None = Field(default=None, ge=1)


@router.post("/takes/{take_id}/visual-review")
async def review_visuals(take_id: int, payload: VisualReview):
    with service.database() as conn:
        take = conn.execute("SELECT project_id FROM studio_takes WHERE id=?", (take_id,)).fetchone()
    if take:
        series.require_editable(take[0])
    return service.review_take_visuals(
        take_id, payload.approved, payload.frame_index, payload.target_shot_id
    )


@router.post("/projects/{project_id}/visual-contract")
async def prepare_visual_contract(project_id: int):
    return series.refresh_visual_contract(project_id)


@router.patch("/series/{series_id}/entities/{entity_id}")
async def save_entity(series_id: int, entity_id: int, payload: EntityPatch):
    return series.update_entity(series_id, entity_id, payload.model_dump(exclude_unset=True))


@router.patch("/projects/{project_id}/script")
async def save_script(project_id: int, payload: ScriptEdit):
    series.require_editable(project_id)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        service.require_project(conn, project_id)
        changed = False
        for patch in payload.shots:
            current = service.require_shot(conn, patch.id, project_id)
            data = patch.model_dump(exclude_unset=True, exclude_none=True)
            data.pop("id")
            extra = dict(
                conn.execute("SELECT * FROM studio_shots WHERE clip_id=?", (patch.id,)).fetchone()
            )
            different = any(
                (current if key == "description" else extra).get(key) != value
                for key, value in data.items()
            )
            if different:
                for key, value in data.items():
                    table, identifier = (
                        ("clips", "id") if key == "description" else ("studio_shots", "clip_id")
                    )
                    conn.execute(
                        f"UPDATE {table} SET {key}=? WHERE {identifier}=?", (value, patch.id)
                    )
                conn.execute("UPDATE studio_shots SET approved=0 WHERE clip_id=?", (patch.id,))
                changed = True
        if changed:
            service.touch(conn, project_id)
            lines = [
                r[0] + "\n" + r[1]
                for r in conn.execute(
                    (
                        "SELECT c.description,ss.dialogue FROM clips c JOIN studio_shots ss"
                        " ON ss.clip_id=c.id JOIN scenes s ON s.id=c.scene_id WHERE c.proje"
                        "ct_id=? ORDER BY s.order_index,s.id,c.order_index,c.id"
                    ),
                    (project_id,),
                )
            ]
            conn.execute(
                "UPDATE studio_projects SET script_source=? WHERE project_id=?",
                ("\n\n".join(lines), project_id),
            )
        conn.commit()
    return service.board(project_id)


@router.get("/series")
async def list_series(archived: bool = False):
    return {"series": series.list_series(archived)}


@router.post("/series")
async def create_series(payload: SeriesCreate):
    return series.create_series(payload.model_dump(exclude_none=True))


@router.get("/series/{series_id}")
async def get_series(series_id: int):
    return series.get_series(series_id)


@router.patch("/series/{series_id}")
async def update_series(series_id: int, payload: SeriesUpdate):
    return series.update_series(
        series_id, payload.model_dump(exclude_unset=True, exclude_none=True)
    )


@router.delete("/series/{series_id}")
async def delete_series(series_id: int):
    return series.delete_series(series_id)


@router.post("/series/{series_id}/episodes")
async def create_episode(series_id: int, payload: EpisodeCreate):
    return series.create_episode(series_id, payload.model_dump())


@router.post("/projects/{project_id}/production/pause")
async def pause(project_id: int):
    return series.pause(project_id)


@router.post("/projects/{project_id}/production/resume")
async def resume(project_id: int):
    return series.resume(project_id)


@router.post("/projects/{project_id}/production/restart")
async def restart(project_id: int, payload: EpisodeRestart):
    return series.restart(project_id, payload.request_id)


@router.get("/projects/{project_id}/audio")
async def get_audio(project_id: int):
    with service.database() as conn:
        service.require_project(conn, project_id)
        return audio.get_audio(conn, project_id)


@router.patch("/projects/{project_id}/audio")
async def save_audio(project_id: int, payload: audio.AudioSettings):
    series.require_editable(project_id)
    return audio.save_audio(project_id, payload.model_dump(exclude_unset=True))


@router.post("/projects/{project_id}/audio/upload")
async def upload_audio(
    project_id: int, file: UploadFile = File(...), kind: Literal["music", "sfx"] = Form("music")
):
    series.require_editable(project_id)
    content = await file.read(75 * 1024 * 1024 + 1)
    if len(content) > 75 * 1024 * 1024:
        raise HTTPException(413, "Piste audio supérieure à 75 Mo.")
    try:
        return await asyncio.to_thread(
            audio.import_audio, project_id, file.filename or "Audio", content, kind
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(422, service.safe_error(exc)) from None


@router.post("/projects/{project_id}/audio/preview")
async def preview_audio(project_id: int):
    return await asyncio.to_thread(audio.preview, project_id)


@router.post("/series/{series_id}/entities/{entity_id}/reference")
async def upload_reference(series_id: int, entity_id: int, file: UploadFile = File(...)):
    with service.database() as conn:
        series.require_references_editable(conn, series_id)
        entity = conn.execute(
            "SELECT id FROM studio_entities WHERE id=? AND series_id=?", (entity_id, series_id)
        ).fetchone()
    if not entity:
        raise HTTPException(404, "Référence introuvable dans cette série.")
    content = await file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "Image supérieure à 25 Mo.")
    try:
        normalized, _ = service.normalized_image(content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    import uuid

    from calliope.config import settings

    path = (
        settings.assets_dir / "series" / str(series_id) / "references" / (uuid.uuid4().hex + ".png")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(normalized)
    with service.database() as conn:
        series.require_references_editable(conn, series_id)
        entity = conn.execute(
            "SELECT identity_json FROM studio_entities WHERE id=? AND series_id=?",
            (entity_id, series_id),
        ).fetchone()
        identity = json.loads(entity[0])
        identity.update(reference_approved=False, reference_sha256="")
        conn.execute(
            "UPDATE studio_entities SET reference_path=?,identity_json=?,reference_request_json=? "
            "WHERE id=? AND series_id=?",
            (
                str(path),
                json.dumps(identity),
                json.dumps({"source": "manual_upload"}),
                entity_id,
                series_id,
            ),
        )
        key = conn.execute(
            "SELECT entity_key FROM studio_entities WHERE id=?", (entity_id,)
        ).fetchone()[0]
        series.invalidate_reference_dependents(conn, series_id, key)
        conn.commit()
    return series.get_series(series_id)
