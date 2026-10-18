from __future__ import annotations

import hashlib
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
        # An unavailable ASR is not proof that the words match.
        return False
    from calliope.studio import transcription
    result = transcription.transcribe_dialogue(str(path), settings.studio_asr_checkpoint, language)
    verdict = transcription.assess_transcript_v2(expected, result, language=language)
    return verdict.get("status") == "ok"

def _voice_reference_path(key, directory, series_id=None):
    # A project-scoped cache changes across episodes. Use one reference store
    # for a series, while preserving the former location for standalone projects.
    cache = (settings.assets_dir / "series" / str(series_id) / "tts-voices"
             if series_id is not None else directory.parent.parent / "tts-voices")
    return cache / f"{key}.wav"


def _reference_digest(path):
    if not path.is_file() or path.stat().st_size == 0:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_hashes(turns, directory, series_id=None):
    speakers = sorted({str(turn.get("speaker") or "").strip() for turn in turns
                       if str(turn.get("text") or "").strip()})
    return {key: _reference_digest(_voice_reference_path(key, directory, series_id))
            for key in speakers}


def _published_series_speakers(conn, series_id, current_project_id):
    """Names that already spoke in the *published* canonical series exports.

    Draft episodes, old attempts and unexported shots do not define voice canon.
    """
    if series_id is None:
        return set()
    rows = conn.execute(
        "SELECT x.snapshot_json FROM studio_exports x "
        "JOIN studio_episodes e ON e.project_id=x.project_id "
        "WHERE e.series_id=? AND e.project_id<>? AND e.canon_revision IS NOT NULL "
        "AND x.revision=e.canon_revision AND x.output_path IS NOT NULL",
        (series_id, current_project_id),
    ).fetchall()
    speakers = set()
    for row in rows:
        snapshot = json.loads(row["snapshot_json"] or "{}")
        for clip in snapshot.get("clips", []):
            if clip.get("purpose") in ("midroll", "outro"):
                continue
            state = clip.get("state") or {}
            turns = clip.get("dialogue_turns") or state.get("dialogue_turns") or []
            for turn in turns:
                if isinstance(turn, dict) and str(turn.get("text") or "").strip():
                    if turn.get("speaker"):
                        speakers.add(str(turn["speaker"]).strip())
            if not turns and str(clip.get("dialogue") or "").strip():
                speaker = state.get("speaker")
                if speaker:
                    speakers.add(str(speaker).strip())
    return speakers


def _ensure_canonical_voice_sources(turns, directory, series_id, published_speakers):
    """Do not invent a replacement voice for a previously published character."""
    if series_id is None or not published_speakers:
        return
    speakers = {str(turn.get("speaker") or "").strip() for turn in turns
                if str(turn.get("text") or "").strip()}
    for speaker in sorted(speakers & published_speakers):
        path = _voice_reference_path(speaker, directory, series_id)
        if not _reference_digest(path):
            raise RuntimeError(
                f"Voix canonique de {speaker} introuvable pour la série {series_id}. "
                "Importez d'abord sa référence WAV validée de l'épisode publié "
                "dans le dossier tts-voices de la série. "
                "Aucun nouvel échantillon vocal ne sera généré automatiquement."
            )


def _reference_for(key, voice, name, engine, directory, *, series_id=None):
    path = _voice_reference_path(key, directory, series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".sha256")
    fingerprint = _reference_digest(path)
    if lock.is_file():
        expected = lock.read_text(encoding="utf-8").strip()
        if not fingerprint or fingerprint != expected:
            raise RuntimeError(
                f"Référence vocale {key} absente ou modifiée : restaurer le WAV approuvé "
                "avant tout doublage. Aucune nouvelle voix ne sera générée."
            )
    if fingerprint:
        if not lock.is_file():
            # An existing operator-supplied WAV is adopted unchanged.
            lock.write_text(fingerprint + "\n", encoding="utf-8")
        return path.read_bytes()
    if path.exists():
        raise RuntimeError(f"Référence vocale vide pour {key} : correction manuelle requise.")
    text = f"Je suis {name}. Voici ma voix, claire et bien articulee, pour cette histoire."
    audio = engine.synthesize(text, voice, 2.0, 10)
    if not audio:
        raise RuntimeError(f"Référence vocale non produite pour {key}.")
    path.write_bytes(audio)
    lock.write_text(hashlib.sha256(audio).hexdigest() + "\n", encoding="utf-8")
    return audio

def apply_to_shot(shot):
    if not settings.studio_tts_enabled:
        return shot
    take = next((t for t in shot.get("takes", []) if t["id"] == shot.get("selected_video_id")), None)
    if not take or not take.get("output_path"):
        return shot
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
        episode = conn.execute(
            "SELECT series_id FROM studio_episodes WHERE project_id=?",
            (shot["project_id"],),
        ).fetchone()
        series_id = episode["series_id"] if episode else None
        published_speakers = _published_series_speakers(conn, series_id, shot["project_id"])
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
    previous = metadata.get("tts") or {}
    source_path = previous.get("source_path") or take["output_path"]
    directory = Path(source_path).parent / f"tts-{take['id']}"
    signature = {
        "dialogue": turns, "voices": voices,
        "app": settings.studio_tts_modal_app, "function": settings.studio_tts_modal_function,
        "cfg": settings.studio_tts_cfg, "timesteps": settings.studio_tts_timesteps,
        "native_gain": settings.studio_tts_native_gain,
        "series_id": series_id,
        "reference_sha256": _reference_hashes(turns, directory, series_id),
    }
    if previous.get("signature") == signature and Path(take["output_path"]).is_file():
        return shot
    # Fail before hydration or any paid synthesis when a returning speaker
    # cannot be matched to an operator-approved reference from the prior film.
    _ensure_canonical_voice_sources(turns, directory, series_id, published_speakers)
    if not settings.studio_asr_checkpoint or not Path(settings.studio_asr_checkpoint).is_file():
        raise RuntimeError(
            "Post-doublage bloqué avant tout appel TTS payant : "
            "checkpoint ASR local requis pour vérifier les paroles."
        )
    engine = ModalTTSEngine(settings.studio_tts_modal_app, settings.studio_tts_modal_function)
    available = engine.check_available()
    if not available["ok"]:
        raise RuntimeError(available["message"])
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
        reference = _reference_for(
            speaker, voice, names.get(speaker) or speaker, engine, directory,
            series_id=series_id,
        )
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
    # Pin the exact reference WAVs used, not just the voice labels. A changed
    # voice file must never be hidden by an apparently matching TTS cache.
    signature["reference_sha256"] = _reference_hashes(turns, directory, series_id)
    metadata["tts"] = {"source_path": source_path, "signature": signature}
    with service.database() as conn:
        conn.execute("UPDATE studio_takes SET output_path=?, metadata_json=? WHERE id=?",
                     (str(muxed), json.dumps(metadata), take["id"]))
        conn.execute("UPDATE studio_shots SET approved=0,trim_start_sec=NULL,trim_end_sec=NULL "
                     "WHERE clip_id=?", (shot["id"],))
        service.touch(conn, shot["project_id"])
        conn.commit()
        return service.public_shot(conn, shot["id"])
