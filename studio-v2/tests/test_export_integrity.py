"""Intégrité du montage final : ordre, durée, doublons et export noir refusé."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import V2Settings  # noqa: E402
from core.export import export_project_mp4  # noqa: E402
from core.scenario import Character, Project, Shot  # noqa: E402
from qc.media import ffmpeg_exe, inspect_media  # noqa: E402
from qc.runner import run_export_qc  # noqa: E402


def _clip(path: Path, duration_s: float, color: str = "navy", freq: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=768x1344:r=24:d={duration_s}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={duration_s}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _project(paths: list[Path], durations: list[float]) -> Project:
    return Project(
        id="export-integrity",
        idea="Un citron vérifie un montage.",
        target_duration_s=sum(durations),
        characters=[Character(id="zeste", name="Zeste", description="Yellow lemon")],
        shots=[
            Shot(id=f"s{index + 1}", duration_s=duration, action="Zeste looks at the counter.",
                 initial_state="Zeste faces the counter.", rendered_video_ref=str(path))
            for index, (path, duration) in enumerate(zip(paths, durations))
        ],
    )


def test_multishot_export_keeps_order_duration_and_vertical_format(tmp_path):
    settings = V2Settings(data_dir=tmp_path / "data", ocr_enabled=False, whisper_model_or_path="")
    settings.ensure_dirs()
    clips = [
        _clip(tmp_path / "a.mp4", 3.0, "navy", 440),
        _clip(tmp_path / "b.mp4", 3.0, "maroon", 520),
        _clip(tmp_path / "c.mp4", 3.0, "teal", 600),
    ]
    project = _project(clips, [3.0, 3.0, 3.0])

    output, qc = export_project_mp4(project, settings)

    media = inspect_media(output)
    assert (media["width"], media["height"]) == (1080, 1920)
    assert media["has_audio"] and media["sample_rate"] == 48000 and media["channels"] == 2
    assert abs(media["duration_s"] - 9.0) < 0.6
    assert qc["ok"] is True, qc
    assert not any("dupliqué" in error for error in qc["errors"])


def test_same_clip_used_by_two_shots_is_refused_before_ffmpeg(tmp_path):
    settings = V2Settings(data_dir=tmp_path / "data", ocr_enabled=False, whisper_model_or_path="")
    settings.ensure_dirs()
    shared = _clip(tmp_path / "shared.mp4", 2.0)
    project = _project([shared, shared], [2.0, 2.0])

    with pytest.raises(ValueError, match="même fichier"):
        export_project_mp4(project, settings)
    assert not list(settings.exports_dir.glob("*.mp4"))


def test_all_black_export_is_rejected_and_dark_scene_is_not_flagged(tmp_path):
    settings = V2Settings(data_dir=tmp_path / "data", ocr_enabled=False, whisper_model_or_path="")
    settings.ensure_dirs()
    black = _clip(tmp_path / "black.mp4", 2.0, "black")
    project = _project([black], [2.0])

    export, _ = export_project_mp4(project, settings)
    qc = run_export_qc(export, project.shots, settings, expected_size=(1080, 1920))
    assert any("entièrement noir" in error for error in qc["errors"])
    assert qc["ok"] is False


def test_dark_but_valid_scene_has_no_black_error(tmp_path):
    settings = V2Settings(data_dir=tmp_path / "data", ocr_enabled=False, whisper_model_or_path="")
    settings.ensure_dirs()
    navy = _clip(tmp_path / "navy.mp4", 2.0, "navy")
    project = _project([navy], [2.0])

    export, _ = export_project_mp4(project, settings)
    qc = run_export_qc(export, project.shots, settings, expected_size=(1080, 1920))
    assert not any("noir" in error for error in qc["errors"])
    assert qc["ok"] is True, qc
