from __future__ import annotations

import json
import subprocess
from pathlib import Path

from calliope.config import settings
from calliope.engines.modal_tts import ModalTTSEngine
from calliope.studio import service

DEFAULT_VOICES = {
    "durio": "A huge, extremely muscular male prisoner, very deep, raspy, powerful French voice, aggressive but clearly articulated",
    "potiron": "A nervous male prison cook, medium-high slightly nasal French voice, fast and trembling but clear and intelligible",
    "momo": "A calm male prisoner, medium-deep, cold, articulate French voice, slow and confident",
}


def voice_for(key, description):
    base = DEFAULT_VOICES.get(str(key or "").strip())
    if base:
        return base
    description = str(description or "").strip()
    if description:
        return "Clear, intelligible French voice. " + description
    return "Clear, intelligible French voice."


def _run(args):
    subprocess.run(args, check=True, capture_output=True)


def build_timeline(turns, duration, output, sample_rate=48000):
    args = ["ffmpeg", "-y", "-f", "lavfi", "-t", f"{float(duration):.3f}", "-i",
            f"anullsrc=r={sample_rate}:cl=mono"]
    for path, _ in turns:
        args += ["-i", str(path)]
    filters = []
    labels = ["[0:a]"]
    for index, (_, start) in enumerate(turns, start=1):
        delay = max(0, int(round(float(start) * 1000)))
        filters.append(f"[{index}:a]aformat=channel_layouts=mono,adelay={delay}|{delay}[a{index}]")
        labels.append(f"[a{index}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0[a]")
    args += ["-filter_complex", ";".join(filters), "-map", "[a]", "-t", f"{float(duration):.3f}",
             "-c:a", "pcm_s16le", str(output)]
    _run(args)


def mux_video(video, tts_audio, output, native_gain):
    if float(native_gain) > 0:
        graph = (f"[0:a]volume={float(native_gain):.3f}[nat];"
                 f"[1:a]volume=1.0[tts];"
                 f"[nat][tts]amix=inputs=2:duration=first:normalize=0[a]")
        args = ["ffmpeg", "-y", "-i", str(video), "-i", str(tts_audio), "-filter_complex", graph,
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(output)]
    else:
        args = ["ffmpeg", "-y", "-i", str(video), "-i", str(tts_audio), "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)]
    _run(args)


def _matches(expected, path, language):
    if not settings.studio_asr_checkpoint:
        return True
    from calliope.studio import transcription
    result = transcription.transcribe_dialogue(str(path), settings.studio_asr_checkpoint, language)
    verdict = transcription.assess_transcript_v2(expected, result, language=language)
    return verdict.get("status") == "ok"

def _reference_for(key, voice, name, engine, directory):
    cache = directory.parent.parent / "tts-voices"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{key}.wav"
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()
    text = f"Je suis {name}. Voici ma voix, claire et bien articulee, pour cette histoire."
    audio = engine.synthesize(text, voice, 2.0, 10)
    path.write_bytes(audio)
    return audio

def apply_to_shot(shot):
    if not settings.studio_tts_enabled:
        return shot
    take = next((t for t in shot.get("takes", []) if t["id"] == shot.get("selected_video_id")), None)
    if not take or not take.get("output_path"):
        return shot
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()[0] or "{}")
    contract = context.get("director_contract") or {}
    turns = contract.get("dialogue") or shot.get("dialogue_turns") or []
    if not turns:
        return shot
    voices = {e["key"]: e.get("voice_description") or e.get("description") or ""
              for e in context.get("entities", [])}
    names = {e["key"]: e.get("name") or e["key"] for e in context.get("entities", [])}
    signature = {
        "dialogue": turns, "voices": voices,
        "app": settings.studio_tts_modal_app, "function": settings.studio_tts_modal_function,
        "cfg": settings.studio_tts_cfg, "timesteps": settings.studio_tts_timesteps,
        "native_gain": settings.studio_tts_native_gain,
    }
    previous = metadata.get("tts") or {}
    if previous.get("signature") == signature and Path(take["output_path"]).is_file():
        return shot
    source_path = previous.get("source_path") or take["output_path"]
    engine = ModalTTSEngine(settings.studio_tts_modal_app, settings.studio_tts_modal_function)
    available = engine.check_available()
    if not available["ok"]:
        raise RuntimeError(available["message"])
    directory = Path(source_path).parent / f"tts-{take['id']}"
    directory.mkdir(parents=True, exist_ok=True)
    language = str(context.get("language") or "fr")
    attempts = max(1, int(settings.studio_tts_turn_retries))
    files = []
    for index, turn in enumerate(turns):
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        speaker = str(turn.get("speaker") or "").strip()
        voice = voice_for(speaker, voices.get(speaker))
        reference = _reference_for(speaker, voice, names.get(speaker) or speaker, engine, directory)
        best = None
        for attempt in range(attempts):
            cfg = settings.studio_tts_cfg if attempt % 2 == 0 else 1.6
            audio = engine.synthesize(text, voice, cfg, settings.studio_tts_timesteps, reference=reference)
            path = directory / f"turn-{index:02d}-{attempt}.wav"
            path.write_bytes(audio)
            if _matches(text, path, language):
                best = path
                break
        if best is None:
            raise RuntimeError(f"Post-doublage non valide pour la replique {index + 1}.")
        final = directory / f"turn-{index:02d}.wav"
        final.write_bytes(best.read_bytes())
        files.append((final, float(turn.get("start_sec") or 0)))
    if not files:
        return shot
    duration = float(shot.get("duration_sec") or take.get("metadata", {}).get("duration_sec") or 8)
    timeline = directory / "dialogue.wav"
    build_timeline(files, duration, timeline)
    muxed = directory / "video-tts.mp4"
    mux_video(source_path, timeline, muxed, settings.studio_tts_native_gain)
    from calliope.studio.export import inspect_media
    metadata.update(inspect_media(muxed))
    for key in ("dialogue_qc", "transcription_cache", "director_qc_review"):
        metadata.pop(key, None)
    metadata["tts"] = {"source_path": source_path, "signature": signature}
    with service.database() as conn:
        conn.execute("UPDATE studio_takes SET output_path=?, metadata_json=? WHERE id=?",
                     (str(muxed), json.dumps(metadata), take["id"]))
        conn.execute("UPDATE studio_shots SET approved=0,trim_start_sec=NULL,trim_end_sec=NULL "
                     "WHERE clip_id=?", (shot["id"],))
        service.touch(conn, shot["project_id"])
        conn.commit()
        return service.public_shot(conn, shot["id"])
