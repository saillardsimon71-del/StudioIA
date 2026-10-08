"""Chantier rythme : durées variables par plan, trim utile, validation dialogue
tolérante, sound design synchronisé sur la grille 24 fps et zéro rerender
inutile."""
import array
import asyncio
import base64
import json
import math
import shutil
import subprocess
import wave

import pytest

from calliope.queue.manager import queue_manager
from calliope.studio import pacing
from tests.test_studio import new_board, png_bytes

FFMPEG_OK = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
requires_ffmpeg = pytest.mark.skipif(not FFMPEG_OK, reason="FFmpeg et ffprobe requis")


def talk_shot(**overrides):
    shot = {
        "description": "Le gardien répond.",
        "prompt": "A keeper answers the radio.",
        "image_prompt": "Keeper at his desk.",
        "dialogue": "Un deux trois.",
        "speaker": "keeper",
        "character_keys": ["keeper"],
        "location_key": "",
        "reference_key": "keeper-at-radio",
        "duration_sec": 2.1,
        "audio_mode": "native",
    }
    shot.update(overrides)
    return shot


def beat_plan(durations, **shot_overrides):
    return {
        "title": "Le signal",
        "summary": "Le gardien entend son nom.",
        "events": ["La radio appelle le gardien."],
        "ongoing_plot": "Qui appelle ?",
        "bible": {"universe": "Un phare isolé."},
        "entities": [
            {
                "key": "keeper",
                "kind": "character",
                "name": "Le gardien",
                "description": "Gardien adulte en ciré jaune.",
            }
        ],
        "scenes": [
            {
                "heading": "INT. PHARE",
                "action": "La radio crépite.",
                "shots": [talk_shot(duration_sec=d, **shot_overrides) for d in durations],
            }
        ],
        "audio": {"music": "none", "enabled": True},
    }


def normalize(plan, context=None):
    import copy as _copy

    from calliope.studio.series_planner import normalize_plan

    return normalize_plan(_copy.deepcopy(plan), context or {"entities": []})


# ---------------------------------------------------------------- durations


def test_variable_shot_durations_are_preserved_and_total_checked():
    beats = [1.6, 3.4, 2.1, 4.8]
    result = normalize(
        beat_plan(beats, dialogue="", speaker="", character_keys=[]),
        {"entities": [], "target_seconds": 12},
    )
    durations = [shot["duration_sec"] for shot in result["scenes"][0]["shots"]]
    # Aucun remappage sur 5 s : la durée utile écrite est respectée à 0,1 s près.
    assert durations == beats
    assert sum(durations) == pytest.approx(11.9)


def test_pack_of_free_durations_hits_the_65_second_format():
    # Le format 65 s est une vraie contrainte : la somme du contenu utile doit
    # rester proche, mais le découpage (nombre et durée des plans) reste libre.
    beats = [1.6, 3.4, 2.1, 4.8, 2.9, 5.2, 1.8, 2.6, 4.1, 3.3,
             2.4, 1.9, 5.6, 3.1, 2.2, 4.4, 1.7, 3.8, 2.7, 5.0, 1.5, 2.3]
    result = normalize(
        beat_plan(beats, dialogue="", speaker="", character_keys=[]),
        {"entities": [], "target_seconds": 65},
    )
    assert sum(s["duration_sec"] for s in result["scenes"][0]["shots"]) == pytest.approx(
        68.4
    )
    with pytest.raises(ValueError, match="durée utile|trop courte"):
        normalize(beat_plan([5.0] * 4), {"entities": [], "target_seconds": 65})
    # 60 s de contenu utile ne suffit pas pour un format qui demande 65 s : le
    # montage n'étirant jamais les plans, l'épisode sortirait sous le format.
    with pytest.raises(ValueError, match="trop courte"):
        normalize(beat_plan([2.4] * 25), {"entities": [], "target_seconds": 65})
    # Une proche marge de garde (décimales, grille 24 fps) reste acceptée : ce
    # n'est pas une règle narrative, seul l'écart manifeste est réparé.
    close = normalize(
        beat_plan([5.3] * 12, dialogue="", speaker="", character_keys=[]),
        {"entities": [], "target_seconds": 65},
    )
    assert len(close["scenes"][0]["shots"]) == 12


