from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
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
from calliope.engines.h3_graph import (
    H3_COMFYUI_REVISION,
    H3_MODEL_FILES,
    H3_PROGRESS_TIMEOUT_SEC,
    H3_VOLUME_NAME,
    build_h3_graph,
    validate_request,
)
from PIL import Image

COMFYUI_DIR = Path(os.getenv("NEWSREEL_COMFYUI_DIR", "/opt/ComfyUI"))
MODELS_ROOT = Path("/mnt/fasth3-models")
COMFY_API = "http://127.0.0.1:8188"
COMFYUI_REPOSITORY = "https://github.com/Comfy-Org/ComfyUI.git"
_COMFY_PROCESS = None
_COMFY_LOG = None
logger = logging.getLogger(__name__)


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
                logger.warning("H3 progress snapshot unavailable")

    def emit(self, phase, detail):
        if self.snapshot["phase"] == phase:
            return
        at = datetime.now(timezone.utc).isoformat()
        self.snapshot["phase"] = phase
        self.snapshot["events"].append({"phase": phase, "at": at, "detail": detail})
        logger.info("H3 phase=%s trace_id=%s", phase, self.snapshot["trace_id"])
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
            self.emit("GENERATING", "ComfyUI executing H3 sampler")
        elif kind in ("progress", "progress_state"):
            node = data if kind == "progress" else data.get("nodes", {}).get("14", {})
            node_id = str(node.get("node", node.get(
                "node_id", "14" if kind == "progress_state" else ""
            )))
            value, maximum = node.get("value"), node.get("max")
            if (
                node_id == "14"
                and isinstance(value, (int, float))
                and isinstance(maximum, (int, float))
                and maximum > 0 and value > 0
            ):
                self.snapshot["sampling"] = {"node": "14", "value": value, "max": maximum}
                self.emit("GENERATING", "ComfyUI reported H3 sampling progress")
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
            logger.warning("H3 ComfyUI event stream unavailable")
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
                logger.warning("H3 ComfyUI progress message unavailable")

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


def _mount_volume_models() -> None:
    locations = [
        ("diffusion_models", H3_MODEL_FILES["unet"]),
        ("text_encoders", H3_MODEL_FILES["clip"]),
        ("vae", H3_MODEL_FILES["video_vae"]),
        ("vae", H3_MODEL_FILES["audio_vae"]),
    ]
    for category, filename in locations:
        source = MODELS_ROOT / category / filename
        if not source.is_file():
            raise RuntimeError(f"Poids H3 absents du volume existant: {category}/{filename}")
        target = COMFYUI_DIR / "models" / category / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"Refus de remplacer un fichier de poids: {target}")
        target.symlink_to(source)


def _log_tail(path: Path) -> str:
    with path.open("rb") as log:
        log.seek(max(0, path.stat().st_size - 6000))
        return log.read().decode("utf-8", errors="replace")


def _start_comfyui() -> tuple[subprocess.Popen, Path]:
    _mount_volume_models()
    log_path = Path("/tmp/studio-ia-h3-comfyui.log")
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
            raise RuntimeError("Échec du rendu H3: " + repr(status.get("messages", []))[:4000])
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
    raise TimeoutError("Le rendu H3 a dépassé 4800 s.")


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
    if video is None:
        raise RuntimeError("Le fichier H3 ne contient pas de vidéo.")
    fps = float(Fraction(video["avg_frame_rate"]))
    frames = int(video.get("nb_read_frames", video.get("nb_frames", 0)))
    if fps <= 0 or frames <= 0:
        raise RuntimeError("Le fichier H3 ne contient pas de frames lisibles.")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "frames": frames,
        "duration_sec": frames / fps,
        "has_audio": any(stream["codec_type"] == "audio" for stream in probe["streams"]),
        "video_codec": video.get("codec_name"),
    }


