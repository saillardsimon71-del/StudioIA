"""Worker Modal autonome LTX-2.5 pour Studio IA V2 — sans dépendance à V1.

Déploiement depuis le PC de l'opérateur (compte Modal `kulejean567`) :
    modal deploy studio-v2/deploy/modal_ltx_v2.py

Implémente exactement le contrat `studio-ia-ltx.render_clip` attendu par
`studio-v2/engines/video.py` (LtxModalEngine) :

    Entrée  : dict `payload` avec les clés validées par `validate_request`
              (contract_version, prompt, requested_duration_sec, duration_sec,
               aspect_ratio, width, height, fps, frames, steps, video_profile,
               seed, audio_mode, continuity_mode, trace_id, image_base64?)
    Sortie  : dict `{"video_base64": str, "metadata": dict}`
              metadata contient au minimum generation_seconds, width, height,
              frames, fps, duration_sec, has_audio, video_codec, audio_codec,
              plus model_revision, refinement_steps, video_cfg, audio_cfg,
              comfyui_revision, comfy_startup_seconds, etc.

Volume des poids : `studio-ltx25-models` monté RO en `/mnt/ltx-models`.
GPU : L40S + 8 CPU + 64 GiB RAM (profil prod docs/studio/LTX.md). Scaledown 2s.
Image : ComfyUI @ d49e888586dd8ae012c0667b33466b815fee07f7, PyTorch 2.8.0 cu128.

Le worker n'a PAS besoin du secret Hugging Face — les 5 poids sont déjà
dans le volume. Aucun jeton n'est présent dans ce fichier.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from queue import Empty, Queue

import modal
from PIL import Image

# ── Constantes LTX-2.5 — révision validée docs/studio/LTX.md ────────────────

LTX_VOLUME_NAME = "studio-ltx25-models"
LTX_MODEL_REVISION = "49b093a39c19169a8a81721dcc541e07e945232f"
LTX_COMFYUI_REVISION = "d49e888586dd8ae012c0667b33466b815fee07f7"
LTX_VIDEO_CFG = 1.0
LTX_NEGATIVE_PROMPT = "background music, subtitles, text overlays, voiceover narration"

LTX_MODEL_FILES = {
    "unet": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
    "clip": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
    "video_vae": "ltx-2.5-video-vae-conv-bf16.safetensors",
    "audio_vae": "ltx-2.5-audio-vae-bf16.safetensors",
    "upscaler": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
}

# Grille LTX-2.5 : 8k+1 frames, max 361 frames (15.04s @24fps)
LTX_FPS = 24
LTX_STEPS = 8
LTX_MIN_FRAMES = 25
LTX_MAX_FRAMES = 361
LTX_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "1:1": (1024, 1024),
}

# ── App Modal & ressources ───────────────────────────────────────────────────

MODELS_MOUNT = Path("/mnt/ltx-models")
COMFYUI_DIR = Path(os.getenv("NEWSREEL_COMFYUI_DIR", "/opt/ComfyUI"))
COMFY_API = "http://127.0.0.1:8188"
COMFYUI_REPOSITORY = "https://github.com/Comfy-Org/ComfyUI.git"
_COMFY_PROCESS: subprocess.Popen | None = None
_COMFY_LOG: Path | None = None
logger = logging.getLogger(__name__)

H3_PROGRESS_TIMEOUT_SEC = 3  # réutilisé pour LTX Dict

app = modal.App("studio-ia-ltx")
models_volume = modal.Volume.from_name(LTX_VOLUME_NAME, create_if_missing=False).with_mount_options(read_only=True)
progress_store = modal.Dict.from_name("studio-ia-ltx-progress", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0", "libsm6", "libxext6")
    .pip_install("torch==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .run_commands(
        "git init /opt/ComfyUI",
        "git -C /opt/ComfyUI remote add origin https://github.com/Comfy-Org/ComfyUI.git",
        f"git -C /opt/ComfyUI fetch --depth 1 origin {LTX_COMFYUI_REVISION}",
        f"git -C /opt/ComfyUI checkout --detach {LTX_COMFYUI_REVISION}",
        "pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt torch==2.8.0 --extra-index-url https://download.pytorch.org/whl/cu128",
    )
    .env({"NEWSREEL_COMFYUI_DIR": "/opt/ComfyUI"})
)

# ── Helpers LTX autonomes (zero dependance V1) ───────────────────────────

def frames_for_ltx_duration(seconds: float) -> int:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("La durée LTX doit être positive et finie.")
    requested = max(LTX_MIN_FRAMES, math.ceil(seconds * LTX_FPS))
    frames = requested + (1 - requested) % 8
    if frames > LTX_MAX_FRAMES:
        raise ValueError(f"La durée LTX dépasse la plage prise en charge (15.041 s) — {frames} frames demandés.")
    return frames


def validate_request(payload: dict) -> None:
    required = {
        "contract_version",
        "prompt",
        "requested_duration_sec",
        "duration_sec",
        "aspect_ratio",
        "width",
        "height",
        "fps",
        "frames",
        "steps",
        "seed",
        "audio_mode",
        "continuity_mode",
    }
    if not isinstance(payload, dict) or not required <= payload.keys():
        raise ValueError(f"Requête LTX incomplète — clés requises {sorted(required)}.")
    if payload.keys() - required - {"image_base64", "trace_id", "video_profile"}:
        raise ValueError(f"La requête LTX contient des paramètres inconnus : {sorted(payload.keys() - required - {'image_base64','trace_id','video_profile'})}")
    if "trace_id" in payload and (
        not isinstance(payload["trace_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payload["trace_id"])
    ):
        raise ValueError("Identifiant de trace LTX invalide.")
    if payload["contract_version"] != 1:
        raise ValueError("Version du contrat LTX invalide (attendu 1).")
    if not isinstance(payload["prompt"], str) or not payload["prompt"].strip():
        raise ValueError("Le prompt LTX est vide.")
    # Aspect & tailles
    aspect = payload["aspect_ratio"]
    if aspect not in LTX_SIZES or (payload["width"], payload["height"]) != LTX_SIZES[aspect]:
        raise ValueError(f"Format LTX invalide : {aspect} -> {(payload['width'], payload['height'])} (attendu {LTX_SIZES.get(aspect, 'inconnu')}).")
    if payload["fps"] != LTX_FPS or payload["steps"] != LTX_STEPS:
        raise ValueError(f"Les étapes et la cadence ne correspondent pas au modèle LTX (fps={payload['fps']} steps={payload['steps']}).")
    frames = payload["frames"]
    if type(frames) is not int or frames != frames_for_ltx_duration(payload["requested_duration_sec"]):
        raise ValueError(f"Frames invalides pour LTX : {frames} (attendu {frames_for_ltx_duration(payload['requested_duration_sec'])}).")
    duration = payload["duration_sec"]
    if type(duration) not in (int, float) or not math.isclose(duration, frames / LTX_FPS, rel_tol=0, abs_tol=1e-6):
        raise ValueError(f"La durée LTX doit correspondre aux frames à 24 fps ({frames} -> {frames/LTX_FPS:.3f}s, reçu {duration}).")
    if type(payload["seed"]) is not int or not 0 <= payload["seed"] < 2**64:
        raise ValueError("La seed LTX doit être un entier non signé sur 64 bits.")
    if payload["audio_mode"] not in ("native", "silent"):
        raise ValueError(f"Mode audio LTX invalide : {payload['audio_mode']}")
    image = payload.get("image_base64")
    if image is None:
        if payload["continuity_mode"] != "none":
            raise ValueError("Une référence LTX est requise pour ce mode de continuité.")
    else:
        if not isinstance(image, str) or not image:
            raise ValueError("Image LTX base64 invalide.")
        try:
            content = base64.b64decode(image, validate=True)
        except Exception as exc:
            raise ValueError("Image LTX base64 invalide.") from exc
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("L'image LTX doit être au format PNG (signature PNG manquante).")
        if payload["continuity_mode"] not in ("image", "last_frame"):
            raise ValueError(f"Mode de continuité LTX invalide : {payload['continuity_mode']}")
    # video_profile optionnel — doit être 'ltx' si présent
    if "video_profile" in payload and payload["video_profile"] != "ltx":
        raise ValueError(f"Le worker LTX n'accepte que video_profile='ltx' (reçu {payload['video_profile']!r}).")


def build_ltx_comfy_graph(payload: dict, *, output_prefix: str, uploaded_image: str | None = None) -> dict:
    """Construit le graphe ComfyUI LTX-2.5 deux passes (distilled 8 steps + spatial upsampler x2)."""
    validate_request(payload)
    if payload.get("video_profile") not in (None, "ltx"):
        raise ValueError("Cette requête nécessite le profil LTX.")
    if not output_prefix or output_prefix.startswith("/") or ".." in output_prefix.split("/"):
        raise ValueError("Préfixe de sortie LTX invalide.")
    if bool(payload.get("image_base64")) != bool(uploaded_image):
        raise ValueError("La référence LTX doit correspondre à l'image chargée (image_base64 vs uploaded_image).")

    def node(kind: str, **inputs):
        return {"class_type": kind, "inputs": inputs}

    graph = {
        "6": node("UNETLoader", unet_name=LTX_MODEL_FILES["unet"], weight_dtype="default"),
        "11": node("VAELoader", vae_name=LTX_MODEL_FILES["video_vae"]),
        "24": node("VAELoader", vae_name=LTX_MODEL_FILES["audio_vae"]),
        "13": node("CLIPLoader", clip_name=LTX_MODEL_FILES["clip"], type="ltxv", device="default"),
        "20": node("CLIPTextEncode", clip=["13", 0], text=payload["prompt"]),
        "21": node("CLIPTextEncode", clip=["13", 0], text=LTX_NEGATIVE_PROMPT),
        "22": node("LTXVConditioning", positive=["20", 0], negative=["21", 0], frame_rate=payload["fps"]),
        "30": node(
            "EmptyLTXVLatentVideo",
            width=payload["width"] // 2,
            height=payload["height"] // 2,
            length=payload["frames"],
            batch_size=1,
        ),
        "32": node(
            "LTXVEmptyLatentAudio",
            audio_vae=["24", 0],
            frames_number=payload["frames"],
            frame_rate=payload["fps"],
            batch_size=1,
        ),
        "33": node("LTXVConcatAVLatent", video_latent=["30", 0], audio_latent=["32", 0]),
        "15": node("RandomNoise", noise_seed=payload["seed"]),
        "16": node(
            "LTXVDualCFGGuider",
            model=["6", 0],
            positive=["22", 0],
            negative=["22", 1],
            video_cfg=LTX_VIDEO_CFG,
            audio_cfg=1.0,
        ),
        "17": node("KSamplerSelect", sampler_name="euler_ancestral"),
        "9": node("ManualSigmas", sigmas="1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"),
        "14": node(
            "SamplerCustomAdvanced",
            noise=["15", 0],
            guider=["16", 0],
            sampler=["17", 0],
            sigmas=["9", 0],
            latent_image=["33", 0],
        ),
        "34": node("LTXVSeparateAVLatent", av_latent=["14", 0]),
        "35": node("LatentUpscaleModelLoader", model_name=LTX_MODEL_FILES["upscaler"]),
        "36": node("LTXVLatentUpsampler", samples=["34", 0], upscale_model=["35", 0], vae=["11", 0]),
        "38": node("LTXVConcatAVLatent", video_latent=["36", 0], audio_latent=["34", 1]),
        "39": node("RandomNoise", noise_seed=42),
        "40": node("ManualSigmas", sigmas="0.85, 0.7250, 0.4219, 0.0"),
        "41": node(
            "SamplerCustomAdvanced",
            noise=["39", 0],
            guider=["16", 0],
            sampler=["17", 0],
            sigmas=["40", 0],
            latent_image=["38", 0],
        ),
        "42": node("LTXVSeparateAVLatent", av_latent=["41", 0]),
        "10": node(
            "VAEDecodeTiled",
            samples=["42", 0],
            vae=["11", 0],
            tile_size=512,
            overlap=64,
            temporal_size=64,
            temporal_overlap=16,
        ),
        "91": node("CreateVideo", images=["10", 0], fps=payload["fps"]),
        "92": node("SaveVideo", video=["91", 0], filename_prefix=output_prefix, format="auto", codec="auto"),
    }
    if uploaded_image:
        graph["1"] = node("LoadImage", image=uploaded_image)
        graph["2"] = node("LTXVPreprocess", image=["1", 0], img_compression=18)
        graph["31"] = node("LTXVImgToVideoInplace", vae=["11", 0], image=["2", 0], latent=["30", 0], strength=0.7, bypass=False)
        graph["33"]["inputs"]["video_latent"] = ["31", 0]
        graph["37"] = node("LTXVImgToVideoInplace", vae=["11", 0], image=["2", 0], latent=["36", 0], strength=1.0, bypass=False)
        graph["38"]["inputs"]["video_latent"] = ["37", 0]
    if payload.get("audio_mode") == "native":
        graph["23"] = node("LTXVAudioVAEDecode", samples=["42", 1], audio_vae=["24", 0])
        graph["91"]["inputs"]["audio"] = ["23", 0]
    return graph


# Alias compatibilité avec l'ancien import V1 (deploy/modal_ltx.py)
def build_ltx_graph(payload: dict, *, output_prefix: str, uploaded_image: str | None = None) -> dict:
    return build_ltx_comfy_graph(payload, output_prefix=output_prefix, uploaded_image=uploaded_image)


def mount_models() -> None:
    """Monte les 5 poids LTX depuis le volume vers ComfyUI/models par symlink RO."""
    folders = {
        "unet": "diffusion_models",
        "clip": "text_encoders",
        "video_vae": "vae",
        "audio_vae": "vae",
        "upscaler": "latent_upscale_models",
    }
    for key, folder in folders.items():
        source = MODELS_MOUNT / folder / LTX_MODEL_FILES[key]
        if not source.is_file():
            raise RuntimeError(
                f"Poids LTX absents dans le volume {LTX_VOLUME_NAME} : {folder}/{LTX_MODEL_FILES[key]} — "
                f"exécutez d'abord `modal run studio-v2/deploy/modal_download_ltx.py`."
            )
        target = COMFYUI_DIR / "models" / folder / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"Refus de remplacer les poids existants : {target} (nettoyez ComfyUI/models).")
        target.symlink_to(source)


# ── Runtime ComfyUI (identique à deploy/modal_h3.py, adapté LTX) ────────────

class _Progress:
    def __init__(self, trace_id):
        self.available = True
        now = datetime.now(timezone.utc).isoformat()
        self.snapshot = {
            "trace_id": trace_id,
            "phase": None,
            "events": [],
            "container_id": os.getenv("MODAL_TASK_ID"),
            "comfy_prompt_id": None,
            "started_at": now,
            "updated_at": now,
        }

    def publish(self):
        self.snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self.snapshot["trace_id"] and self.available:
            try:
                asyncio.run(asyncio.wait_for(
                    progress_store.put.aio(self.snapshot["trace_id"], self.snapshot),
                    timeout=H3_PROGRESS_TIMEOUT_SEC,
                ))
            except Exception:
                self.available = False
                logger.warning("LTX progress snapshot unavailable")

    def emit(self, phase, detail):
        if self.snapshot["phase"] == phase:
            return
        at = datetime.now(timezone.utc).isoformat()
        self.snapshot["phase"] = phase
        self.snapshot["events"].append({"phase": phase, "at": at, "detail": detail})
        logger.info("LTX phase=%s trace_id=%s", phase, self.snapshot["trace_id"])
        self.publish()

    def comfy_event(self, message):
        if not isinstance(message, dict):
            return
        data = message.get("data", {})
        if not isinstance(data, dict):
            return
        if data.get("prompt_id") != self.snapshot["comfy_prompt_id"]:
            return
        kind = message.get("type")
        if kind == "execution_start":
            self.emit("LOADING_MODEL", "ComfyUI started graph preparation; cache/loading unknown")
        elif kind == "executing" and str(data.get("node")) == "14":
            self.emit("GENERATING", "ComfyUI executing LTX sampler (pass 1/2)")
        elif kind == "executing" and str(data.get("node")) == "41":
            self.emit("GENERATING", "ComfyUI executing LTX refinement (pass 2/2)")
        elif kind in ("progress", "progress_state"):
            node = data if kind == "progress" else data.get("nodes", {}).get("14", {})
            node_id = str(node.get("node", node.get("node_id", "14" if kind == "progress_state" else "")))
            value, maximum = node.get("value"), node.get("max")
            if (
                node_id in ("14", "41")
                and isinstance(value, (int, float))
                and isinstance(maximum, (int, float))
                and maximum > 0 and value > 0
            ):
                self.snapshot["sampling"] = {"node": node_id, "value": value, "max": maximum}
                self.emit("GENERATING", "ComfyUI reported LTX sampling progress")
                self.publish()


class _ComfyEvents:
    def __init__(self, client_id):
        self.client_id = client_id
        self.messages = Queue()
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(timeout=6)

    def _run(self):
        try:
            asyncio.run(self._listen())
        except Exception:
            logger.warning("LTX ComfyUI event stream unavailable")
        finally:
            self.ready.set()

    async def _listen(self):
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = COMFY_API.replace("http://", "ws://") + "/ws?clientId=" + self.client_id
            async with session.ws_connect(url) as socket:
                self.ready.set()
                while not self.stopped.is_set():
                    try:
                        message = await socket.receive(timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self.messages.put(json.loads(message.data))
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    def drain(self, progress):
        while True:
            try:
                message = self.messages.get_nowait()
            except Empty:
                return
            try:
                progress.comfy_event(message)
            except Exception:
                logger.warning("LTX ComfyUI progress message unavailable")

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)


def _http_json(route: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        COMFY_API + route,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(3000).decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI HTTP {exc.code}: {detail}") from exc


def _log_tail(path: Path) -> str:
    with path.open("rb") as log:
        log.seek(max(0, path.stat().st_size - 6000))
        return log.read().decode("utf-8", errors="replace")


def _start_comfyui() -> tuple[subprocess.Popen, Path]:
    mount_models()
    log_path = Path("/tmp/studio-ia-ltx-comfyui.log")
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "main.py",
                "--listen",
                "127.0.0.1",
                "--port",
                "8188",
                "--disable-auto-launch",
                "--disable-comfy-compiler",
            ],
            cwd=COMFYUI_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("ComfyUI arrêté au démarrage:\n" + _log_tail(log_path))
        try:
            _http_json("/system_stats", timeout=5)
            return process, log_path
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            time.sleep(2)
    _stop_process(process)
    raise TimeoutError("ComfyUI n'a pas démarré en 360 s:\n" + _log_tail(log_path))


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _reset_comfyui() -> None:
    global _COMFY_PROCESS, _COMFY_LOG
    process = _COMFY_PROCESS
    _COMFY_PROCESS, _COMFY_LOG = None, None
    if process is not None:
        _stop_process(process)


def _ensure_comfyui() -> tuple[subprocess.Popen, Path]:
    global _COMFY_PROCESS, _COMFY_LOG
    if _COMFY_PROCESS is not None:
        if _COMFY_PROCESS.poll() is None:
            try:
                _http_json("/system_stats", timeout=5)
                return _COMFY_PROCESS, _COMFY_LOG
            except (urllib.error.URLError, TimeoutError, RuntimeError):
                pass
        _reset_comfyui()
    _COMFY_PROCESS, _COMFY_LOG = _start_comfyui()
    return _COMFY_PROCESS, _COMFY_LOG


def _readable_video(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return bool(file.read(1))
    except OSError:
        return False


def _wait_for_video(
    prompt_id: str, prefix: str, process: subprocess.Popen, log: Path,
    events=None, progress=None,
) -> Path:
    deadline = time.monotonic() + 4800
    artifact_deadline = None
    while time.monotonic() < deadline:
        if events is not None:
            events.drain(progress)
        if process.poll() is not None:
            raise RuntimeError("ComfyUI arrêté pendant la génération:\n" + _log_tail(log))
        history = _http_json("/history/" + urllib.parse.quote(prompt_id, safe=""))
        entry = history.get(prompt_id, {})
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError("Échec du rendu LTX: " + repr(status.get("messages", []))[:4000])
        if status.get("completed") or status.get("status_str") in ("success", "completed"):
            if artifact_deadline is None:
                artifact_deadline = time.monotonic() + 60
            output_root = (COMFYUI_DIR / "output").resolve()
            for output in entry.get("outputs", {}).values():
                for key in ("images", "videos", "gifs", "files"):
                    for item in output.get(key, []):
                        if Path(item.get("filename", "")).suffix.lower() != ".mp4":
                            continue
                        path = (
                            output_root / item.get("subfolder", "") / item["filename"]
                        ).resolve()
                        if path.is_relative_to(output_root) and _readable_video(path):
                            return path
            relative = Path(prefix)
            candidates = [
                path.resolve()
                for path in (output_root / relative.parent).glob(relative.name + "*.mp4")
                if path.resolve().is_relative_to(output_root) and _readable_video(path)
            ]
            if candidates:
                return max(candidates, key=lambda path: path.stat().st_mtime_ns)
            if time.monotonic() >= artifact_deadline:
                raise RuntimeError("ComfyUI a terminé sans produire de fichier MP4 lisible en 60 s.")
        time.sleep(2)
    raise TimeoutError("Le rendu LTX a dépassé 4800 s.")


def _probe_video(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    probe = json.loads(completed.stdout)
    video = next((stream for stream in probe["streams"] if stream["codec_type"] == "video"), None)
    audio = next((stream for stream in probe["streams"] if stream["codec_type"] == "audio"), None)
    if video is None:
        raise RuntimeError("Le fichier LTX ne contient pas de vidéo.")
    fps = float(Fraction(video["avg_frame_rate"]))
    frames = int(video.get("nb_read_frames", video.get("nb_frames", 0)))
    if fps <= 0 or frames <= 0:
        raise RuntimeError("Le fichier LTX ne contient pas de frames lisibles.")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "frames": frames,
        "duration_sec": frames / fps,
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name"),
        "pixel_format": video.get("pix_fmt"),
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def _prepare_video(source, target, audio_mode):
    info = _probe_video(source)
    copy_video = info.get("video_codec") == "h264" and info.get("pixel_format") == "yuv420p"
    copy_audio = info.get("audio_codec") == "aac"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
               "-map", "0:v:0", "-map_metadata", "-1"]
    command.extend(["-c:v", "copy"] if copy_video else
                   ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"])
    if audio_mode == "native":
        command.extend(["-map", "0:a:0"])
        command.extend(["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", str(target)])
    subprocess.run(command, check=True, capture_output=True, timeout=300)
    return "stream_copy" if copy_video and (audio_mode != "native" or copy_audio) else "transcode"


def _run_clip(payload: dict) -> dict:
    validate_request(payload)
    progress = _Progress(payload.get("trace_id"))
    progress.emit("CONTAINER_STARTED", "Modal function entered")
    generation_start = time.perf_counter()
    try:
        return _render_clip(payload, progress, generation_start)
    except BaseException:
        progress.emit("FAILED", "LTX render failed; inspect function logs")
        _reset_comfyui()
        raise


def _render_clip(payload, progress, generation_start):
    image_name = None
    events = None
    source = None
    if payload.get("image_base64"):
        content = base64.b64decode(payload["image_base64"], validate=True)
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        image_name = "studio-" + uuid.uuid4().hex + ".png"
        input_path = COMFYUI_DIR / "input" / image_name
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(content)
    try:
        progress.emit(
            "LOADING_MODEL",
            "Starting/checking ComfyUI and preparing model; loading vs cache unknown",
        )
        startup_started = time.perf_counter()
        process_reused = _COMFY_PROCESS is not None and _COMFY_PROCESS.poll() is None
        process, log_path = _ensure_comfyui()
        startup_seconds = time.perf_counter() - startup_started
        client_id = uuid.uuid4().hex
        events = _ComfyEvents(client_id)
        events.start()
        prefix = "studio/" + uuid.uuid4().hex
        graph = build_ltx_comfy_graph(payload, output_prefix=prefix, uploaded_image=image_name)
        requested_prompt_id = str(uuid.uuid4())
        progress.snapshot["comfy_prompt_id"] = requested_prompt_id
        progress.publish()
        execution_started = time.perf_counter()
        queued = _http_json("/prompt", {
            "prompt": graph, "client_id": client_id, "prompt_id": requested_prompt_id,
        })
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI a refusé le graphe LTX.")
        if str(prompt_id) != requested_prompt_id:
            raise RuntimeError("ComfyUI a renvoyé un identifiant de graphe inattendu.")
        source = _wait_for_video(str(prompt_id), prefix, process, log_path, events, progress)
        events.drain(progress)
        execution_seconds = time.perf_counter() - execution_started
        postprocess_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="studio-ltx-output-") as directory:
            video_path = Path(directory) / "clip.mp4"
            output_method = _prepare_video(source, video_path, payload["audio_mode"])
            metadata = _probe_video(video_path)
            if (metadata["width"], metadata["height"], metadata["frames"]) != (
                payload["width"],
                payload["height"],
                payload["frames"],
            ):
                raise RuntimeError(
                    f"Les dimensions ou la durée du rendu LTX ne correspondent pas à la requête "
                    f"(attendu {payload['width']}x{payload['height']} {payload['frames']} frames, "
                    f"reçu {metadata['width']}x{metadata['height']} {metadata['frames']} frames)."
                )
            metadata.update(
                {
                    "generation_seconds": round(time.perf_counter() - generation_start, 3),
                    "comfy_startup_seconds": round(startup_seconds, 3),
                    "comfy_execution_seconds": round(execution_seconds, 3),
                    "postprocess_seconds": round(time.perf_counter() - postprocess_started, 3),
                    "output_method": output_method,
                    "comfy_process_reused": process_reused,
                    "engine": "LTX-2.5 Distilled",
                    "model_file": LTX_MODEL_FILES["unet"],
                    "model_revision": LTX_MODEL_REVISION,
                    "video_profile": payload.get("video_profile", "ltx"),
                    "refinement_steps": 3,
                    "video_cfg": LTX_VIDEO_CFG,
                    "audio_cfg": 1.0,
                    "steps": payload["steps"],
                    "seed": payload["seed"],
                    "audio_mode": payload["audio_mode"],
                    "requested_duration_sec": payload["requested_duration_sec"],
                    "continuity_mode": payload["continuity_mode"],
                    "comfyui_revision": LTX_COMFYUI_REVISION,
                }
            )
            video_base64 = base64.b64encode(video_path.read_bytes()).decode("ascii")
            progress.emit("ARTIFACT_READY", "LTX MP4 readable and validated")
            return {
                "video_base64": video_base64,
                "metadata": metadata,
            }
    finally:
        if source is not None:
            try:
                if source.resolve().is_relative_to((COMFYUI_DIR / "output").resolve()):
                    source.unlink(missing_ok=True)
            except OSError:
                logger.warning("LTX source MP4 cleanup unavailable")
        if events is not None:
            events.close()
        if image_name:
            (COMFYUI_DIR / "input" / image_name).unlink(missing_ok=True)


# ── Fonction Modal exposée — contrat V2 strict ───────────────────────────────

@app.function(
    image=image,
    gpu="L40S",
    cpu=8,
    memory=65536,
    timeout=5400,
    min_containers=0,
    max_containers=1,
    scaledown_window=2,
    volumes={str(MODELS_MOUNT): models_volume},
)
def render_clip(payload: dict) -> dict:
    """Point d'entrée Modal `studio-ia-ltx.render_clip` — une prise LTX-2.5 à la fois, zéro retry."""
    result = _run_clip(payload)
    # Compatibilité : enrichit les métadonnées comme l'ancien deploy/modal_ltx.py
    result["metadata"].update(
        model_revision=LTX_MODEL_REVISION,
        refinement_steps=3,
        video_cfg=LTX_VIDEO_CFG,
        audio_cfg=1.0,
    )
    return result