def test_planner_rejects_unfilmable_durations_and_bloated_episodes():
    with pytest.raises(ValueError, match="entre 1 et 15"):
        normalize(beat_plan([0.4], dialogue="", speaker="", character_keys=[]))
    with pytest.raises(ValueError, match="entre 1 et 15"):
        normalize(beat_plan([20.0], dialogue="", speaker="", character_keys=[]))
    with pytest.raises(ValueError, match="40 plans"):
        normalize(beat_plan([1.5] * 41, dialogue="", speaker="", character_keys=[]))


def test_a_line_must_fit_its_own_seconds_not_a_fixed_block():
    too_long = beat_plan([1.6], dialogue=" ".join(["mot"] * 5))
    with pytest.raises(ValueError, match="trop longue"):
        normalize(too_long)
    fits = normalize(beat_plan([2.1], dialogue=" ".join(["mot"] * 4)))
    assert fits["scenes"][0]["shots"][0]["duration_sec"] == 2.1


# ------------------------------------------------------------------ fenêtre


def test_useful_window_trims_the_tail_on_the_24fps_grid():
    # Le cas nominal du chantier : un clip H3 de 5,17 s ramené à 1,6 s utiles.
    start, end = pacing.useful_window(1.6, 5.1667, None)
    assert start == 0.0
    assert end == pytest.approx(1.5833333)
    assert (end - start) * 24 == 38  # un nombre entier de frames


def test_useful_window_never_pads_a_short_take():
    probe = 2.0
    start, end = pacing.useful_window(5.0, probe, None)
    assert (start, end) == (0.0, probe)


def test_useful_window_covers_detected_speech_wherever_it_starts():
    voice = [(2.02, 3.2)]
    start, end = pacing.useful_window(1.6, 7.0, voice)
    assert start <= 2.02 <= end
    assert end - start == pytest.approx(pacing.quantize(1.6))
    assert end <= 7.0


def test_useful_window_never_cuts_speech_beyond_the_beat():
    voice = [(0.4, 4.6)]
    start, end = pacing.useful_window(2.0, 5.1667, voice)
    assert end >= 4.6
    assert end <= pacing.floor_grid(5.1667) + 1e-9
    assert start <= 0.4


def test_explicit_trim_points_are_aligned_and_validated():
    assert pacing.explicit_window(None, None, 5.1667) == (0.0, 5.1667)
    start, end = pacing.explicit_window(1.03, 3.0, 5.1667)
    assert start * 24 == round(start * 24)
    assert 1.0 <= start <= 1.05
    assert end == 3.0
    assert pacing.explicit_window(None, 9.0, 5.0) == (0.0, 5.0)
    with pytest.raises(ValueError, match="coupe"):
        pacing.explicit_window(3.0, 2.0, 5.0)
    with pytest.raises(ValueError, match="coupe"):
        pacing.explicit_window(6.0, None, 5.0)


# ----------------------------------------------------------------- dialogue


def test_dialogue_validation_tolerates_small_imperfections():
    dialogue = " ".join(["mot"] * 3)  # ~1,4 s de parole attendue
    expected_window = 3 / 2.2
    # Début de parole légèrement tardif : toléré (le trim rattrape).
    voice = {"intervals": [(1.2, 2.6)], "max_db": -12.0}
    assert pacing.assess_dialogue(dialogue, 1.6, 5.17, voice)["status"] == "ok"
    # Silence court avant la réplique : toléré, jamais un motif de rejet.
    voice = {"intervals": [(0.0, 1.4)], "max_db": -14.0}
    assert pacing.assess_dialogue(dialogue, 1.6, 5.17, voice)["status"] == "ok"
    # Quelques frames utiles en plus : toléré.
    voice = {"intervals": [(0.1, 1.7)], "max_db": -14.0}
    assert pacing.assess_dialogue(dialogue, 1.6, 5.17, voice)["status"] == "ok"
    assert expected_window > 1
    # Mesure impossible : permissif, on ne bloque pas sur du doute.
    assert pacing.assess_dialogue(dialogue, 1.6, 5.17, None)["status"] == "unknown"


