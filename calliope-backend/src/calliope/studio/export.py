from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import math
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960)}
_PROCESS_OPTIONS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _binary(name):
    path = shutil.which(name)
    if not path:
        raise ValueError(f"{name} est introuvable. Installez FFmpeg pour exporter les vidéos.")
    return path


def inspect_media(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Vidéo introuvable: {path}")
    result = subprocess.run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,"
            "duration,duration_ts,time_base,nb_frames,nb_read_frames",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        **_PROCESS_OPTIONS,
    )
    if result.returncode or result.stderr.strip():
        detail = result.stderr.decode("utf-8", "replace")[-1000:]
        raise ValueError(f"Vidéo illisible ou tronquée: {path}. {detail}")
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        width, height = int(video["width"]), int(video["height"])
        fps = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0"))
        duration = video.get("duration")
        if duration in (None, "N/A") and video.get("duration_ts") is not None:
            duration = int(video["duration_ts"]) * float(Fraction(video["time_base"]))
        if duration in (None, "N/A"):
            duration = data.get("format", {}).get("duration", 0)
        duration = float(duration)
        frames = int(video.get("nb_read_frames") or 0)
        expected_frames = video.get("nb_frames")
        if expected_frames not in (None, "N/A") and frames < int(expected_frames):
            raise ValueError("Frames manquantes")
        if width <= 0 or height <= 0 or frames <= 0 or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Durée ou image vidéo invalide")
    except (KeyError, TypeError, ValueError, StopIteration, ZeroDivisionError) as exc:
        raise ValueError(f"Vidéo invalide ou tronquée: {path}") from exc
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_sec": duration,
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "has_video": True,
    }


def _check_cancel(cancel_check):
    if cancel_check and cancel_check():
        raise InterruptedError("Export annulé")


async def _progress(callback, progress, message):
    if callback:
        await callback({"progress": progress, "message": message})


def _window_for(clip, probe):
    """Fenêtre conservée d'une prise, en secondes, sur la grille 24 fps.

    Priorité aux points d'entrée/sortie explicites du plan. Sinon, la durée
    utile écrite (beat_sec) guide le trim, aligné sur la parole détectée pour
    les plans à dialogue. Sans information utile, la prise est gardée entière.
    """
    from calliope.studio import pacing

    probe_sec = probe["duration_sec"]
    if clip.get("trim_start_sec") is not None or clip.get("trim_end_sec") is not None:
        return pacing.explicit_window(
            clip.get("trim_start_sec"), clip.get("trim_end_sec"), probe_sec
        )
    beat = clip.get("beat_sec")
    try:
        beat = float(beat) if beat is not None else 0.0
    except (TypeError, ValueError):
        beat = 0.0
    if beat <= 0:
        return 0.0, probe_sec
    if (
        clip.get("audio_mode") == "native"
        and str(clip.get("dialogue") or "").strip()
    ):
        transcript = clip.get("transcript") or {}
        if transcript.get("status") != "ok" or not transcript.get("speech_intervals"):
            return 0.0, probe_sec
        if clip.get("trim_to_speech"):
            return pacing.speech_window(
                probe_sec, transcript["speech_intervals"],
                clip.get("post_roll_sec", pacing.POST_ROLL_SEC),
            )
        return pacing.useful_window(beat, probe_sec, transcript["speech_intervals"])
    return pacing.useful_window(beat, probe_sec, None)


def _check_target(duration, target, stage):
    if target is None:
        return
    try:
        target = float(target)
    except (TypeError, ValueError) as exc:
        raise ValueError("Durée cible invalide pour l'export.") from exc
    if not math.isfinite(target) or target <= 0:
        raise ValueError("Durée cible invalide pour l'export.")
    lower_margin, upper_margin = max(3.0, target * 0.1), max(5.0, target * 0.1)
    if not target - lower_margin - 1e-6 <= duration <= target + upper_margin + 1e-6:
        raise ValueError(
            f"Durée {stage}: {duration:.2f} s pour une cible de {target:.2f} s "
            f"(fenêtre autorisée {max(0, target - lower_margin):.2f}"
            f"-{target + upper_margin:.2f} s). "
            "Le contenu utile doit être ajusté avant publication; aucun padding n'est ajouté."
        )


async def _run_ffmpeg(command, duration, start, end, message, callback, cancel_check):
    _check_cancel(cancel_check)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_PROCESS_OPTIONS,
    )
    stderr_task = asyncio.create_task(process.stderr.read())
    line_task = None
    try:
        while True:
            _check_cancel(cancel_check)
            if line_task is None:
                line_task = asyncio.create_task(process.stdout.readline())
            done, _ = await asyncio.wait({line_task}, timeout=0.1)
            if not done:
                continue
            line = line_task.result()
            line_task = None
            if not line:
                break
            if line.startswith(b"out_time_us="):
                try:
                    elapsed = int(line.partition(b"=")[2]) / 1_000_000
                except ValueError:
                    continue
                progress = start + (end - start) * min(1, max(0, elapsed / duration))
                await _progress(callback, progress, message)
        stderr = await stderr_task
        returncode = await process.wait()
        _check_cancel(cancel_check)
        if returncode:
            detail = stderr.decode("utf-8", "replace")[-2000:]
            raise ValueError(f"Échec de l'export FFmpeg: {detail}")
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        if line_task is not None:
            line_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await line_task
        await process.wait()
        await stderr_task


