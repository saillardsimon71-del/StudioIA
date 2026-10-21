"""Application FastAPI minimale de Studio IA V2 (port 8270 par défaut).

Couvre les 5 étapes sans couche intermédiaire superflue :
1. IDEA      — Création / sélection de projet + saisie de l'idée
2. SCENARIO  — Appel unique DeepSeek OU édition directe des plans/dialogues/états/compteurs
3. ASSETS    — Bibliothèque persistante (personnages/décors), import glisser-déposer, validation humaine
4. RENDER    — Compilation/validation prompt LTX (<= 150 mots), estimation coût Modal + incertitudes,
               autorisation humaine explicite prise par prise, relance d'un seul plan sans perte
5. EXPORT    — Assemblage MP4 vertical 1080x1920 24fps AAC 48kHz stéréo + chiffres fixes + QC OCR/Whisper
"""

from __future__ import annotations

import mimetypes
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from core.config import STUDIO_V2_ROOT, V2Settings, load_settings, save_settings
from core.export import export_project_mp4
from core.ltx_prompt import (
    MAX_LTX_PROMPT_WORDS,
    PromptCapacityError,
    compile_shot_prompt,
    validate_ltx_prompt,
)
from core.scenario import Character, DialogueLine, Project, Shot
from core.storage import StudioRepository
from engines.video import (
    AuthorizationRequiredError,
    BudgetExceededError,
    LtxModalEngine,
    RenderExecutionError,
    RenderPreparationError,
)
from llm.deepseek import ScenarioGenerationError, generate_scenario_once
from qc.media import ffmpeg_exe, ffprobe_exe, inspect_media
from qc.runner import run_take_qc
from social.x_automation import enqueue_validated_episodes


class CreateProjectRequest(BaseModel):
    title: str = ""
    idea: str = Field(min_length=1, max_length=4000)
    target_duration_s: float = Field(default=15.0, gt=1.0, le=120.0)


class GenerateScenarioRequest(BaseModel):
    idea: str | None = None
    target_duration_s: float | None = Field(default=None, gt=1.0, le=120.0)


class AssignAssetRequest(BaseModel):
    asset_id: str


class ValidateImageRequest(BaseModel):
    validated: bool = True


class GenerateImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    kind: str = "character"
    name: str = "Image Meta AI"
    description: str = ""


class RenderShotRequest(BaseModel):
    authorized_by_human: bool = False
    max_budget_eur: float | None = Field(default=None, gt=0, le=100.0)


class ReviewTakeRequest(BaseModel):
    take_id: str | None = None
    human_approved: bool = True
    human_notes: str = ""
    actual_cost_eur: float | None = None


def build_phase3_template_project(project_id: str | None = None) -> Project:
    """Modèle prêt à l'emploi pour la Phase 3 : 1 fruit anthropomorphe, 1 prise de 5s."""
    pid = project_id or "phase3-fruit-5s"
    project = Project(
        id=pid,
        title="Phase 3 — Première prise réelle (Fruit 5s)",
        idea=(
            "Un citron anthropomorphe grognon en imperméable froissé inspecte un comptoir "
            "de cuisine nocturne sous un néon chaud et lâche une réplique française pince-sans-rire."
        ),
        aspect_ratio="9:16",
        target_duration_s=5.0,
        characters=[
            Character(
                id="inspecteur_zeste",
                name="Inspecteur Zeste",
                description=(
                    "Anthropomorphic yellow lemon character with textured citrus skin, expressive "
                    "dark eyes, tiny rumpled beige trench coat, standing on a wooden kitchen counter"
                ),
                voice_hint="slightly raspy medium-low",
            )
        ],
        shots=[
            Shot(
                id="shot_1",
                duration_s=5.0,
                action=(
                    "Medium close-up in warm moody kitchen neon light. Inspecteur Zeste leans forward "
                    "behind a wooden counter, squints suspiciously at the camera and raises one eyebrow."
                ),
                initial_state="Inspecteur Zeste stands still behind the counter, arms crossed, suspicious gaze.",
                final_state="Inspecteur Zeste leans closer to the lens with one eyebrow raised in disbelief.",
                continue_from_previous=False,
                dialogue=[
                    DialogueLine(
                        speaker_id="inspecteur_zeste",
                        text="Cinq secondes pour prouver que j'existe ? T'as intérêt à bien cadrer.",
                        start_sec=0.3,
                    )
                ],
                seed=20261020,
            )
        ],
    )
    project.shots[0].ltx_prompt = compile_shot_prompt(project, project.shots[0])
    return project