def test_dialogue_validation_flags_only_manifest_failures():
    dialogue = " ".join(["mot"] * 3)
    no_speech = {"intervals": [], "max_db": -20.0}
    assert pacing.assess_dialogue(dialogue, 1.6, 5.17, no_speech)["status"] == "missing"
    dead_audio = {"intervals": [], "max_db": -60.0}
    assert pacing.assess_dialogue(dialogue, 1.6, 5.17, dead_audio)["status"] == "inaudible"
    invented = {"intervals": [(0.0, 6.0)], "max_db": -12.0}
    assert pacing.assess_dialogue(dialogue, 1.6, 7.2, invented)["status"] == "extra"


@requires_ffmpeg
def test_voice_detection_measures_a_real_take(tmp_path):
    path = tmp_path / "prise.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=7",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=7,volume=0:enable='not(between(t,2,3.2))'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    voice = pacing.detect_voice(path, 7.0)
    assert voice and voice["max_db"] > -30
    begin, end = voice["intervals"][0]
    assert begin == pytest.approx(2.0, abs=0.1)
    assert end == pytest.approx(3.2, abs=0.1)


# ------------------------------------------------------------------ montage


def make_clip(path, seconds, color="blue", tone_windows=(), tone_hz=440):
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=24:d={seconds}",
    ]
    if tone_windows:
        enable = "+".join(f"between(t,{a},{b})" for a, b in tone_windows)
        command += [
            "-f", "lavfi", "-i",
            f"sine=frequency={tone_hz}:duration={seconds},volume=0:enable='not({enable})'",
            "-c:a", "aac", "-ar", "48000",
        ]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True, capture_output=True)
    return path


def edit_snapshot(clips, **overrides):
    snapshot = {"aspect_ratio": "1:1", "clips": []}
    for index, clip in enumerate(clips):
        entry = {
            "clip_id": index + 1,
            "take_id": index + 11,
            "path": str(clip["path"]),
            "duration_sec": clip["seconds"],
            "beat_sec": clip.get("beat"),
            "dialogue": clip.get("dialogue", ""),
            "trim_start_sec": clip.get("trim_start"),
            "trim_end_sec": clip.get("trim_end"),
            "audio_mode": clip.get("audio_mode", "silent"),
        }
        snapshot["clips"].append(entry)
    snapshot.update(overrides)
    return snapshot


def audio_rms(path, start, duration, lowpass=None):
    command = [
        "ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
        "-t", str(duration), "-vn", "-ac", "1", "-ar", "8000",
    ]
    if lowpass:
        command += ["-af", f"lowpass=f={lowpass}"]
    command += ["-f", "s16le", "pipe:1"]
    result = subprocess.run(command, check=True, capture_output=True)
    samples = array.array("h", result.stdout)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


@requires_ffmpeg
async def test_export_trims_each_take_to_its_useful_beat(tmp_path):
    from calliope.studio import export

    clips = [
        make_clip(tmp_path / f"plan-{i}.mp4", 5.1667, color)
        for i, color in enumerate(("blue", "red", "green"))
    ]
    beats = [1.6, 3.4, 2.1]
    snapshot = edit_snapshot(
        [dict(path=p, seconds=5.1667, beat=b) for p, b in zip(clips, beats)]
    )
    result = await export.assemble(snapshot, tmp_path / "film.mp4")
    expect = sum(pacing.quantize(b) for b in beats)
    assert result["duration_sec"] == pytest.approx(expect, abs=0.05)
    # Jamais 15,5 s : personne n'a gardé les prises entières.
    assert result["duration_sec"] < 8
    # Chaque segment est recalé à 24 images/s (vf fps=24) ; avg_frame_rate du
    # conteneur peut dériver légèrement sur des segments très courts.
    assert result["fps"] == pytest.approx(24, abs=1.0)
    assert result["grid_fps"] == 24
    assert result["cuts_sec"] == [
        [0.0, round(pacing.quantize(b), 4)] for b in beats
    ]


