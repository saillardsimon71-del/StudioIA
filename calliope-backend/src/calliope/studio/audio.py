import array
import json
import math
import random
import subprocess
import uuid
import wave
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from calliope.config import settings
from calliope.studio import service
from calliope.studio.pacing import quantize

MUSIC = ("none", "neutral", "tension", "warm", "playful", "energetic", "horror")
EFFECTS = ("click", "beep", "impact", "boom", "whoosh", "riser", "door", "footstep")
AMBIENCE = ("none", "room", "wind", "rain", "cafe", "night", "city", "forest")


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect: Literal[
        "click", "beep", "impact", "boom", "whoosh", "riser", "door", "footstep"
    ] = "click"
    shot_index: int = Field(default=0, ge=0, le=1000)
    at_sec: float = Field(default=0, ge=0, le=120)
    gain_db: float = Field(default=-24, ge=-60, le=0)
    asset_id: int | None = None


class Hold(BaseModel):
    """Silence volontaire : musique et fond sonore s'effacent brièvement, par
    exemple juste avant une révélation. Les voix ne sont jamais touchées."""

    model_config = ConfigDict(extra="forbid")
    shot_index: int = Field(default=0, ge=0, le=1000)
    at_sec: float = Field(default=0, ge=0, le=120)
    duration_sec: float = Field(default=0.8, gt=0, le=30)
    drop_db: float = Field(default=-36, ge=-90, lt=0)


class AudioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    music: Literal["none", "neutral", "tension", "warm", "playful", "energetic", "horror"] = (
        "neutral"
    )
    music_gain_db: float = Field(default=-28, ge=-60, le=0)
    voice_gain_db: float = Field(default=-5, ge=-24, le=6)
    tempo: int = Field(default=72, ge=40, le=160)
    ducking: bool = True
    ambience: Literal["none", "room", "wind", "rain", "cafe", "night", "city", "forest"] = (
        "none"
    )
    ambience_gain_db: float = Field(default=-38, ge=-60, le=0)
    music_asset_id: int | None = None
    sfx: list[Cue] = Field(default_factory=list, max_length=100)
    holds: list[Hold] = Field(default_factory=list, max_length=100)


def get_audio(conn, project_id):
    row = conn.execute(
        "SELECT settings_json FROM studio_audio WHERE project_id=?", (project_id,)
    ).fetchone()
    config = AudioSettings.model_validate(
        json.loads(row[0]) if row else {"enabled": False, "music": "none"}
    ).model_dump()
    config["assets"] = [
        dict(r) | {"url": service.media_url(r["path"])}
        for r in conn.execute(
            "SELECT * FROM studio_audio_assets WHERE project_id=? ORDER BY id",
            (project_id,),
        )
    ]
    return config


def snapshot_audio(conn, project_id):
    config = get_audio(conn, project_id)
    assets = {item["id"]: item for item in config.pop("assets")}
    config["music_path"] = (
        assets[config["music_asset_id"]]["path"] if config["music_asset_id"] else None
    )
    for cue in config["sfx"]:
        cue["path"] = assets[cue["asset_id"]]["path"] if cue["asset_id"] else None
    return config


def save_audio(project_id, data):
    with service.database() as conn:
        service.require_project(conn, project_id)
        current = get_audio(conn, project_id)
        current.pop("assets")
        config = AudioSettings.model_validate(current | data).model_dump()
        asset_ids = [config["music_asset_id"]] + [c["asset_id"] for c in config["sfx"]]
        for identifier in filter(None, asset_ids):
            if not conn.execute(
                "SELECT id FROM studio_audio_assets WHERE id=? AND project_id=?",
                (identifier, project_id),
            ).fetchone():
                raise HTTPException(422, "Cette piste audio n'appartient pas à cet épisode.")
        conn.execute(
            "INSERT INTO studio_audio(project_id,settings_json) VALUES(?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET settings_json=excluded.settings_json",
            (project_id, json.dumps(config)),
        )
        if config != current:
            service.touch(conn, project_id)
        conn.commit()
        return get_audio(conn, project_id)