async def assemble(
    snapshot: dict,
    destination: Path,
    progress_callback=None,
    cancel_check=None,
) -> dict:
    frozen = copy.deepcopy(snapshot)
    clips = frozen.get("clips", [])
    size = _SIZES.get(frozen.get("aspect_ratio"))
    if not size or not clips:
        raise ValueError("Choisissez un format et au moins une prise vidéo pour exporter.")
    for clip in clips:
        if clip.get("audio_mode") not in ("native", "silent") or not clip.get("path"):
            raise ValueError("Prise vidéo ou mode audio invalide dans l'export.")
    _check_cancel(cancel_check)
    ffmpeg = _binary("ffmpeg")
    await _progress(progress_callback, 0, "Vérification des prises")
    probes = []
    for clip in clips:
        _check_cancel(cancel_check)
        probes.append(await asyncio.to_thread(inspect_media, Path(clip["path"])))
    _check_cancel(cancel_check)
    windows = [
        await asyncio.to_thread(_window_for, clip, probe)
        for clip, probe in zip(clips, probes)
    ]
    _check_cancel(cancel_check)
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    durations = [max(0.0, end - start) for start, end in windows]
    total_duration = sum(durations)
    _check_target(total_duration, frozen.get("target_seconds"), "des fenêtres utiles")
    base = [ffmpeg, "-v", "error", "-nostdin", "-nostats", "-progress", "pipe:1", "-y"]
    with tempfile.TemporaryDirectory(prefix="studio-export-", dir=destination.parent) as temporary:
        work = Path(temporary)
        elapsed = 0
        for index, (clip, probe, window) in enumerate(zip(clips, probes, windows)):
            trim_start, _ = window
            duration = durations[index]
            segment = work / f"segment-{index:06d}.mov"
            command = list(base)
            if trim_start > 1e-9:
                command += ["-ss", f"{trim_start:.9f}"]
            command += ["-i", clip["path"]]
            native_audio = clip["audio_mode"] == "native" and probe["has_audio"]
            if not native_audio:
                command += ["-f", "lavfi", "-i", "anullsrc=cl=stereo:r=48000"]
            command += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0" if native_audio else "1:a:0",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
                f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                "setsar=1,fps=24,format=yuv420p,setpts=PTS-STARTPTS",
                "-af",
                "aresample=48000,apad,asetpts=PTS-STARTPTS",
                "-t",
                f"{duration:.9f}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "pcm_f32le" if frozen.get("audio", {}).get("enabled") else "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(segment),
            ]
            start = 0.05 + 0.8 * elapsed / total_duration
            end = 0.05 + 0.8 * (elapsed + duration) / total_duration
            await _run_ffmpeg(
                command,
                duration,
                start,
                end,
                f"Préparation du plan {index + 1}/{len(clips)}",
                progress_callback,
                cancel_check,
            )
            elapsed += duration
        manifest = work / "segments.txt"
        manifest.write_text(
            "".join(f"file 'segment-{index:06d}.mov'\n" for index in range(len(clips))),
            encoding="utf-8",
        )
        mixed_audio = frozen.get("audio", {}).get("enabled", False)
        ready = work / ("voice.mov" if mixed_audio else "ready.mp4")
        command = base + [
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(manifest),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_f32le" if mixed_audio else "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(ready),
        ]
        await _run_ffmpeg(
            command,
            total_duration,
            0.85,
            0.99,
            "Assemblage du film",
            progress_callback,
            cancel_check,
        )
        if mixed_audio:
            from calliope.studio.audio import mix_command

            mixed = work / "ready.mp4"
            command = await asyncio.to_thread(
                mix_command, base, ready, frozen["audio"], durations, work, mixed,
            )
            await _run_ffmpeg(
                command, total_duration, 0.95, 0.99, "Mix voix, musique et bruitages",
                progress_callback, cancel_check,
            )
            ready = mixed
        metadata = await asyncio.to_thread(inspect_media, ready)
        _check_target(metadata["duration_sec"], frozen.get("target_seconds"), "du MP4 réel")
        metadata.pop("has_video")
        metadata["clip_count"] = len(clips)
        metadata["cuts_sec"] = [
            [round(start, 4), round(end, 4)] for start, end in windows
        ]
        metadata["grid_fps"] = 24
        _check_cancel(cancel_check)
        ready.replace(destination)
    await _progress(progress_callback, 1, "Film exporté")
    return metadata
