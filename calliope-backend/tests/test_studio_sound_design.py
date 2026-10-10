import array
import math
import shutil
import subprocess
import wave

import pytest

from calliope.studio import audio


def samples(path, start=0, duration=None, frequency_filter=None):
    command = ["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path)]
    if duration is not None:
        command += ["-t", str(duration)]
    if frequency_filter:
        command += ["-af", frequency_filter]
    result = subprocess.run(
        command + ["-vn", "-ac", "1", "-ar", "24000", "-f", "f32le", "pipe:1"],
        check=True, capture_output=True,
    )
    return array.array("f", result.stdout)


def rms(values):
    return math.sqrt(sum(value * value for value in values) / len(values))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_ducking_preserves_an_audible_bed_and_does_not_change_the_voice(tmp_path):
    source, music = tmp_path / "voice.mov", tmp_path / "imported.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=160x90:r=24:d=6", "-f", "lavfi", "-i",
        "sine=frequency=3000:duration=6,volume=4,volume=0:enable='not(between(t,1,3))'",
        "-c:v", "libx264", "-c:a", "pcm_s16le", str(source),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=220:duration=6", str(music),
    ], check=True, capture_output=True)
    outputs = []
    for ducking in (False, True):
        config = audio.AudioSettings(
            music="none", voice_gain_db=0, music_gain_db=-12, ducking=ducking,
        ).model_dump() | {"music_path": str(music)}
        destination = tmp_path / f"mix-{ducking}.mp4"
        subprocess.run(audio.mix_command(
            ["ffmpeg", "-v", "error", "-y"], source, config, [6], tmp_path, destination,
        ), check=True, capture_output=True)
        outputs.append(destination)
    plain, ducked = outputs
    bed_filter = "lowpass=f=400,lowpass=f=400"
    under_voice = rms(samples(ducked, 1.5, 1, bed_filter))
    reference = rms(samples(plain, 1.5, 1, bed_filter))
    assert 0.33 < under_voice / reference < 0.75
    assert rms(samples(ducked, 4, 0.4, bed_filter)) == pytest.approx(
        rms(samples(plain, 4, 0.4, bed_filter)), rel=0.1,
    )
    assert rms(samples(ducked, 1.5, 1, "highpass=f=2000")) == pytest.approx(
        rms(samples(plain, 1.5, 1, "highpass=f=2000")), rel=0.02,
    )


@pytest.mark.parametrize("effect", ["pop", "boing", "scratch"])
def test_comic_cues_are_timed_and_have_a_predictable_peak(tmp_path, effect):
    config = audio.AudioSettings(music="none", sfx=[{
        "effect": effect, "shot_index": 1, "at_sec": 0.25, "gain_db": -18,
    }]).model_dump()
    _, path = audio.render_stems(config, [1, 1], tmp_path)
    with wave.open(str(path)) as stream:
        rate = stream.getframerate()
        values = array.array("h", stream.readframes(stream.getnframes()))
    onset = next(i for i, value in enumerate(values) if value) / rate
    assert onset == pytest.approx(1.25, abs=0.001)
    peak_db = 20 * math.log10(max(abs(value) for value in values) / 32768)
    assert peak_db == pytest.approx(-19, abs=0.5)
    assert not any(values[int(1.9 * rate):])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_energetic_music_contains_rhythmic_percussion(tmp_path):
    path, _ = audio.render_stems({"music": "energetic", "tempo": 110}, [8], tmp_path)
    percussion = samples(path, 1.5, 4, "highpass=f=2500,highpass=f=2500")
    assert rms(percussion) > 0.005
    assert rms(samples(path, 1.5, 4)) > 0.1


def test_series_sound_identity_reaches_the_plan_without_overriding_explicit_levels():
    from calliope.studio import series_planner
    from tests.test_studio_qualification import context, exchange

    config = context() | {"directing_profile": {
        "music_style": "playful", "music_gain_db": -11,
    }}
    plan = {"scenes": [{"shots": [exchange()]}], "audio": {"music": "tension"}}
    result = series_planner.normalize_plan(plan, config)
    assert result["audio"]["music"] == "playful"
    assert result["audio"]["music_gain_db"] == -11
