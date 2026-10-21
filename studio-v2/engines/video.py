"""Moteur de rendu vidéo `VideoEngine` et implémentation `LtxModalEngine` (LTX-2.5 sur Modal).

Garanties strictes V2 :
1. Validation complète AVANT tout appel GPU (garde-fou local 300 mots, grille `8k+1 <= 361` frames,
   image initiale lisible et validée par l'humain).
2. Estimation transparente fondée sur le tarif réel du fournisseur Modal + durée observée/calibrée,
   avec affichage explicite de la fourchette et des incertitudes (`None` si tarif non configuré).
3. Autorisation humaine explicite obligatoire à chaque prise ; plafond uniquement si fourni.
4. Une seule prise à la fois, AUCUN retry GPU automatique, relâchement immédiat du conteneur.
5. Enregistrement systématique du coût réel et des erreurs dans `v2.db` et `COSTS.md`.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import io
import math
import subprocess
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

from core.config import V2Settings
from core.ltx_prompt import validate_ltx_prompt
from core.scenario import Shot
from core.storage import StudioRepository
from qc.media import PROCESS_FLAGS, ffmpeg_exe, inspect_media

LTX_FPS = 24
LTX_STEPS = 8
LTX_MIN_FRAMES = 25
LTX_MAX_FRAMES = 361  # 15.041 s à 24 fps
LTX_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "1:1": (1024, 1024),
}

_MODAL_IMPORT_LOCK = threading.Lock()
_RENDER_LOCK = threading.Lock()


class AuthorizationRequiredError(PermissionError):
    """Levée lorsqu'un rendu payant est tenté sans autorisation explicite de l'utilisateur."""


class BudgetExceededError(ValueError):
    """Levée lorsque l'estimation dépasse le plafond autorisé."""


class RenderPreparationError(ValueError):
    """Levée avant GPU lorsqu'un prérequis de plan ou de média n'est pas satisfait."""


class RenderExecutionError(RuntimeError):
    """Levée si l'exécution Modal échoue, après sauvegarde complète de l'erreur."""


class RenderBusyError(RenderExecutionError):
    pass


class RenderPendingError(RenderExecutionError):
    pass


def frames_for_ltx_duration(duration_s: float) -> int:
    if not isinstance(duration_s, (int, float)) or not math.isfinite(duration_s) or duration_s <= 0:
        raise RenderPreparationError("La durée du plan doit être strictement positive.")
    requested = max(LTX_MIN_FRAMES, math.ceil(duration_s * LTX_FPS))
    frames = requested + (1 - requested) % 8
    if frames > LTX_MAX_FRAMES:
        raise RenderPreparationError(
            f"Arrêt avant GPU : {duration_s:.2f}s requiert {frames} frames, au-delà du maximum "
            f"moteur LTX-2.5 ({LTX_MAX_FRAMES} frames / 15,04 s)."
        )
    return frames


def image_to_png_bytes(path: Path) -> bytes:
    target = Path(path)
    if not target.is_file():
        raise RenderPreparationError(f"Image de référence introuvable : {target}")
    try:
        with Image.open(target) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        raise RenderPreparationError(
            f"Fichier image illisible ({target.name}) : {exc}"
        ) from exc


def extract_last_frame_png(video_path: Path) -> bytes:
    source = Path(video_path)
    if not source.is_file():
        raise RenderPreparationError(
            f"Impossible d'enchaîner : la vidéo du plan précédent est introuvable ({source})."
        )
    with tempfile.TemporaryDirectory(prefix="studio-v2-lastframe-") as tmp:
        out_png = Path(tmp) / "last.png"
        proc = subprocess.run(
            [
                ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-sseof",
                "-0.15",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-update",
                "1",
                str(out_png),
            ],
            capture_output=True,
            timeout=45,
            **PROCESS_FLAGS,
        )
        if proc.returncode != 0 or not out_png.is_file():
            detail = proc.stderr.decode("utf-8", errors="replace")[-400:]
            raise RenderPreparationError(
                f"Extraction de la dernière image échouée ({source.name}) : {detail}"
            )
        return image_to_png_bytes(out_png)


def _load_modal():
    with _MODAL_IMPORT_LOCK:
        policy = asyncio.get_event_loop_policy()
        try:
            return importlib.import_module("modal")
        finally:
            asyncio.set_event_loop_policy(policy)


