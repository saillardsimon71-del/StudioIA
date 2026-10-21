r"""Assemblage FFmpeg MP4 vertical (1080x1920, 24 fps, AAC 48 kHz stéréo) et chiffres exacts.

Extrait l'essentiel éprouvé de V1 (`studio/export.py` + `studio/screen_text.py`) sans dette :
- Chaque prise est normalisée en `.mov` H.264 `yuv420p` 24 fps et PCM 48 kHz stéréo (`loudnorm`)
  afin d'éviter tout micro-trou AAC aux coupes.
- Si un plan déclare `display_overlay`, un fichier ASS local `overlay_N.ass` incruste les
  valeurs exactes aux moments déclarés (`initial_text` puis `changes`), exécuté avec
  `cwd=work_dir` pour éviter tout problème d'échappement de chemin `C:\` sous Windows.
- Les segments sont concaténés proprement en MP4 AAC 48 kHz stéréo `+faststart` et vérifiés par `qc.runner`.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from core.config import V2Settings
from core.scenario import DisplayOverlay, Project, Shot
from qc.media import PROCESS_FLAGS, ffmpeg_exe, inspect_media
from qc.runner import run_export_qc

EXPORT_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


def _ass_timestamp(seconds: float) -> str:
    cs = round(max(0.0, seconds) * 100)
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def _rgb_to_ass_bgr(hex_rgb: str) -> str:
    return hex_rgb[4:6] + hex_rgb[2:4] + hex_rgb[0:2]


def write_fixed_overlay_ass(
    overlay: DisplayOverlay,
    duration_s: float,
    width: int,
    height: int,
    ass_path: Path,
) -> Path:
    """Génère un script ASS fixe horodaté pour les chiffres/textes précis au montage."""
    px = round(overlay.x * width)
    py = round(overlay.y * height)
    font_px = max(24, round(overlay.font_size * height))
    fg_bgr = _rgb_to_ass_bgr(overlay.color)
    bg_bgr = _rgb_to_ass_bgr(overlay.box_bg or "101418")
    border_style = 3 if overlay.box_bg else 1

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: FixedZone,Arial,{font_px},&H00{fg_bgr},&H00{fg_bgr},&H20{bg_bgr},&H30{bg_bgr},"
        f"1,0,0,0,100,100,2,0,{border_style},12,0,5,0,0,0,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    cuts = [0.0] + [c.at_sec for c in overlay.changes if 0.0 < c.at_sec < duration_s] + [duration_s]
    for start_t, end_t in zip(cuts, cuts[1:]):
        val = overlay.initial_text
        for change in overlay.changes:
            if change.at_sec <= start_t + 1e-6:
                val = change.text
        safe_val = str(val).replace("{", "").replace("}", "")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start_t)},{_ass_timestamp(end_t)},FixedZone,,0,0,0,,"
            f"{{\\an5\\pos({px},{py})}}{safe_val}"
        )

    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ass_path


def _run_cmd(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        timeout=300,
        **PROCESS_FLAGS,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace")[-1200:]
        raise ValueError(f"Échec FFmpeg : {detail}")


def export_project_mp4(
    project: Project,
    settings: V2Settings,
    output_path: Path | None = None,
    apply_loudnorm: bool = True,
) -> tuple[Path, dict]:
    """Assemble les prises rendues d'un projet en un MP4 vertical prêt à visionner + rapport QC."""
    if not project.shots:
        raise ValueError("Aucun plan dans le projet à exporter.")

    missing = [s.id for s in project.shots if not s.rendered_video_ref or not Path(s.rendered_video_ref).is_file()]
    if missing:
        raise ValueError(
            "Plans sans prise vidéo disponible sur le disque : " + ", ".join(missing)
        )

    size = EXPORT_SIZES.get(project.aspect_ratio, (1080, 1920))
    width, height = size
    settings.ensure_dirs()
    destination = (
        Path(output_path).resolve()
        if output_path is not None
        else (settings.exports_dir / f"{project.id}-export-{uuid.uuid4().hex[:8]}.mp4").resolve()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_exe()

    with tempfile.TemporaryDirectory(prefix="studio-v2-export-", dir=str(destination.parent)) as tmp:
        work = Path(tmp)
        segment_names: list[str] = []

        for idx, shot in enumerate(project.shots):
            src = Path(shot.rendered_video_ref).resolve()  # type: ignore[arg-type]
            probe = inspect_media(src)
            clip_dur = probe["duration_s"]

            vf_parts = [
                f"scale={width}:{height}:force_original_aspect_ratio=increase",
                f"crop={width}:{height}",
                "setsar=1",
                "fps=24",
                "format=yuv420p",
                "setpts=PTS-STARTPTS",
            ]
            if shot.display_overlay is not None:
                ass_name = f"overlay_{idx}.ass"
                write_fixed_overlay_ass(
                    shot.display_overlay, clip_dur, width, height, work / ass_name
                )
                vf_parts.append(f"ass=filename={ass_name}")

            af_parts = ["aresample=48000"]
            if probe["has_audio"] and apply_loudnorm:
                af_parts.append("loudnorm=I=-23:TP=-1:LRA=11,aresample=48000")
            af_parts.append(f"apad=whole_dur={clip_dur:.6f}")
            af_parts.append("asetpts=PTS-STARTPTS")

            seg_name = f"segment_{idx:04d}.mov"
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src)]
            if not probe["has_audio"]:
                cmd += ["-f", "lavfi", "-i", "anullsrc=cl=stereo:r=48000"]

            cmd += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0" if probe["has_audio"] else "1:a:0",
                "-vf",
                ",".join(vf_parts),
                "-af",
                ",".join(af_parts),
                "-t",
                f"{clip_dur:.6f}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                seg_name,
            ]
            _run_cmd(cmd, cwd=work)
            segment_names.append(seg_name)

        concat_list = work / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{name}'\n" for name in segment_names),
            encoding="utf-8",
        )
        assembled = work / "assembled.mp4"
        _run_cmd(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                "concat.txt",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(assembled),
            ],
            cwd=work,
        )
        assembled.replace(destination)

    qc_report = run_export_qc(destination, project.shots, settings, expected_size=size)
    return destination, qc_report
