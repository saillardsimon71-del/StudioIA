"""Golden V4 : le studio réassemble le film validé à partir des prises locales.

Aucun GPU, aucun TTS, aucune écriture : les huit fichiers existent déjà. Ce
test échoue si le studio ne sait pas réutiliser les prises et sortir le format
social validé. Il est ignoré sur une machine sans les médias V4.
"""

import asyncio
from pathlib import Path

import pytest

from calliope.studio import export, pacing

ROOT = Path(__file__).resolve().parents[2]
CLIPS = [
    ROOT / "work/ep02-v4/renders/cantine/video.mp4",
    ROOT / "calliope-backend/data/assets/44/studio/takes/605/video.mp4",
    ROOT / "calliope-backend/data/assets/44/studio/takes/633/video.mp4",
    ROOT / "calliope-backend/data/assets/44/studio/takes/634/video.mp4",
    ROOT / "work/ep02-v4/renders/corridor/video.mp4",
    ROOT / "calliope-backend/data/assets/44/studio/takes/617/video.mp4",
    ROOT / "work/ep02-v4/renders/courtyard/video.mp4",
    ROOT / "work/ep02-v4/renders/cta/video.mp4",
]

pytestmark = pytest.mark.skipif(
    not all(path.is_file() for path in CLIPS),
    reason="Médias locaux V4 absents",
)


def test_golden_v4_assembles_from_existing_takes_without_gpu(tmp_path):
    snapshot = {
        "aspect_ratio": "9:16",
        "clips": [
            {
                "clip_id": index + 1,
                "scene_id": index + 1,
                "take_id": index + 1,
                "path": str(path),
                "audio_mode": "native",
            }
            for index, path in enumerate(CLIPS)
        ],
        "audio": {
            "enabled": False,
            "loudnorm": True,
            "loudness_target_lufs": -14,
            "true_peak_db": -1.5,
        },
    }
    destination = tmp_path / "EPISODE-02-V4-golden.mp4"
    result = asyncio.run(export.assemble(snapshot, destination))
    assert result["clip_count"] == 8
    assert result["width"] == 1080 and result["height"] == 1920
    assert result["duration_sec"] == pytest.approx(60.667, abs=0.2)
    audio = export.inspect_audio(destination)
    assert audio["sample_rate"] == 48000 and audio["channels"] == 2
    assert audio["audio_duration_sec"] + 0.25 >= audio["video_duration_sec"]
    loudness = pacing.measure_loudness(destination)
    assert loudness is not None
    assert abs(loudness["integrated_lufs"] + 14) <= 1.5
