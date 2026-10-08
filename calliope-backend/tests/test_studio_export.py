import array
import asyncio
import copy
import importlib
import json
import math
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def export_module():
    return importlib.import_module("calliope.studio.export")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required")
    directory = tmp_path_factory.mktemp("prises d'été")
    native = directory / "prise bleue d'été.mp4"
    silent = directory / "prise rouge sans son.mp4"
    for path, color, duration, audio in [
        (native, "blue", "1.25", True),
        (silent, "red", "0.75", False),
    ]:
        command = [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=160x90:r=24:d={duration}",
        ]
        if audio:
            command += [
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
            ]
        command += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(path),
        ]
        subprocess.run(command, check=True, capture_output=True)
    return native, silent


def snapshot_for(*clips, aspect_ratio="1:1"):
    return {
        "aspect_ratio": aspect_ratio,
        "clips": [
            {
                "clip_id": index + 1, "take_id": index + 11,
                "path": str(path), "duration_sec": duration, "audio_mode": audio_mode,
            }
            for index, (path, duration, audio_mode) in enumerate(clips)
        ],
    }


def probe_file(path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
        str(path),
    ], check=True, capture_output=True)
    return json.loads(result.stdout)


def audio_rms(path, start, duration):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
        "-t", str(duration), "-vn", "-ac", "1", "-ar", "8000",
        "-f", "s16le", "pipe:1",
    ], check=True, capture_output=True)
    samples = array.array("h", result.stdout)
    assert len(samples) > 100
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def test_inspect_media_reports_real_video_and_audio(export_module, media):
    native, silent = media
    assert export_module.inspect_media(native) == {
        "width": 160, "height": 90, "fps": 24.0, "duration_sec": 1.25,
        "has_audio": True, "has_video": True,
    }
    result = export_module.inspect_media(silent)
    assert result["has_audio"] is False
    assert result["duration_sec"] == 0.75


def test_inspect_media_prefers_video_duration(export_module, media, tmp_path):
    native, _ = media
    destination = tmp_path / "long_audio.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(native), "-f", "lavfi", "-i",
        "sine=frequency=220:duration=2", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", str(destination),
    ], check=True, capture_output=True)
    assert float(probe_file(destination)["format"]["duration"]) >= 2
    assert export_module.inspect_media(destination)["duration_sec"] == 1.25


def test_inspect_media_rejects_truncated_faststart_mp4(export_module, media, tmp_path):
    native, _ = media
    destination = tmp_path / "truncated.mp4"
    data = native.read_bytes()
    destination.write_bytes(data[: len(data) * 3 // 4])
    with pytest.raises(ValueError):
        export_module.inspect_media(destination)


def test_inspect_media_rejects_audio_only(export_module, tmp_path):
    destination = tmp_path / "audio.m4a"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.1", "-c:a", "aac", str(destination),
    ], check=True, capture_output=True)
    with pytest.raises(ValueError):
        export_module.inspect_media(destination)


async def test_square_export_preserves_full_takes_and_audio_modes(export_module, media, tmp_path):
    native, silent = media
    snapshot = snapshot_for(
        (native, 0.25, "native"),
        (silent, 0.25, "native"),
        (native, 0.25, "silent"),
    )
    original = copy.deepcopy(snapshot)
    destination = tmp_path / "film d'été avec espaces.mp4"
    events = []

    async def progress(event):
        events.append(event)

    result = await export_module.assemble(snapshot, destination, progress_callback=progress)
    assert snapshot == original
    assert result["width"] == result["height"]
    # Cadence imposée à 24 i/s par le filtre vf ; la moyenne ffprobe sur des
    # segments très courts varie légèrement selon la version d'ffmpeg.
    assert result["fps"] == pytest.approx(24.0, abs=1.0)
    assert result["clip_count"] == 3
    assert result["has_audio"] is True
    assert result["duration_sec"] == pytest.approx(3.25, abs=0.05)
    streams = probe_file(destination)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac"
    assert audio["sample_rate"] == "48000"
    assert audio["channels"] == 2
    assert float(audio["duration"]) == pytest.approx(3.25, abs=0.08)
    assert audio_rms(destination, 0.85, 0.3) > 1000
    assert audio_rms(destination, 1.4, 0.4) < 10
    assert audio_rms(destination, 2.2, 0.7) < 10
    pixel = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", "1.6", "-i", str(destination),
        "-frames:v", "1", "-vf", "crop=2:2:iw/2:ih/2", "-pix_fmt", "rgb24",
        "-f", "rawvideo", "pipe:1",
    ], check=True, capture_output=True).stdout
    assert pixel[0] > 200 and pixel[1] < 30 and pixel[2] < 30
    data = destination.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
    assert events[0]["progress"] == 0
    assert events[-1]["progress"] == 1
    assert all(0 <= event["progress"] <= 1 and event["message"] for event in events)
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("aspect_ratio,ratio", [("16:9", 16 / 9), ("9:16", 9 / 16)])
async def test_export_orientation(export_module, media, tmp_path, aspect_ratio, ratio):
    _, silent = media
    destination = tmp_path / "film.mp4"
    result = await export_module.assemble(
        snapshot_for((silent, 0.75, "native"), aspect_ratio=aspect_ratio), destination,
    )
    assert result["width"] / result["height"] == pytest.approx(ratio)
    assert audio_rms(destination, 0.1, 0.3) < 10


async def test_export_freezes_snapshot_before_awaiting_progress(export_module, media, tmp_path):
    _, silent = media
    snapshot = snapshot_for((silent, 0.75, "native"))

    async def progress(event):
        snapshot["aspect_ratio"] = "9:16"
        snapshot["clips"].clear()

    result = await export_module.assemble(
        snapshot, tmp_path / "film.mp4", progress_callback=progress,
    )
    assert result["clip_count"] == 1
    assert result["width"] == result["height"]


async def test_missing_clip_leaves_destination_untouched(export_module, tmp_path):
    destination = tmp_path / "existing.mp4"
    destination.write_bytes(b"existing output")
    with pytest.raises(ValueError):
        await export_module.assemble(
            snapshot_for((tmp_path / "missing.mp4", 1, "native")), destination,
        )
    assert destination.read_bytes() == b"existing output"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("existing", [False, True])
async def test_cancel_stops_encoding_and_removes_partial_files(
    export_module, media, tmp_path, existing,
):
    native, _ = media
    long_video = native.parent / "long_cancel.mp4"
    if not long_video.exists():
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            "testsrc2=s=320x180:r=24:d=8", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(long_video),
        ], check=True, capture_output=True)
    destination = tmp_path / "cancelled.mp4"
    if existing:
        destination.write_bytes(b"previous completed export")
    cancel = False

    async def progress(event):
        nonlocal cancel
        if 0 < event["progress"] < 0.8:
            cancel = True

    with pytest.raises(InterruptedError):
        await asyncio.wait_for(export_module.assemble(
            snapshot_for((long_video, 8, "native")), destination,
            progress_callback=progress, cancel_check=lambda: cancel,
        ), timeout=5)
    assert cancel
    if existing:
        assert destination.read_bytes() == b"previous completed export"
        assert list(tmp_path.iterdir()) == [destination]
    else:
        assert not destination.exists()
        assert list(tmp_path.iterdir()) == []