def create_app(
    *,
    settings: V2Settings | None = None,
    llm_transport: Callable[[dict], dict] | None = None,
    modal_runner: Callable[[dict], dict] | None = None,
) -> FastAPI:
    cfg = settings or load_settings()
    cfg.ensure_dirs()
    repo = StudioRepository(cfg.db_path)

    app = FastAPI(
        title="Studio IA V2",
        version="2.0.0",
        description="Pipeline minimal isolé : IDEA → SCENARIO → ASSETS → RENDER → EXPORT",
    )
    app.state.settings = cfg
    app.state.repo = repo
    app.state.llm_transport = llm_transport
    app.state.modal_runner = modal_runner

    def _queue_social() -> dict:
        try:
            return {"queued": enqueue_validated_episodes(app.state.repo, app.state.settings.data_dir)}
        except (OSError, ValueError, RuntimeError) as exc:
            return {"queued": 0, "warning": f"File sociale à vérifier : {type(exc).__name__}"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _engine() -> LtxModalEngine:
        return LtxModalEngine(
            app.state.settings,
            repo=app.state.repo,
            modal_runner=app.state.modal_runner,
        )

    def _safe_media_path(raw_path: str) -> Path:
        candidate = Path(raw_path).resolve()
        allowed_root = Path(app.state.settings.data_dir).resolve()
        if not candidate.is_relative_to(allowed_root):
            raise HTTPException(403, "Accès refusé : fichier hors du dossier isolé studio-v2/data.")
        if not candidate.is_file():
            raise HTTPException(404, "Fichier média introuvable.")
        return candidate

    def _project_or_404(project_id: str) -> Project:
        project = app.state.repo.get_project(project_id)
        if project is None:
            raise HTTPException(404, f"Projet « {project_id} » introuvable.")
        return project

    def _shot_index_or_404(project: Project, shot_id: str) -> int:
        for idx, shot in enumerate(project.shots):
            if shot.id == shot_id:
                return idx
        raise HTTPException(404, f"Plan « {shot_id} » introuvable dans le projet.")

    def _save_image_upload(upload: UploadFile, subdir: str) -> tuple[Path, int, int]:
        dest_dir = Path(app.state.settings.media_dir) / subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload.filename or "image.png").suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        target = dest_dir / f"{uuid.uuid4().hex[:12]}{suffix}"
        raw = upload.file.read()
        if not raw:
            raise HTTPException(400, "Le fichier image importé est vide.")
        target.write_bytes(raw)
        try:
            with Image.open(target) as img:
                img.verify()
            with Image.open(target) as img2:
                width, height = img2.size
        except (OSError, UnidentifiedImageError) as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(400, f"Fichier image invalide ou corrompu : {exc}") from exc
        return target, width, height

    # ── Santé & Configuration ────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> dict:
        ocr_ok = shutil.which("tesseract") is not None
        try:
            import whisper as _w  # noqa: F401 # type: ignore[import-not-found]
            whisper_ok = True
        except ImportError:
            whisper_ok = False
        try:
            ff = ffmpeg_exe()
        except ValueError:
            ff = None
        return {
            "status": "ok",
            "version": "2.0.0",
            "port": app.state.settings.port,
            "db_path": str(app.state.settings.db_path),
            "v1_isolated": True,
            "max_ltx_words": MAX_LTX_PROMPT_WORDS,
            "tools": {
                "ffmpeg": bool(ff),
                "ffmpeg_path": ff,
                "ffprobe": bool(ffprobe_exe()),
                "tesseract_ocr": ocr_ok,
                "whisper_cpu": whisper_ok,
                "deepseek_key_configured": bool(app.state.settings.effective_deepseek_key()),
            },
        }

    @app.get("/api/modal/status")
    def modal_status() -> dict:
        return _engine().check_modal_status()

    @app.get("/api/settings")
    def get_settings() -> dict:
        return app.state.settings.public_dict()

    @app.patch("/api/settings")
    def update_settings(patch: dict) -> dict:
        current_data = app.state.settings.model_dump()
        for key, value in patch.items():
            if key == "deepseek_api_key" and value in (None, ""):
                # Permet de conserver la clé existante sauf si explicitement effacée via clear_deepseek_key
                if not patch.get("clear_deepseek_key"):
                    continue
            if key in current_data:
                current_data[key] = value
        try:
            updated = V2Settings.model_validate(current_data)
            save_settings(updated)
        except Exception as exc:
            raise HTTPException(400, f"Paramètres invalides : {exc}") from exc
        app.state.settings = updated
        return updated.public_dict()

    # ── Projets & Modèle Phase 3 ─────────────────────────────────────────────

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return app.state.repo.list_projects()

    @app.post("/api/projects")
    def create_project(req: CreateProjectRequest) -> Project:
        project = Project(
            id=f"proj-{uuid.uuid4().hex[:8]}",
            title=req.title.strip() or req.idea.strip()[:60],
            idea=req.idea.strip(),
            aspect_ratio="9:16",
            target_duration_s=req.target_duration_s,
            characters=[],
            shots=[],
        )
        return app.state.repo.save_project(project)

    @app.post("/api/templates/phase3-fruit")
    def create_phase3_project() -> Project:
        existing = app.state.repo.get_project("phase3-fruit-5s")
        if existing is not None:
            return existing
        proj = build_phase3_template_project("phase3-fruit-5s")
        return app.state.repo.save_project(proj)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> Project:
        return _project_or_404(project_id)

    @app.put("/api/projects/{project_id}")
    def save_project_endpoint(project_id: str, project: Project) -> Project:
        if project.id != project_id:
            raise HTTPException(400, "L'identifiant du projet ne correspond pas à l'URL.")
        try:
            return app.state.repo.save_project(project)
        except Exception as exc:
            raise HTTPException(400, f"Validation Pydantic refusée : {exc}") from exc

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> dict:
        deleted = app.state.repo.delete_project(project_id)
        return {"deleted": deleted}

    # ── Étape 2 : SCENARIO (1 clic = 1 appel DeepSeek, zéro agent critique) ──

    @app.post("/api/projects/{project_id}/generate-scenario")
    def generate_scenario(project_id: str, req: GenerateScenarioRequest) -> dict:
        existing = _project_or_404(project_id)
        idea = (req.idea or existing.idea).strip()
        target_dur = req.target_duration_s or existing.target_duration_s
        try:
            generated, meta = generate_scenario_once(
                settings=app.state.settings,
                idea=idea,
                target_duration_s=target_dur,
                project_id=project_id,
                transport_fn=app.state.llm_transport,
            )
        except ScenarioGenerationError as exc:
            app.state.repo.record_llm_run(
                project_id=project_id,
                model=app.state.settings.deepseek_model,
                status="failed",
                error_message=str(exc),
            )
            raise HTTPException(
                422,
                {
                    "message": str(exc),
                    "raw_excerpt": exc.raw_content,
                    "hint": "Aucune relance automatique n'a été effectuée. Ajustez l'idée ou demandez explicitement une nouvelle génération.",
                },
            ) from exc

        saved = app.state.repo.save_project(generated)
        app.state.repo.record_llm_run(
            project_id=project_id,
            model=meta["model"],
            status="completed",
            prompt_tokens=meta["prompt_tokens"],
            completion_tokens=meta["completion_tokens"],
            estimated_cost_eur=meta["estimated_cost_eur"],
        )
        return {"project": saved.model_dump(mode="json"), "llm_meta": meta}

    # ── Étape 3 : ASSETS (Images manuelles ChatGPT + Bibliothèque persistante) ─

    @app.get("/api/assets")
    def list_assets(kind: str | None = None) -> list[dict]:
        return app.state.repo.list_assets(kind=kind)

    @app.post("/api/assets/generate")
    def generate_library_asset(req: GenerateImageRequest) -> dict:
        """Generate an image through a separately hosted metaai-api instance."""
        if req.kind not in {"character", "decor", "frame"}:
            raise HTTPException(400, "Type d'asset invalide (`character`, `decor`, `frame`).")
        cfg: V2Settings = app.state.settings
        base_url = cfg.metaai_api_url.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(400, "L'URL Meta AI doit commencer par http:// ou https://.")
        headers = {"Content-Type": "application/json"}
        if cfg.metaai_api_key:
            headers["Authorization"] = f"Bearer {cfg.metaai_api_key}"
        try:
            with httpx.Client(timeout=cfg.metaai_timeout_s, follow_redirects=True) as client:
                response = client.post(f"{base_url}/image", json={"prompt": req.prompt}, headers=headers)
                response.raise_for_status()
                payload = response.json()
                urls = payload.get("image_urls") or payload.get("images") or []
                if isinstance(urls, str):
                    urls = [urls]
                if not payload.get("success", True) or not urls:
                    raise ValueError("Le service n'a renvoyé aucune image.")
                image_url = str(urls[0])
                parsed = urlparse(image_url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("URL d'image renvoyée invalide.")
                image_response = client.get(image_url)
                image_response.raise_for_status()
                raw = image_response.content
                if not raw or len(raw) > 25 * 1024 * 1024:
                    raise ValueError("Image vide ou supérieure à la limite de 25 Mo.")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(502, f"Échec du service Meta AI : {exc}") from exc

        dest_dir = Path(cfg.media_dir) / "library" / req.kind
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{uuid.uuid4().hex[:12]}.img"
        target.write_bytes(raw)
        try:
            with Image.open(target) as img:
                image_format = (img.format or "WEBP").lower()
                img.verify()
            with Image.open(target) as img:
                width, height = img.size
            suffix = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif", "bmp": ".bmp"}.get(image_format)
            if not suffix:
                raise ValueError(f"Format d'image non pris en charge : {image_format}")
            final_target = target.with_suffix(suffix)
            target.replace(final_target)
            target = final_target
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(502, "Meta AI a renvoyé un fichier image invalide ou non pris en charge.") from exc
        return app.state.repo.add_asset(
            kind=req.kind,
            name=req.name.strip() or "Image Meta AI",
            description=req.description.strip() or req.prompt,
            file_path=target,
            width=width,
            height=height,
        )

    @app.post("/api/assets/upload")
    def upload_library_asset(
        file: UploadFile = File(...),
        kind: str = Form("character"),
        name: str = Form("Asset importé"),
        description: str = Form(""),
    ) -> dict:
        if kind not in {"character", "decor", "frame"}:
            raise HTTPException(400, "Type d'asset invalide (`character`, `decor`, `frame`).")
        target, width, height = _save_image_upload(file, f"library/{kind}")
        return app.state.repo.add_asset(
            kind=kind,
            name=name.strip() or (file.filename or "Image ChatGPT"),
            description=description.strip(),
            file_path=target,
            width=width,
            height=height,
        )

    @app.post("/api/projects/{project_id}/characters/{char_id}/image")
    def upload_character_image(
        project_id: str, char_id: str, file: UploadFile = File(...)
    ) -> Project:
        project = _project_or_404(project_id)
        char = next((c for c in project.characters if c.id == char_id), None)
        if char is None:
            raise HTTPException(404, f"Personnage « {char_id} » introuvable.")
        target, width, height = _save_image_upload(file, f"projects/{project_id}/characters")
        app.state.repo.add_asset(
            kind="character",
            name=char.name,
            description=char.description,
            file_path=target,
            width=width,
            height=height,
        )
        char.image_ref = str(target.resolve())
        return app.state.repo.save_project(project)

    @app.post("/api/projects/{project_id}/shots/{shot_id}/image")
    def upload_shot_image(
        project_id: str, shot_id: str, file: UploadFile = File(...)
    ) -> Project:
        project = _project_or_404(project_id)
        idx = _shot_index_or_404(project, shot_id)
        target, width, height = _save_image_upload(file, f"projects/{project_id}/shots")
        app.state.repo.add_asset(
            kind="frame",
            name=f"{project.title or project.id} — {shot_id}",
            description=project.shots[idx].initial_state,
            file_path=target,
            width=width,
            height=height,
        )
        project.shots[idx].image_ref = str(target.resolve())
        project.shots[idx].image_validated = False  # Exige une validation visuelle explicite
        return app.state.repo.save_project(project)

    @app.post("/api/projects/{project_id}/shots/{shot_id}/assign-asset")
    def assign_asset_to_shot(
        project_id: str, shot_id: str, req: AssignAssetRequest
    ) -> Project:
        project = _project_or_404(project_id)
        idx = _shot_index_or_404(project, shot_id)
        asset = app.state.repo.get_asset(req.asset_id)
        if asset is None:
            raise HTTPException(404, "Asset introuvable dans la bibliothèque.")
        project.shots[idx].image_ref = asset["file_path"]
        project.shots[idx].image_validated = False
        return app.state.repo.save_project(project)

    @app.post("/api/projects/{project_id}/characters/{char_id}/assign-asset")
    def assign_asset_to_character(
        project_id: str, char_id: str, req: AssignAssetRequest
    ) -> Project:
        project = _project_or_404(project_id)
        char = next((c for c in project.characters if c.id == char_id), None)
        if char is None:
            raise HTTPException(404, f"Personnage « {char_id} » introuvable.")
        asset = app.state.repo.get_asset(req.asset_id)
        if asset is None:
            raise HTTPException(404, "Asset introuvable dans la bibliothèque.")
        char.image_ref = asset["file_path"]
        return app.state.repo.save_project(project)

    @app.post("/api/projects/{project_id}/shots/{shot_id}/validate-image")
    def validate_shot_image(
        project_id: str, shot_id: str, req: ValidateImageRequest
    ) -> Project:
        project = _project_or_404(project_id)
        idx = _shot_index_or_404(project, shot_id)
        shot = project.shots[idx]
        if req.validated and not shot.continue_from_previous:
            if not shot.image_ref or not Path(shot.image_ref).is_file():
                raise HTTPException(
                    400, "Importez d'abord une image initiale lisible avant de la valider."
                )
        shot.image_validated = req.validated
        return app.state.repo.save_project(project)

    # ── Étape 4 : RENDER (Préparation LTX <=150 mots, Estimation coût, Autorisation) ─

    @app.post("/api/projects/{project_id}/prepare-render")
    def prepare_project_render(
        project_id: str, recompile_prompts: bool = Query(default=True)
    ) -> dict:
        project = _project_or_404(project_id)
        engine = _engine()
        shot_reports: list[dict] = []

        for idx, shot in enumerate(project.shots):
            compile_error: str | None = None
            prompt_info: dict | None = None
            try:
                if recompile_prompts or not shot.ltx_prompt.strip():
                    shot.ltx_prompt = compile_shot_prompt(project, shot)
                prompt_info = validate_ltx_prompt(shot.ltx_prompt, shot_id=shot.id)
            except PromptCapacityError as exc:
                compile_error = str(exc)

            prev_video = (
                Path(project.shots[idx - 1].rendered_video_ref)
                if idx > 0 and project.shots[idx - 1].rendered_video_ref
                else None
            )
            payload_ready = False
            payload_issue: str | None = compile_error
            if payload_issue is None:
                try:
                    engine.prepare_payload(
                        shot,
                        aspect_ratio=project.aspect_ratio,
                        previous_video_path=prev_video,
                    )
                    payload_ready = True
                except RenderPreparationError as exc:
                    payload_issue = str(exc)

            cost_info = engine.estimate_cost_breakdown(shot)
            shot_reports.append(
                {
                    "shot_id": shot.id,
                    "ltx_prompt": shot.ltx_prompt,
                    "word_count": (
                        prompt_info["word_count"]
                        if prompt_info
                        else len(shot.ltx_prompt.split())
                    ),
                    "max_words": MAX_LTX_PROMPT_WORDS,
                    "ready_for_gpu": payload_ready,
                    "blocking_reason": payload_issue,
                    "cost": cost_info,
                }
            )

        app.state.repo.save_project(project)
        all_known = all(r["cost"]["estimated_cost_eur"] is not None for r in shot_reports)
        total_est = (
            round(sum(r["cost"]["estimated_cost_eur"] for r in shot_reports), 4)
            if all_known
            else None
        )
        return {
            "project": project.model_dump(mode="json"),
            "shots": shot_reports,
            "total_estimated_cost_eur": total_est,
            "max_budget_per_take_eur": app.state.settings.max_budget_eur_per_take,
        }

    @app.post("/api/projects/{project_id}/shots/{shot_id}/render")
    def render_single_shot(
        project_id: str, shot_id: str, req: RenderShotRequest
    ) -> dict:
        """Lance UNE SEULE prise GPU après autorisation explicite de l'utilisateur."""
        project = _project_or_404(project_id)
        idx = _shot_index_or_404(project, shot_id)
        shot = project.shots[idx]
        engine = _engine()

        prev_video = (
            Path(project.shots[idx - 1].rendered_video_ref)
            if idx > 0 and project.shots[idx - 1].rendered_video_ref
            else None
        )
        engine.arm_single_take(
            project_id=project.id,
            shot_id=shot.id,
            authorized_by_human=req.authorized_by_human,
            max_budget_eur=req.max_budget_eur,
            aspect_ratio=project.aspect_ratio,
            previous_video_path=prev_video,
        )

        try:
            rendered_path = engine.render(shot)
        except AuthorizationRequiredError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (BudgetExceededError, PromptCapacityError, RenderPreparationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except RenderExecutionError as exc:
            raise HTTPException(502, str(exc)) from exc

        # Les autres plans du projet ne sont jamais touchés ; seul ce plan pointe vers la nouvelle prise
        shot.rendered_video_ref = rendered_path
        shot.human_validated = False
        saved = app.state.repo.save_project(project)

        qc = run_take_qc(Path(rendered_path), shot, app.state.settings)
        takes = app.state.repo.list_takes(project.id, shot.id)
        latest_take = takes[0] if takes else None
        if latest_take:
            latest_take = app.state.repo.update_take_qc(latest_take["id"], qc=qc)

        return {
            "project": saved.model_dump(mode="json"),
            "take": latest_take,
            "qc": qc,
        }

    @app.post("/api/projects/{project_id}/shots/{shot_id}/upload-take")
    def upload_manual_take(
        project_id: str, shot_id: str, file: UploadFile = File(...)
    ) -> dict:
        """Permet d'attacher un MP4 de prise existant (test local d'export/QC sans payer de GPU)."""
        project = _project_or_404(project_id)
        idx = _shot_index_or_404(project, shot_id)
        shot = project.shots[idx]

        dest_dir = Path(app.state.settings.takes_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"manual-{shot.id}-{uuid.uuid4().hex[:8]}.mp4"
        raw = file.file.read()
        if not raw:
            raise HTTPException(400, "Fichier vidéo vide.")
        target.write_bytes(raw)
        try:
            media = inspect_media(target)
        except ValueError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(400, str(exc)) from exc

        shot.rendered_video_ref = str(target.resolve())
        shot.human_validated = False
        saved = app.state.repo.save_project(project)
        qc = run_take_qc(target, shot, app.state.settings)
        take = app.state.repo.record_take(
            project_id=project.id,
            shot_id=shot.id,
            status="imported",
            frames=media["frames"],
            ltx_prompt=shot.ltx_prompt,
            actual_cost_eur=0.0,
            video_path=str(target.resolve()),
            qc=qc,
        )
        return {"project": saved.model_dump(mode="json"), "take": take, "qc": qc}

    @app.get("/api/projects/{project_id}/takes")
    def list_project_takes(project_id: str, shot_id: str | None = None) -> list[dict]:
        _project_or_404(project_id)
        return app.state.repo.list_takes(project_id, shot_id=shot_id)

    @app.post("/api/projects/{project_id}/shots/{shot_id}/review")
    def review_shot_take(
        project_id: str, shot_id: str, req: ReviewTakeRequest
    ) -> dict:
        project = _project_or_404(project_id)
        idx = _shot_index_or_404(project, shot_id)
        project.shots[idx].human_validated = req.human_approved
        saved = app.state.repo.save_project(project)

        takes = app.state.repo.list_takes(project_id, shot_id)
        target_id = req.take_id or (takes[0]["id"] if takes else None)
        updated_take = None
        if target_id:
            updated_take = app.state.repo.update_take_qc(
                target_id,
                human_approved=req.human_approved,
                human_notes=req.human_notes,
                actual_cost_eur=req.actual_cost_eur,
            )
        return {"project": saved.model_dump(mode="json"), "take": updated_take, "social": _queue_social()}

    # ── Étape 5 : EXPORT (Assemblage FFmpeg MP4 vertical + QC complet) ───────

    @app.post("/api/projects/{project_id}/export")
    def export_project_endpoint(project_id: str) -> dict:
        project = _project_or_404(project_id)
        try:
            out_path, qc = export_project_mp4(project, app.state.settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        record = app.state.repo.record_export(
            project_id=project.id,
            output_path=out_path,
            duration_s=qc["media"]["duration_s"] if qc.get("media") else 0.0,
            width=qc["media"]["width"] if qc.get("media") else 0,
            height=qc["media"]["height"] if qc.get("media") else 0,
            qc=qc,
        )
        record["social"] = _queue_social()
        return record

    @app.get("/api/projects/{project_id}/exports")
    def list_project_exports(project_id: str) -> list[dict]:
        _project_or_404(project_id)
        return app.state.repo.list_exports(project_id)

    @app.get("/api/costs")
    def get_cost_ledger() -> dict:
        return app.state.repo.cost_ledger()

    @app.get("/api/media")
    def serve_media(path: str = Query(...)) -> FileResponse:
        safe = _safe_media_path(path)
        mime, _ = mimetypes.guess_type(str(safe))
        return FileResponse(safe, media_type=mime or "application/octet-stream")

    # ── Frontend SvelteKit compilé (`studio-v2/web/ui_bundle`) ───────────────

    ui_dir = STUDIO_V2_ROOT / "web" / "ui_bundle"
    if (ui_dir / "_app").is_dir():
        app.mount("/_app", StaticFiles(directory=str(ui_dir / "_app")), name="ui-app")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "Endpoint API introuvable.")
        index_file = ui_dir / "index.html"
        if full_path:
            asset_candidate = (ui_dir / full_path).resolve()
            if asset_candidate.is_relative_to(ui_dir.resolve()) and asset_candidate.is_file():
                return FileResponse(asset_candidate)
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse(
            "<h1>Studio IA V2 API active</h1>"
            "<p>Compilez l'interface SvelteKit avec <code>cd studio-v2/web && npm install && npm run build</code>.</p>"
        )

    return app


app = create_app()