def _run_clip(payload: dict) -> dict:
    validate_request(payload)
    progress = _Progress(payload.get("trace_id"))
    progress.emit("CONTAINER_STARTED", "Modal function entered")
    generation_start = time.perf_counter()
    try:
        return _render_clip(payload, progress, generation_start)
    except BaseException:
        progress.emit("FAILED", "H3 render failed; inspect function logs")
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
        process, log_path = _ensure_comfyui()
        client_id = uuid.uuid4().hex
        events = _ComfyEvents(client_id)
        events.start()
        prefix = "studio/" + uuid.uuid4().hex
        graph = build_h3_graph(payload, output_prefix=prefix, uploaded_image=image_name)
        requested_prompt_id = str(uuid.uuid4())
        progress.snapshot["comfy_prompt_id"] = requested_prompt_id
        progress.publish()
        queued = _http_json("/prompt", {
            "prompt": graph, "client_id": client_id, "prompt_id": requested_prompt_id,
        })
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI a refusé le graphe H3.")
        if str(prompt_id) != requested_prompt_id:
            raise RuntimeError("ComfyUI a renvoyé un identifiant de graphe inattendu.")
        source = _wait_for_video(str(prompt_id), prefix, process, log_path, events, progress)
        events.drain(progress)
        with tempfile.TemporaryDirectory(prefix="studio-h3-output-") as directory:
            video_path = Path(directory) / "clip.mp4"
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
            ]
            if payload["audio_mode"] == "native":
                command.extend(["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"])
            else:
                command.append("-an")
            command.extend(["-movflags", "+faststart", str(video_path)])
            subprocess.run(command, check=True, capture_output=True, timeout=300)
            metadata = _probe_video(video_path)
            if (metadata["width"], metadata["height"], metadata["frames"]) != (
                payload["width"],
                payload["height"],
                payload["frames"],
            ):
                raise RuntimeError(
                    "Les dimensions ou la durée du rendu H3 ne correspondent pas à la requête."
                )
            metadata.update(
                {
                    "generation_seconds": round(time.perf_counter() - generation_start, 3),
                    "engine": "MiniMax H3 / FastH3 V2",
                    "steps": payload["steps"],
                    "seed": payload["seed"],
                    "audio_mode": payload["audio_mode"],
                    "requested_duration_sec": payload["requested_duration_sec"],
                    "continuity_mode": payload["continuity_mode"],
                    "comfyui_revision": H3_COMFYUI_REVISION,
                    "reference_mode_experimental": image_name is not None,
                }
            )
            video_base64 = base64.b64encode(video_path.read_bytes()).decode("ascii")
            progress.emit("ARTIFACT_READY", "H3 MP4 readable and validated")
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
                logger.warning("H3 source MP4 cleanup unavailable")
        if events is not None:
            events.close()
        if image_name:
            (COMFYUI_DIR / "input" / image_name).unlink(missing_ok=True)


app = modal.App("studio-ia-h3")
progress_store = modal.Dict.from_name("studio-ia-h3-progress", create_if_missing=True)
models_volume = modal.Volume.from_name(H3_VOLUME_NAME, create_if_missing=False).with_mount_options(
    read_only=True
)
comfy_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libgl1", "libglib2.0-0", "libsm6", "libxext6")
    .pip_install("torch==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .run_commands(
        "git init /opt/ComfyUI",
        f"git -C /opt/ComfyUI remote add origin {COMFYUI_REPOSITORY}",
        f"git -C /opt/ComfyUI fetch --depth 1 origin {H3_COMFYUI_REVISION}",
        f"git -C /opt/ComfyUI checkout --detach {H3_COMFYUI_REVISION}",
        "pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt",
    )
    .env({"NEWSREEL_COMFYUI_DIR": "/opt/ComfyUI"})
    .add_local_python_source("calliope.engines")
)


@app.function(
    image=comfy_image,
    gpu="L40S",
    cpu=8,
    memory=98304,
    timeout=5400,
    min_containers=0,
    max_containers=2,
    scaledown_window=300,
    volumes={str(MODELS_ROOT): models_volume},
)
def render_clip(payload: dict) -> dict:
    return _run_clip(payload)