@requires_ffmpeg
async def test_export_never_pads_a_take_shorter_than_its_beat(tmp_path):
    from calliope.studio import export

    clip = make_clip(tmp_path / "court.mp4", 2.0)
    snapshot = edit_snapshot([dict(path=clip, seconds=2.0, beat=5.0)])
    result = await export.assemble(snapshot, tmp_path / "film.mp4")
    assert result["duration_sec"] == pytest.approx(2.0, abs=0.05)
    assert result["cuts_sec"] == [[0.0, 2.0]]


@requires_ffmpeg
async def test_export_honours_explicit_trim_points(tmp_path):
    from calliope.studio import export

    clip = tmp_path / "deux moitiés.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=2",
            "-f", "lavfi", "-i", "color=c=red:s=160x90:r=24:d=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
        capture_output=True,
    )
    snapshot = edit_snapshot(
        [dict(path=clip, seconds=4.0, trim_start=2.0, trim_end=3.5)]
    )
    result = await export.assemble(snapshot, tmp_path / "film.mp4")
    assert result["duration_sec"] == pytest.approx(1.5, abs=0.05)
    pixel = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(tmp_path / "film.mp4"),
            "-frames:v", "1", "-vf", "crop=2:2:iw/2:ih/2", "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert pixel[0] > 200 and pixel[1] < 30 and pixel[2] < 30


@requires_ffmpeg
async def test_dialogue_trim_stays_on_the_spoken_window(tmp_path):
    from calliope.studio import export

    clip = make_clip(tmp_path / "dialogue.mp4", 7.0, tone_windows=[(2.02, 3.2)])
    snapshot = edit_snapshot(
        [
            dict(
                path=clip,
                seconds=7.0,
                beat=1.6,
                dialogue="Un deux trois.",
                audio_mode="native",
            )
        ]
    )
    destination = tmp_path / "film.mp4"
    result = await export.assemble(snapshot, destination)
    assert result["duration_sec"] == pytest.approx(pacing.quantize(1.6), abs=0.05)
    # La parole détectée à 2,02 s dans la prise arrive au début du segment.
    assert audio_rms(destination, 0.15, 0.6) > 500


# ---------------------------------------------------------------- sound design


def stem_rms(path, start=0.0, end=None):
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        count = handle.getnframes()
        first = int(start * rate)
        last = count if end is None else min(count, int(end * rate))
        handle.setpos(first)
        raw = handle.readframes(last - first)
    samples = array.array("h", raw)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def test_cue_onsets_share_the_montage_grid(tmp_path):
    from calliope.studio import audio

    config = audio.AudioSettings(
        music="none", sfx=[{"effect": "click", "shot_index": 0, "at_sec": 0.234, "gain_db": -6}]
    ).model_dump()
    _, fx_path = audio.render_stems(config, [2.0], tmp_path)
    with wave.open(str(fx_path), "rb") as handle:
        rate = handle.getframerate()
        samples = array.array("h", handle.readframes(handle.getnframes()))
    onset = next(i for i, s in enumerate(samples) if s != 0) / rate
    # 0,234 s demandé n'existe pas sur la grille : le SFX part à la frame 6 (0,25 s).
    assert onset == pytest.approx(0.25, abs=1 / rate)


def test_every_ambience_preset_and_tension_cue_renders(tmp_path):
    from calliope.studio import audio

    for preset in ("room", "wind", "rain", "cafe", "night", "city", "forest"):
        config = audio.AudioSettings(music="none", ambience=preset).model_dump()
        _, fx_path = audio.render_stems(config, [1.0], tmp_path)
        assert stem_rms(fx_path) > 0.1, preset
    config = audio.AudioSettings(
        music="none",
        sfx=[{"effect": "riser", "shot_index": 0, "at_sec": 0.0, "gain_db": -12}],
    ).model_dump()
    _, fx_path = audio.render_stems(config, [2.4], tmp_path)
    early = stem_rms(fx_path, 0.1, 0.6)
    late = stem_rms(fx_path, 1.6, 2.1)
    assert early > 0 and late > early * 2  # la tension monte réellement