class VideoEngine(ABC):
    """Interface minimale de génération vidéo Studio IA V2."""

    @abstractmethod
    def estimate_cost(self, shot: Shot) -> float | None:
        """Retourne le coût estimé en EUR, ou `None` si le tarif fournisseur n'est pas configuré."""

    @abstractmethod
    def render(self, shot: Shot) -> str:
        """Exécute le rendu d'un plan autorisé et retourne le chemin du MP4 généré."""


class LtxModalEngine(VideoEngine):
    """Moteur LTX-2.5 sur Modal (`studio-ia-ltx` / `render_clip`), une prise à la fois, zéro retry."""

    def __init__(
        self,
        settings: V2Settings,
        repo: StudioRepository | None = None,
        modal_runner: Callable[[dict], dict] | None = None,
    ):
        self.settings = settings
        self.repo = repo
        self._modal_runner = modal_runner
        # Contexte d'autorisation armé explicitement avant un appel à `render(shot)`
        self._armed_authorization: dict | None = None
        self.last_take_id: str | None = None

    def arm_single_take(
        self,
        *,
        project_id: str,
        shot_id: str,
        authorized_by_human: bool,
        max_budget_eur: float | None = None,
        aspect_ratio: str = "9:16",
        previous_video_path: Path | None = None,
    ) -> None:
        """Arme explicitement l'autorisation pour UNE SEULE prise (consommée dès `render()`)."""
        self._armed_authorization = {
            "project_id": project_id,
            "shot_id": shot_id,
            "authorized_by_human": bool(authorized_by_human),
            "max_budget_eur": max_budget_eur,
            "aspect_ratio": aspect_ratio,
            "previous_video_path": previous_video_path,
        }

    def estimate_cost_breakdown(self, shot: Shot) -> dict:
        frames = frames_for_ltx_duration(shot.duration_s)
        stats = self.repo.observed_gpu_stats() if self.repo else {"count": 0}
        if stats.get("count", 0) > 0 and stats.get("avg_sec_per_frame"):
            sec_per_frame = float(stats["avg_sec_per_frame"])
            expected_compute_s = frames * sec_per_frame + self.settings.scaledown_window_s
            warm_compute_s = expected_compute_s
            cold_compute_s = expected_compute_s + self.settings.cold_startup_s
            calibration_source = (
                f"Moyenne observée sur {stats['count']} prise(s) réelle(s) dans v2.db"
            )
        else:
            warm_compute_s = (
                frames * self.settings.warm_sec_per_frame + self.settings.scaledown_window_s
            )
            cold_compute_s = warm_compute_s + self.settings.cold_startup_s
            expected_compute_s = cold_compute_s
            calibration_source = (
                "Mesures de référence LTX-2.5 (docs/studio/LTX.md : 0,54 s/frame chaud + "
                f"{self.settings.cold_startup_s:.0f} s montage/démarrage conteneur froid)"
            )

        worst_compute_s = (
            frames * self.settings.warm_sec_per_frame
            + max(self.settings.cold_startup_s, 120.0)
            + self.settings.worst_scaledown_window_s
        )

        eur_per_sec = self.settings.container_eur_per_sec()
        usd_per_sec = self.settings.container_usd_per_sec()

        if eur_per_sec is None or usd_per_sec is None:
            return {
                "estimated_cost_eur": None,
                "warm_min_eur": None,
                "cold_expected_eur": None,
                "worst_case_eur": None,
                "estimated_cost_usd": None,
                "frames": frames,
                "engine_duration_s": round(frames / LTX_FPS, 3),
                "warm_compute_s": round(warm_compute_s, 1),
                "expected_compute_s": round(expected_compute_s, 1),
                "worst_compute_s": round(worst_compute_s, 1),
                "gpu_label": self.settings.gpu_label,
                "eur_per_sec": None,
                "usd_per_sec": usd_per_sec,
                "calibration_source": calibration_source,
                "uncertainties": [
                    "Tarif GPU ou taux USD->EUR non configuré : aucun prix inventé n'est affiché.",
                    "Démarrage à froid Modal variable (+70 à +120 s si aucun conteneur actif).",
                    "Fenêtre d'inactivité facturée après fin de calcul (2 s après relâchement, jusqu'à 60 s sinon).",
                ],
            }

        warm_eur = round(warm_compute_s * eur_per_sec, 4)
        expected_eur = round(expected_compute_s * eur_per_sec, 4)
        worst_eur = round(worst_compute_s * eur_per_sec, 4)

        return {
            "estimated_cost_eur": expected_eur,
            "warm_min_eur": warm_eur,
            "cold_expected_eur": expected_eur,
            "worst_case_eur": worst_eur,
            "estimated_cost_usd": round(expected_compute_s * usd_per_sec, 4),
            "frames": frames,
            "engine_duration_s": round(frames / LTX_FPS, 3),
            "warm_compute_s": round(warm_compute_s, 1),
            "expected_compute_s": round(expected_compute_s, 1),
            "worst_compute_s": round(worst_compute_s, 1),
            "gpu_label": self.settings.gpu_label,
            "eur_per_sec": round(eur_per_sec, 7),
            "usd_per_sec": round(usd_per_sec, 7),
            "calibration_source": calibration_source,
            "uncertainties": [
                f"Conteneur chaud vs froid : {warm_compute_s:.0f}s ({warm_eur:.3f} €) à {expected_compute_s:.0f}s ({expected_eur:.3f} €).",
                f"Pire cas (chargement lent + 60s d'inactivité) : {worst_compute_s:.0f}s ({worst_eur:.3f} €).",
                f"Facturation Modal en USD convertie au taux configuré ({self.settings.usd_to_eur} €/USD).",
            ],
        }

    def estimate_cost(self, shot: Shot) -> float | None:
        return self.estimate_cost_breakdown(shot)["estimated_cost_eur"]

    def check_modal_status(self) -> dict:
        if self._modal_runner is not None:
            return {"ok": True, "message": "Runner Modal personnalisé actif."}
        try:
            modal = _load_modal()
            fn = modal.Function.from_name(
                self.settings.modal_app_name, self.settings.modal_function_name
            )
            fn.hydrate()
            return {
                "ok": True,
                "message": f"Worker Modal `{self.settings.modal_app_name}.{self.settings.modal_function_name}` accessible.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": (
                    f"Modal non connecté dans cet environnement ({type(exc).__name__}). "
                    "Sur Windows, vérifiez `modal token info` avant la Phase 3."
                ),
            }

    def prepare_payload(
        self,
        shot: Shot,
        *,
        aspect_ratio: str = "9:16",
        previous_video_path: Path | None = None,
    ) -> dict:
        prompt_check = validate_ltx_prompt(shot.ltx_prompt, shot_id=shot.id)
        if aspect_ratio not in LTX_SIZES:
            raise RenderPreparationError(f"Format non supporté par LTX-2.5 : {aspect_ratio}")

        frames = frames_for_ltx_duration(shot.duration_s)
        width, height = LTX_SIZES[aspect_ratio]

        payload: dict = {
            "contract_version": 1,
            "prompt": prompt_check["prompt"],
            "requested_duration_sec": shot.duration_s,
            "duration_sec": frames / LTX_FPS,
            "aspect_ratio": aspect_ratio,
            "width": width,
            "height": height,
            "fps": LTX_FPS,
            "frames": frames,
            "steps": LTX_STEPS,
            "video_profile": "ltx",
            "seed": int(shot.seed),
            "audio_mode": "native",
            "continuity_mode": "none",
            "trace_id": f"v2-{shot.id}-{uuid.uuid4().hex[:8]}",
        }

        if shot.continue_from_previous:
            if previous_video_path is None:
                raise RenderPreparationError(
                    f"Plan « {shot.id} » : `continue_from_previous=True` mais le plan précédent "
                    "n'a pas encore de vidéo rendue."
                )
            png_bytes = extract_last_frame_png(previous_video_path)
            payload["image_base64"] = base64.b64encode(png_bytes).decode("ascii")
            payload["continuity_mode"] = "last_frame"
        elif shot.image_ref:
            if not shot.image_validated:
                raise RenderPreparationError(
                    f"Plan « {shot.id} » : validez visuellement l'image initiale dans l'étape "
                    "ASSETS avant de lancer le GPU."
                )
            png_bytes = image_to_png_bytes(Path(shot.image_ref))
            payload["image_base64"] = base64.b64encode(png_bytes).decode("ascii")
            payload["continuity_mode"] = "image"
        else:
            raise RenderPreparationError(
                f"Plan « {shot.id} » : importez et validez une image initiale (ChatGPT) "
                "avant de lancer le rendu GPU."
            )

        return payload

    def _call_modal_once(self, payload: dict, take_id: str, call_id: str | None = None) -> dict:
        if self._modal_runner is not None:
            return self._modal_runner(payload)

        modal = _load_modal()
        from modal.exception import Error, FunctionTimeoutError, OutputExpiredError

        fn = modal.Function.from_name(
            self.settings.modal_app_name, self.settings.modal_function_name
        )
        if call_id:
            call = modal.FunctionCall.from_id(call_id)
        else:
            fn.hydrate()
            try:
                call = fn.spawn(payload)
            except Exception as exc:
                raise RenderPendingError(
                    "Envoi Modal incertain. Aucun nouvel appel GPU ne sera lance automatiquement. "
                    "Verifiez les appels Modal avant toute nouvelle prise."
                ) from exc
            if self.repo:
                self.repo.update_render(take_id, call_id=call.object_id)
        try:
            result = call.get(timeout=5460)  # Worker configure pour 5400 s.
        except (FunctionTimeoutError, OutputExpiredError):
            raise
        except (TimeoutError, ConnectionError, OSError, Error) as exc:
            raise RenderPendingError(
                f"Resultat Modal non recupere ({call.object_id}). Reprenez ce plan pour "
                "recuperer le meme appel, sans nouvelle generation GPU."
            ) from exc
        finally:
            try:
                fn.update_autoscaler(scaledown_window=int(self.settings.scaledown_window_s))
            except Exception:
                pass
        return result

    def _append_costs_md(
        self,
        *,
        project_id: str,
        shot_id: str,
        status: str,
        estimated_eur: float | None,
        actual_eur: float | None,
        compute_s: float | None,
        note: str,
    ) -> None:
        path = self.settings.costs_markdown_path
        if not path.exists():
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        est_str = f"{estimated_eur:.4f} €" if estimated_eur is not None else "N/A"
        act_str = f"{actual_eur:.4f} €" if actual_eur is not None else "N/A"
        dur_str = f"{compute_s:.1f} s" if compute_s is not None else "N/A"
        row = f"| {timestamp} | `{project_id}` / `{shot_id}` | {status} | {est_str} | {act_str} | {dur_str} | {note} |\n"
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(row)
        except OSError:
            pass

    def render(self, shot: Shot) -> str:
        if not _RENDER_LOCK.acquire(blocking=False):
            raise RenderBusyError("Une prise est deja en cours. Attendez sa fin avant de reprendre.")
        try:
            return self._render_single_take(shot)
        finally:
            _RENDER_LOCK.release()

    def _render_single_take(self, shot: Shot) -> str:
        auth = self._armed_authorization
        self._armed_authorization = None  # Usage unique strict (1 prise = 1 autorisation)
        pending = self.repo.pending_render() if self.repo else None
        recovering = bool(auth and pending and (pending["project_id"], pending["shot_id"])
                          == (auth["project_id"], shot.id))

        if not auth or (not auth.get("authorized_by_human") and not recovering) or auth.get("shot_id") != shot.id:
            raise AuthorizationRequiredError(
                f"Arrêt sécurité GPU : aucune autorisation humaine explicite active pour le plan « {shot.id} »."
            )

        project_id = auth["project_id"]
        max_budget = auth["max_budget_eur"]
        breakdown = self.estimate_cost_breakdown(shot)
        estimated_eur = breakdown["estimated_cost_eur"]

        if not pending and estimated_eur is not None and max_budget is not None and estimated_eur > max_budget:
            raise BudgetExceededError(
                f"Arrêt plafond budgétaire : coût estimé ({estimated_eur:.3f} €) > plafond "
                f"configuré ({max_budget:.3f} €) pour le plan « {shot.id} »."
            )

        self.settings.ensure_dirs()
        if pending:
            if (pending["project_id"], pending["shot_id"]) != (project_id, shot.id):
                raise RenderBusyError(f"Recuperez d'abord la prise {pending['project_id']} / {pending['shot_id']}.")
            if not pending["modal_call_id"]:
                raise RenderPendingError("Envoi Modal incertain sans identifiant recupere. Verifiez les appels Modal avant toute nouvelle prise.")
            take_id = pending["id"]
            request = pending["request"]
            payload = request["payload"]
            estimated_eur = request["estimated_eur"]
            max_budget = request["max_budget"]
            existing_take = self.repo.get_take(take_id)
            if existing_take and existing_take["status"] == "completed":
                self.last_take_id = take_id
                return existing_take["video_path"]
        else:
            payload = self.prepare_payload(
                shot, aspect_ratio=auth["aspect_ratio"], previous_video_path=auth["previous_video_path"],
            )
            take_id = f"take-{shot.id}-{uuid.uuid4().hex[:8]}"
            if self.repo:
                try:
                    self.repo.start_render(take_id, project_id, shot.id, {
                        "payload": payload, "estimated_eur": estimated_eur, "max_budget": max_budget,
                    })
                except ValueError as exc:
                    raise RenderBusyError(str(exc)) from exc
        self.last_take_id = take_id
        output_path = self.settings.takes_dir / f"{take_id}.mp4"

        wall_start = time.perf_counter()
        try:
            result = self._call_modal_once(payload, take_id, pending["modal_call_id"] if pending else None)
            if not isinstance(result, dict) or not result.get("video_base64"):
                raise RenderExecutionError("Réponse Modal invalide : `video_base64` manquant.")
            video_bytes = base64.b64decode(result["video_base64"], validate=True)
            output_path.write_bytes(video_bytes)
            media = inspect_media(output_path)
            if ((media["width"], media["height"]) != (payload["width"], payload["height"])
                    or abs(media["frames"] - payload["frames"]) > 1 or not media["has_audio"]):
                raise RenderExecutionError("Le MP4 recu ne correspond pas aux dimensions, a la duree ou a l'audio demandes.")
        except RenderPendingError as exc:
            if self.repo:
                self.repo.update_render(take_id, error=str(exc))
            raise
        except Exception as exc:
            wall_elapsed = round(time.perf_counter() - wall_start, 2)
            failed_cost = None  # Le temps d'attente client n'est pas du temps GPU facture.
            if self.repo is not None:
                self.repo.record_take(
                    take_id=take_id,
                    project_id=project_id,
                    shot_id=shot.id,
                    status="failed",
                    seed=payload["seed"],
                    frames=payload["frames"],
                    ltx_prompt=payload["prompt"],
                    estimated_cost_eur=estimated_eur,
                    max_budget_eur=max_budget,
                    authorized_by_human=True,
                    observed_compute_s=None,
                    actual_cost_eur=failed_cost,
                    error_message=str(exc),
                )
                self.repo.update_render(take_id, status="failed", error=str(exc))
            self._append_costs_md(
                project_id=project_id,
                shot_id=shot.id,
                status="ÉCHEC",
                estimated_eur=estimated_eur,
                actual_eur=failed_cost,
                compute_s=wall_elapsed,
                note=str(exc)[:120],
            )
            if isinstance(exc, RenderExecutionError):
                raise
            raise RenderExecutionError(
                f"Échec du rendu GPU du plan « {shot.id} » (aucun retry automatique lancé) : {exc}"
            ) from exc

        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        metadata["cost_is_estimate"] = True
        metadata["trace_id"] = payload["trace_id"]
        observed_s = metadata.get("generation_seconds")
        if not isinstance(observed_s, (int, float)) or not math.isfinite(observed_s) or observed_s <= 0:
            observed_s = None
        billable_s = observed_s + self.settings.scaledown_window_s if observed_s is not None else None
        eur_rate = self.settings.container_eur_per_sec()
        actual_eur = round(billable_s * eur_rate, 4) if eur_rate is not None and billable_s is not None else None

        if self.repo is not None:
            self.repo.record_take(
                take_id=take_id,
                project_id=project_id,
                shot_id=shot.id,
                status="completed",
                seed=payload["seed"],
                frames=payload["frames"],
                ltx_prompt=payload["prompt"],
                estimated_cost_eur=estimated_eur,
                max_budget_eur=max_budget,
                authorized_by_human=True,
                observed_compute_s=round(observed_s, 3) if observed_s is not None else None,
                actual_cost_eur=actual_eur,
                video_path=str(output_path.resolve()),
                metadata=metadata,
            )
        self._append_costs_md(
            project_id=project_id,
            shot_id=shot.id,
            status="OK",
            estimated_eur=estimated_eur,
            actual_eur=actual_eur,
            compute_s=observed_s,
            note=f"{payload['frames']} frames ({output_path.name})",
        )
        return str(output_path.resolve())
