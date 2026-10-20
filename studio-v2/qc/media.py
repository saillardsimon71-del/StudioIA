"""Inspection média et audio extraite de V1 (`studio/export.py`) et allégée.

Compatible Windows (`CREATE_NO_WINDOW`) et autonome : utilise `ffprobe`/`ffmpeg` du
PATH système en priorité, avec repli transparent sur le binaire statique `imageio-ffmpeg`
du `.venv` lorsque `ffprobe` n'est pas installé dans le PATH.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import cv2

PROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise ValueError(
            "FFmpeg est introuvable. Installez FFmpeg ou `pip install imageio-ffmpeg`."
        ) from exc


def ffprobe_exe() -> str | None:
    return shutil.which("ffprobe")


def _probe_with_ffprobe(path: Path, probe_bin: str) -> dict:
    proc = subprocess.run(
        [
            probe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,"
            "avg_frame_rate,r_frame_rate,sample_rate,channels,duration,nb_frames",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        **PROCESS_FLAGS,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace")[-600:]
        raise ValueError(f"Média illisible ({path.name}) : {detail}")

    data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError(f"Aucun flux vidéo dans {path.name}.")

    width = int(video["width"])
    height = int(video["height"])
    fps_raw = video.get("avg_frame_rate") or video.get("r_frame_rate") or "24/1"
    fps = float(Fraction(fps_raw)) if fps_raw not in ("0/0", "0") else 0.0
    duration_s = float(
        video.get("duration") or data.get("format", {}).get("duration") or 0.0
    )
    nb_frames = int(video.get("nb_frames") or round(duration_s * fps))
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "frames": nb_frames,
        "duration_s": round(duration_s, 4),
        "video_codec": video.get("codec_name") or "unknown",
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        "channels": int(audio.get("channels") or 0) if audio else 0,
        "audio_duration_s": round(
            float(audio.get("duration") or duration_s), 4
        )
        if audio
        else 0.0,
    }


def _probe_with_ffmpeg_and_cv2(path: Path) -> dict:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        timeout=30,
        **PROCESS_FLAGS,
    )
    info_text = proc.stderr.decode("utf-8", errors="replace")

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"Vidéo illisible ou corrompue ({path.name}).")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ok_first, _ = cap.read()
        if not ok_first:
            raise ValueError(f"Première image vidéo illisible ({path.name}).")
    finally:
        cap.release()

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info_text)
    if dur_match:
        h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
        duration_s = h * 3600 + m * 60 + s
    else:
        duration_s = frames / fps if fps > 0 else 0.0

    vcodec_match = re.search(r"Video:\s*([A-Za-z0-9_]+)", info_text)
    audio_line_match = re.search(r"Audio:\s*([^\r\n]+)", info_text)
    has_audio = audio_line_match is not None
    acodec = None
    sample_rate = 0
    channels = 0
    if audio_line_match:
        aline = audio_line_match.group(1)
        ac_m = re.match(r"([A-Za-z0-9_]+)", aline)
        acodec = ac_m.group(1) if ac_m else "unknown"
        sr_m = re.search(r"(\d+)\s*Hz", aline)
        sample_rate = int(sr_m.group(1)) if sr_m else 0
        if "stereo" in aline or "2 channels" in aline:
            channels = 2
        elif "mono" in aline or "1 channels" in aline:
            channels = 1

    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "frames": frames,
        "duration_s": round(duration_s, 4),
        "video_codec": vcodec_match.group(1) if vcodec_match else "unknown",
        "has_audio": has_audio,
        "audio_codec": acodec,
        "sample_rate": sample_rate,
        "channels": channels,
        "audio_duration_s": round(duration_s, 4) if has_audio else 0.0,
    }


def inspect_media(path: Path) -> dict:
    """Vérifie qu'un MP4 existe, est décodable et retourne ses caractéristiques exactes."""
    target = Path(path)
    if not target.is_file() or target.stat().st_size == 0:
        raise ValueError(f"Fichier vidéo introuvable ou vide : {target}")

    probe_bin = ffprobe_exe()
    info = (
        _probe_with_ffprobe(target, probe_bin)
        if probe_bin
        else _probe_with_ffmpeg_and_cv2(target)
    )
    if (
        info["width"] <= 0
        or info["height"] <= 0
        or info["frames"] <= 0
        or not math.isfinite(info["duration_s"])
        or info["duration_s"] <= 0
    ):
        raise ValueError(f"Dimensions ou durée invalides pour {target.name}.")
    return info


def verify_export_delivery(
    path: Path,
    *,
    expected_size: tuple[int, int] = (1080, 1920),
    sample_rate: int = 48000,
    channels: int = 2,
) -> dict:
    """Vérifie la conformité technique d'un MP4 vertical d'export."""
    info = inspect_media(path)
    errors: list[str] = []
    if (info["width"], info["height"]) != expected_size:
        errors.append(
            f"dimensions {info['width']}x{info['height']} au lieu de "
            f"{expected_size[0]}x{expected_size[1]}"
        )
    if not info["has_audio"]:
        errors.append("aucune piste audio dans le MP4 exporté")
    else:
        if info["sample_rate"] != sample_rate:
            errors.append(f"audio {info['sample_rate']} Hz au lieu de {sample_rate} Hz")
        if info["channels"] != channels:
            errors.append(f"audio {info['channels']} canal(aux) au lieu de {channels}")
        if info["duration_s"] - info["audio_duration_s"] > 0.25:
            errors.append("piste audio plus courte que l'image")
    if errors:
        raise ValueError("Export MP4 non conforme : " + "; ".join(errors) + ".")
    return info