def test_a_hold_cuts_music_and_bed_but_never_the_voices(tmp_path):
    from calliope.studio import audio

    config = audio.AudioSettings(
        music="neutral",
        music_gain_db=-10,
        ambience="rain",
        ambience_gain_db=-16,
        holds=[{"shot_index": 0, "at_sec": 0.5, "duration_sec": 1.0, "drop_db": -36}],
    ).model_dump()
    music_path, fx_path = audio.render_stems(config, [2.4], tmp_path)
    before = stem_rms(music_path, 0.0, 0.4)
    during = stem_rms(music_path, 0.55, 1.45)
    after = stem_rms(music_path, 1.7, 2.3)
    assert during < before * 0.15
    assert after > during * 3
    fx_before = stem_rms(fx_path, 0.0, 0.4)
    fx_during = stem_rms(fx_path, 0.55, 1.45)
    assert fx_during < fx_before * 0.15


def test_audio_settings_validate_holds_and_new_names():
    from pydantic import ValidationError

    from calliope.studio import audio

    config = audio.AudioSettings(ambience="forest", sfx=[{"effect": "boom"}], holds=[])
    assert config.ambience == "forest"
    with pytest.raises(ValidationError):
        audio.AudioSettings(ambience="spaceship")
    with pytest.raises(ValidationError):
        audio.AudioSettings(sfx=[{"effect": "explosion"}])
    with pytest.raises(ValidationError):
        audio.AudioSettings(holds=[{"duration_sec": 0}])
    # Les anciens réglages sans holds restent valables.
    assert audio.AudioSettings.model_validate({"music": "warm"}).holds == []


@requires_ffmpeg
async def test_ducking_lowers_the_bed_under_voices(tmp_path):
    from calliope.studio import audio, export

    source = tmp_path / "voix.mov"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=4",
            "-f", "lavfi", "-i",
            "sine=frequency=3000:duration=4,volume=0:enable='not(between(t,0,2))'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    async def mixed(ducking):
        config = audio.AudioSettings(
            music="neutral", music_gain_db=-16, voice_gain_db=0, ducking=ducking
        ).model_dump()
        ready = tmp_path / f"mix-{ducking}.mp4"
        base = ["ffmpeg", "-v", "error", "-nostdin", "-nostats", "-y"]
        command = await asyncio.to_thread(
            audio.mix_command, base, source, config, [4.0], tmp_path, ready
        )
        await export._run_ffmpeg(command, 4.0, 0, 1, "mix", None, None)
        return ready

    ducked = await mixed(True)
    plain = await mixed(False)
    # Bande des graves (le lit musical) : le ducking le baisse sous la voix.
    ducked_voice = audio_rms(ducked, 0.4, 1.2, lowpass=700)
    plain_voice = audio_rms(plain, 0.4, 1.2, lowpass=700)
    assert ducked_voice < plain_voice * 0.75
    # Hors voix, le lit reste au même niveau : le ducking n'écrase pas tout.
    ducked_free = audio_rms(ducked, 2.5, 1.2, lowpass=700)
    plain_free = audio_rms(plain, 2.5, 1.2, lowpass=700)
    assert ducked_free == pytest.approx(plain_free, rel=0.15)
    # La voix domine toujours le mix : musique et SFX ne masquent jamais les voix.
    assert audio_rms(ducked, 0.4, 1.2) > ducked_voice * 8


# ---------------------------------------------------------------- production


@pytest.fixture(autouse=True)
def isolated_production(monkeypatch, client):
    previous = queue_manager.paused
    queue_manager.paused = True
    monkeypatch.setattr("calliope.config.settings.dry_run", False)
    monkeypatch.setattr(
        "calliope.config.settings.llm_base_url", "http://localhost:9001/v1"
    )
    monkeypatch.setattr(
        "calliope.config.settings.studio_image_base_url", "http://localhost:9002/v1"
    )
    yield
    queue_manager.paused = previous


def produce(client, monkeypatch, tmp_path, plan, clip_bytes):
    from calliope.engines.modal_h3 import ModalH3Engine
    from calliope.studio import production, service
    from tests.test_studio_series import episode, series

    spawns = []

    async def image(request):
        return service.normalized_image(png_bytes())

    monkeypatch.setattr(production, "plan_episode", plan)
    monkeypatch.setattr(service, "_generate_image", image)
    monkeypatch.setattr(
        ModalH3Engine,
        "spawn",
        lambda self, request: spawns.append(request) or f"call-{len(spawns)}",
    )
    monkeypatch.setattr(
        ModalH3Engine,
        "poll",
        lambda self, call_id: {
            "video_base64": base64.b64encode(clip_bytes).decode(),
            "metadata": {},
        },
    )
    created = episode(client, series(client)["id"]).json()
    job = queue_manager.get_job(created["job_id"])
    asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
    return created["project_id"], spawns


