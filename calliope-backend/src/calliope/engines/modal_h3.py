from __future__ import annotations

import asyncio
import base64
import importlib
import io
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from calliope.engines.h3_graph import (
    H3_FPS,
    H3_PROGRESS_TIMEOUT_SEC,
    H3_SIZES,
    H3_STEPS,
    frames_for_duration,
    validate_request,
)

_IMPORT_LOCK = threading.Lock()


def is_poll_network_error(error):
    _load_modal()
    from grpclib.exceptions import StreamTerminatedError
    from modal.exception import ServiceError

    return isinstance(error, (ServiceError, StreamTerminatedError, ConnectionError))


def _load_modal():
    with _IMPORT_LOCK:
        policy = asyncio.get_event_loop_policy()
        try:
            return importlib.import_module("modal")
        finally:
            asyncio.set_event_loop_policy(policy)


def _image_png(path: Path) -> bytes:
    try:
        with Image.open(path) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            return buffer.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Le fichier de référence n'est pas une image lisible.") from exc


def _last_frame_png(video_path: Path, end_sec=None) -> bytes:
    if not video_path.is_file():
        raise ValueError("La vidéo précédente est introuvable.")
    with tempfile.TemporaryDirectory(prefix="studio-h3-frame-") as directory:
        target = Path(directory) / "last.png"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    *(
                        ["-ss", str(max(0, float(end_sec) - 1 / H3_FPS))]
                        if end_sec is not None else ["-sseof", "-2"]
                    ),
                    "-i",
                    str(video_path),
                    "-map",
                    "0:v:0",
                    "-an",
                    *( ["-frames:v", "1"] if end_sec is not None else [] ),
                    "-update",
                    "1",
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise ValueError("FFmpeg est requis pour extraire la dernière image.") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Impossible d'extraire la dernière image de la vidéo.") from exc
        return _image_png(target)


def build_request(
    *,
    prompt: str,
    duration_sec: float,
    aspect_ratio: str,
    seed: int,
    image_path: Path | None = None,
    audio_mode: str = "native",
    previous_video_path: Path | None = None,
    trace_id: str | None = None,
    previous_video_time: float | None = None,
) -> dict:
    if aspect_ratio not in H3_SIZES:
        raise ValueError("Le format doit être 16:9, 9:16 ou 1:1.")
    if image_path is not None and previous_video_path is not None:
        raise ValueError("Choisissez une image ou la vidéo précédente comme référence.")
    frames = frames_for_duration(duration_sec)
    width, height = H3_SIZES[aspect_ratio]
    payload = {
        "contract_version": 1,
        "prompt": prompt.strip() if isinstance(prompt, str) else prompt,
        "requested_duration_sec": duration_sec,
        "duration_sec": frames / H3_FPS,
        "aspect_ratio": aspect_ratio,
        "width": width,
        "height": height,
        "fps": H3_FPS,
        "frames": frames,
        "steps": H3_STEPS,
        "seed": seed,
        "audio_mode": audio_mode,
        "continuity_mode": "none",
    }
    if trace_id is not None:
        payload["trace_id"] = trace_id
    validate_request(payload)
    if image_path is not None:
        payload["image_base64"] = base64.b64encode(_image_png(Path(image_path))).decode("ascii")
        payload["continuity_mode"] = "image"
    elif previous_video_path is not None:
        payload["image_base64"] = base64.b64encode(
            _last_frame_png(Path(previous_video_path), previous_video_time)
        ).decode("ascii")
        payload["continuity_mode"] = "last_frame"
    validate_request(payload)
    return payload


class ModalH3Engine:
    def __init__(self, app_name: str, function_name: str):
        self.app_name = app_name
        self.function_name = function_name
        self._results = {}
        self.remote_terminal = False

    def _function(self):
        modal = _load_modal()
        return modal.Function.from_name(self.app_name, self.function_name)

    def check_available(self) -> dict:
        try:
            self._function().hydrate()
        except ImportError:
            return {"ok": False, "message": "Le client Modal n'est pas installé."}
        except Exception:
            return {
                "ok": False,
                "message": "Modal inaccessible: authentification ou déploiement à vérifier.",
            }
        return {
            "ok": True,
            "message": "Déploiement H3 accessible. GPU alloué uniquement à la génération.",
        }

    def spawn(self, payload: dict) -> str:
        validate_request(payload)
        return self._function().spawn(payload).object_id

    def poll(self, call_id: str) -> dict | None:
        self.remote_terminal = call_id in self._results
        if call_id in self._results:
            return self._results[call_id]
        modal = _load_modal()
        from modal.exception import FunctionTimeoutError, OutputExpiredError, RemoteError

        try:
            result = modal.FunctionCall.from_id(call_id).get(timeout=0)
        except Exception as exc:
            # SDK 1.6.1 attaches this cache only to deserialized remote tracebacks.
            if isinstance(exc, (FunctionTimeoutError, OutputExpiredError, RemoteError)) or hasattr(
                exc, '__line_cache__',
            ):
                self.remote_terminal = True
            elif isinstance(exc, TimeoutError):
                return None
            raise
        self.remote_terminal = True
        if (
            not isinstance(result, dict)
            or not result.get("video_base64")
            or not isinstance(result.get("metadata"), dict)
        ):
            raise RuntimeError("Le worker H3 a renvoyé un résultat invalide.")
        self._results[call_id] = result
        return result

    def cancel(self, call_id: str) -> None:
        modal = _load_modal()
        modal.FunctionCall.from_id(call_id).cancel(terminate_containers=True)
        self._results.pop(call_id, None)

    def progress(self, trace_id: str) -> dict | None:
        try:
            store = _load_modal().Dict.from_name("studio-ia-h3-progress")
            snapshot = asyncio.run(asyncio.wait_for(
                store.get.aio(trace_id), timeout=H3_PROGRESS_TIMEOUT_SEC,
            ))
            return snapshot if isinstance(snapshot, dict) else None
        except Exception:
            return None

    def identity(self):
        identity = {'workspace': None, 'environment': None, 'profile': None}
        try:
            _load_modal()
            config_module = importlib.import_module("modal.config")
            config = config_module.Config()
            identity = {
                "profile": config_module._profile,
                "environment": config.get("environment") or None,
                "workspace": None,
            }
            response = asyncio.run(
                asyncio.wait_for(
                    config_module._lookup_workspace(
                        config.get("server_url"),
                        config.get("token_id"),
                        config.get("token_secret"),
                        oauth_refresh_token=config.get("oauth_refresh_token"),
                        oauth_client_id=config.get("oauth_client_id"),
                        oauth_client_secret=config.get("oauth_client_secret"),
                    ),
                    timeout=H3_PROGRESS_TIMEOUT_SEC,
                )
            )
            identity["workspace"] = response.username
            return identity
        except Exception:
            return identity
