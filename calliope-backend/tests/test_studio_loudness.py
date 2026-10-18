import shutil
import subprocess

import pytest

from calliope.studio import pacing


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg requis")
def test_measure_loudness_returns_ebu_r128_values(tmp_path):
    path = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
        capture_output=True,
    )
    result = pacing.measure_loudness(path)
    assert result is not None
    assert -40 < result["integrated_lufs"] < 0
    assert result["true_peak_dbfs"] is not None


def test_audio_settings_enable_loudness_normalization_by_default():
    from calliope.studio.audio import AudioSettings

    settings = AudioSettings()
    assert settings.loudnorm is True
    assert settings.loudness_target_lufs == -23
    assert settings.true_peak_db == -1


def test_hot_true_peak_is_not_a_dialogue_defect():
    """Un pic natif proche de 0 dBFS est normalise a l'export, pas rejoue."""
    from calliope.studio import production

    verdict = production.apply_loudness(
        {"status": "ok", "reason": ""},
        {"integrated_lufs": -11.9, "lra_lu": 20.1, "true_peak_dbfs": -0.5},
    )
    assert verdict["status"] == "ok"
    assert verdict["loudness"]["true_peak_dbfs"] == -0.5


def test_unusable_low_loudness_remains_blocking():
    from calliope.studio import production

    verdict = production.apply_loudness(
        {"status": "ok", "reason": ""},
        {"integrated_lufs": -40.0, "lra_lu": 1.0, "true_peak_dbfs": -20.0},
    )
    assert verdict["status"] == "audio_level"
    assert "trop faible" in verdict["reason"]