def import_audio(project_id, name, content, kind):
    with service.database() as conn:
        service.require_project(conn, project_id)
    directory = settings.assets_dir / str(project_id) / "studio" / "audio"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + ".audio")
    path.write_bytes(content)
    try:
        from calliope.studio.export import _PROCESS_OPTIONS, _binary

        result = subprocess.run(
            [
                _binary("ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=30,
            **_PROCESS_OPTIONS,
        )
        data = json.loads(result.stdout) if result.returncode == 0 else {}
        if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
            raise ValueError("Choisissez un fichier audio lisible.")
        duration = float(data.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or not 0 < duration <= 1800:
            raise ValueError("La piste doit durer entre 0 et 1800 secondes.")
        with service.database() as conn:
            cur = conn.execute(
                (
                    "INSERT INTO studio_audio_assets(project_id,kind,name,path,duration_sec"
                    ") VALUES(?,?,?,?,?)"
                ),
                (project_id, kind, Path(name).name, str(path), duration),
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "name": Path(name).name,
                "kind": kind,
                "duration_sec": duration,
                "url": service.media_url(path),
            }
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _write_wave(path, samples, rate):
    pcm = array.array("h", (int(max(-0.95, min(0.95, value)) * 32767) for value in samples))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())


def render_stems(config, durations, directory):
    rate = 24000
    total = sum(durations)
    frames = math.ceil(total * rate)
    music = array.array("f", [0]) * frames
    effects = array.array("f", [0]) * frames
    preset = config.get("music", "none")
    if preset != "none" and not config.get("music_path"):
        roots, intervals = {
            "neutral": ([48, 53, 55, 48], [0, 7, 12]),
            "tension": ([45, 45, 41, 44], [0, 7, 14]),
            "warm": ([48, 53, 57, 55], [0, 4, 7]),
            "playful": ([60, 65, 67, 60], [0, 4, 7]),
            "energetic": ([48, 55, 57, 53], [0, 7, 12]),
            "horror": ([40, 39, 40, 38], [0, 1, 7]),
        }[preset]
        beat = 60 / config.get("tempo", 72)
        for index in range(frames):
            t = index / rate
            chord_index = int(t / (beat * 4)) % 4
            root = roots[chord_index]
            phase = (t % (beat * 4)) / (beat * 4)
            pad = (
                sum(
                    math.sin(2 * math.pi * (440 * 2 ** ((root + step - 69) / 12)) * t)
                    for step in intervals
                )
                / 3
            )
            pulse_time = t % (beat * 2)
            note = root + intervals[int(t / (beat * 2)) % len(intervals)] + 12
            pulse = math.sin(2 * math.pi * (440 * 2 ** ((note - 69) / 12)) * t) * math.exp(
                -pulse_time * 4
            )
            envelope = min(1, t / 1.2, (total - t) / 1.5)
            music[index] = (pad * (0.1 + 0.03 * math.sin(phase * math.pi)) + pulse * 0.05) * max(
                0, envelope
            )
    rng = random.Random(2718)
    ambience = config.get("ambience", "none")
    gain = 10 ** (config.get("ambience_gain_db", -38) / 20)
    if ambience != "none":
        previous = 0.0
        walk = 0.5
        next_chirp, chirp_end, chirp_freq = 0.4, -1.0, 3200.0
        for index in range(frames):
            t = index / rate
            noise = rng.uniform(-1, 1)
            if ambience == "rain":
                value = noise * 0.15
            elif ambience == "wind":
                previous = previous * 0.97 + noise * 0.03
                value = previous * (0.6 + 0.5 * math.sin(2 * math.pi * 0.07 * t + 1.3))
            elif ambience == "cafe":
                # Murmure lointain : bruit adouci dont le niveau se promène.
                previous = previous * 0.96 + noise * 0.04
                walk = min(1.0, max(0.15, walk + rng.uniform(-0.02, 0.02)))
                value = previous * (0.3 + 0.7 * walk) * 1.4
            elif ambience == "city":
                previous = previous * 0.99 + noise * 0.01
                value = previous * 1.5 + math.sin(2 * math.pi * 55 * t) * 0.03
            elif ambience == "night":
                previous = previous * 0.985 + noise * 0.015
                cricket = (
                    math.sin(2 * math.pi * 4200 * t)
                    * max(0.0, math.sin(2 * math.pi * 18 * t)) ** 8
                    * 0.06
                )
                value = previous * 0.5 + cricket
            elif ambience == "forest":
                previous = previous * 0.97 + noise * 0.03
                value = previous * 0.4
                if t >= next_chirp:
                    chirp_end = t + rng.uniform(0.1, 0.18)
                    chirp_freq = rng.uniform(2800, 3800)
                    next_chirp = t + rng.uniform(0.7, 2.6)
                if t < chirp_end:
                    value += math.sin(2 * math.pi * chirp_freq * t) * 0.05
            else:  # room
                previous = previous * 0.97 + noise * 0.03
                value = previous * 0.8
            effects[index] = value * gain
    offsets, elapsed = [], 0
    for duration in durations:
        offsets.append(elapsed)
        elapsed += duration
    for cue in config.get("sfx", []):
        if cue.get("path") or cue["shot_index"] >= len(durations):
            continue
        start = quantize(
            offsets[cue["shot_index"]] + min(cue["at_sec"], durations[cue["shot_index"]])
        )
        effect = cue["effect"]
        length = {
            "click": 0.16,
            "beep": 0.16,
            "footstep": 0.16,
            "impact": 0.65,
            "whoosh": 0.65,
            "door": 0.65,
            "boom": 0.9,
            "riser": 2.2,
        }[effect]
        scale = 10 ** (cue["gain_db"] / 20)
        for index in range(min(int(length * rate), max(0, frames - int(start * rate)))):
            t = index / rate
            noise = rng.uniform(-1, 1)
            if effect == "beep":
                value = math.sin(2 * math.pi * 880 * t) * math.sin(math.pi * t / length) * 0.4
            elif effect == "whoosh":
                value = noise * math.sin(math.pi * t / length) * 0.5
            elif effect == "riser":
                # Montée de tension : souffle filtré et tonalité qui grimpe.
                sweep = math.sin(2 * math.pi * (220 + 320 * t / length) * t)
                value = (noise * 0.25 + sweep * 0.3) * (t / length) ** 1.5
            elif effect == "boom":
                value = (math.sin(2 * math.pi * 48 * t) * 0.7 + noise * 0.3) * math.exp(-t * 7)
            elif effect in ("impact", "door", "footstep"):
                value = (math.sin(2 * math.pi * 65 * t) * 0.5 + noise * 0.2) * math.exp(-t * 14)
            else:
                value = noise * math.exp(-t * 70) * 0.5
            effects[int(start * rate) + index] += value * scale
    for hold in config.get("holds", []):
        # Silence volontaire : musique et fond sonore s'effacent avec de courtes
        # rampes ; les voix (piste native des prises) ne sont jamais touchées.
        if hold["shot_index"] >= len(durations):
            continue
        start = quantize(
            offsets[hold["shot_index"]] + min(hold["at_sec"], durations[hold["shot_index"]])
        )
        floor = 10 ** (hold["drop_db"] / 20)
        first = min(frames, max(0, int(start * rate)))
        last = min(frames, first + int(hold["duration_sec"] * rate))
        ramp = min(int(0.03 * rate), max(1, (last - first) // 4))
        for index in range(first, last):
            into = index - first
            left = last - index
            if into < ramp:
                weight = 1 - (1 - floor) * (into / ramp)
            elif left < ramp:
                weight = 1 - (1 - floor) * (left / ramp)
            else:
                weight = floor
            music[index] *= weight
            effects[index] *= weight
    music_path, fx_path = Path(directory) / "music.wav", Path(directory) / "effects.wav"
    _write_wave(music_path, music, rate)
    _write_wave(fx_path, effects, rate)
    return music_path, fx_path


def preview(project_id):
    with service.database() as conn:
        service.require_project(conn, project_id)
        config = snapshot_audio(conn, project_id)
        durations = [
            quantize(r[0] or 5)
            for r in conn.execute(
                "SELECT duration_sec FROM clips WHERE project_id=? ORDER BY id", (project_id,)
            )
        ] or [20]
    directory = (
        settings.assets_dir / str(project_id) / "studio" / "audio" / ("preview-" + uuid.uuid4().hex)
    )
    directory.mkdir(parents=True, exist_ok=True)
    music_path, fx_path = render_stems(config, durations, directory)
    return {
        "music_url": service.media_url(config.get("music_path") or music_path),
        "effects_url": service.media_url(fx_path),
    }


def mix_command(base, source, config, durations, directory, ready):
    music_path, fx_path = render_stems(config, durations, directory)
    command = base + [
        "-i",
        str(source),
        "-stream_loop",
        "-1",
        "-i",
        str(config.get("music_path") or music_path),
        "-i",
        str(fx_path),
    ]
    total = sum(durations)
    filters = [
        f"[0:a]volume={config.get('voice_gain_db', -5)}dB,asplit=2[voice][key]",
        f"[1:a]aresample=48000,atrim=duration={total:.9f},asetpts=PTS-STARTPTS,"
        f"volume={config.get('music_gain_db', -28)}dB,afade=t=in:d=1,"
        f"afade=t=out:st={max(0, total - 1.5):.9f}:d=1.5[bed]",
    ]
    if config.get("ducking", True):
        filters.append(
            "[bed][key]sidechaincompress=threshold=0.015:ratio=6:attack=15:release=350[ducked]"
        )
    else:
        filters.extend(["[key]anullsink", "[bed]anull[ducked]"])
    inputs = ["[voice]", "[ducked]", "[2:a]"]
    offsets = [sum(durations[:i]) for i in range(len(durations))]
    for cue in config.get("sfx", []):
        if not cue.get("path") or cue["shot_index"] >= len(durations):
            continue
        index = len(inputs)
        command += ["-i", cue["path"]]
        delay = round(
            quantize(
                offsets[cue["shot_index"]] + min(cue["at_sec"], durations[cue["shot_index"]])
            )
            * 1000
        )
        label = f"cue{index}"
        filters.append(
            f"[{index}:a]aresample=48000,volume={cue['gain_db']}dB,adelay={delay}:all=1[{label}]"
        )
        inputs.append(f"[{label}]")
    filters.append(
        "".join(inputs)
        + f"amix=inputs={len(inputs)}:duration=first:normalize=0,"
        + "alimiter=limit=0.94:level=0:latency=1[mixed]"
    )
    return command + [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "[mixed]",
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
        "-t",
        f"{total:.9f}",
        "-movflags",
        "+faststart",
        str(ready),
    ]