@requires_ffmpeg
def test_a_good_take_is_approved_without_rerender_and_trimmed_usefully(
    client, monkeypatch, tmp_path
):
    from calliope.studio import service

    clip = make_clip(tmp_path / "bonne prise.mp4", 5.1667, tone_windows=[(0.1, 1.5)])
    episode_plan = beat_plan([1.6])

    async def plan_result(*args):
        return episode_plan

    project_id, spawns = produce(client, monkeypatch, tmp_path, plan_result, clip.read_bytes())
    assert len(spawns) == 1  # aucun rerender GPU pour une prise exploitable
    board = service.board(project_id)
    shot = board["scenes"][0]["shots"][0]
    assert shot["approved"] is True
    # La fenêtre utile a été enregistrée : ~1,58 s conservés sur 5,17 s.
    assert shot["trim_start_sec"] == pytest.approx(0.0, abs=1e-6)
    assert shot["trim_end_sec"] == pytest.approx(pacing.quantize(1.6), abs=0.05)
    exports = board["exports"]
    assert exports and exports[0]["url"]
    assert exports[0]["metadata"]["duration_sec"] == pytest.approx(
        pacing.quantize(1.6), abs=0.05
    )


@requires_ffmpeg
def test_one_retry_on_manifest_dialogue_failure_then_editing_goes_on(
    client, monkeypatch, tmp_path
):
    from calliope.studio import service

    silent = tmp_path / "prise muette.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=5.1667",
            "-f", "lavfi", "-i", "aevalsrc=0:d=5.1667",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            "-shortest", str(silent),
        ],
        check=True,
        capture_output=True,
    )
    episode_plan = beat_plan([1.6])

    async def plan_result(*args):
        return episode_plan

    project_id, spawns = produce(
        client, monkeypatch, tmp_path, plan_result, silent.read_bytes()
    )
    # Échec manifeste deux fois de suite : une relance, pas une boucle GPU.
    assert len(spawns) == 2
    board = service.board(project_id)
    assert board["production"]["status"] == "done"
    assert board["scenes"][0]["shots"][0]["approved"] is True
    assert board["exports"]


# ------------------------------------------------------------------- routes


def test_shot_routes_accept_free_durations_and_trim_points(client):
    project_id, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    response = client.patch(
        f"/api/studio/projects/{project_id}/shots/{shot['id']}",
        json={"duration_sec": 1.6},
    )
    assert response.status_code == 200, response.text
    assert response.json()["duration_sec"] == 1.6
    assert (
        client.patch(
            f"/api/studio/projects/{project_id}/shots/{shot['id']}",
            json={"duration_sec": 0.4},
        ).status_code
        == 422
    )
    response = client.patch(
        f"/api/studio/projects/{project_id}/shots/{shot['id']}",
        json={"trim_start_sec": 1.0, "trim_end_sec": 2.6},
    )
    assert response.status_code == 200, response.text
    assert response.json()["trim_start_sec"] == 1.0
    assert response.json()["trim_end_sec"] == 2.6
    assert (
        client.patch(
            f"/api/studio/projects/{project_id}/shots/{shot['id']}",
            json={"trim_start_sec": 3.0, "trim_end_sec": 2.0},
        ).status_code
        == 422
    )
    response = client.patch(
        f"/api/studio/projects/{project_id}/shots/{shot['id']}",
        json={"trim_start_sec": None, "trim_end_sec": None},
    )
    assert response.json()["trim_start_sec"] is None
    assert response.json()["trim_end_sec"] is None
    created = client.post(
        f"/api/studio/projects/{project_id}/scenes/{board['scenes'][0]['id']}/shots",
        json={"description": "Réaction brève", "duration_sec": 1.8},
    )
    assert created.status_code == 200, created.text
    assert created.json()["duration_sec"] == 1.8
