from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from pathlib import Path

from calliope.config import settings
from calliope.engines.modal_tts import ModalTTSEngine
from calliope.studio import service

DEFAULT_VOICES = {
    "durio": "A huge, extremely muscular male prisoner, very deep, raspy, powerful French voice, aggressive but clearly articulated",
    "potiron": "A nervous male prison cook, medium-high slightly nasal French voice, fast and trembling but clear and intelligible",
    "momo": "A calm male prisoner, medium-deep, cold, articulate French voice, slow and confident",
}

# Marge tolérée entre la durée du TTS et la fenêtre de réplique du contrat.
TTS_FIT_TOLERANCE_SEC = 0.08
# Silences de bord retirés avant de placer une réplique.
SILENCE_TRIM = (
    "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-50dB,"
    "areverse,silenceremove=start_periods=1:start_duration=0.05:start_threshold=-50dB,"
    "areverse"
)


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


def mux_video(video, tts_audio, output, native_gain, mute_windows=None):
    """Mixe le TTS en conservant le jeu natif hors des fenêtres remplacées.

    Sans ``mute_windows``, le comportement historique reste disponible : le TTS
    remplace toute la piste (ou se superpose si ``native_gain`` > 0). Avec des
    fenêtres, la piste native est coupée uniquement pendant les répliques
    doublées : l'ambiance et les autres voix de la prise restent audibles.
    """
    windows = [(float(start), float(end)) for start, end in (mute_windows or [])
               if float(end) > float(start)]
    if windows:
        conditions = "+".join(
            f"between(t,{start:.3f},{end:.3f})" for start, end in windows
        )
        graph = (
            f"[0:a]volume=0:enable='{conditions}',aresample=48000[nat];"
            f"[1:a]aresample=48000[tts];"
            f"[nat][tts]amix=inputs=2:duration=longest:normalize=0[a]"
        )
        args = [
            "ffmpeg", "-y", "-i", str(video), "-i", str(tts_audio),
            "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output),
        ]
    elif float(native_gain) > 0:
        graph = (f"[0:a]volume={float(native_gain):.3f},aresample=48000[nat];"
                 f"[1:a]volume=1.0,aresample=48000[tts];"
                 f"[nat][tts]amix=inputs=2:duration=first:normalize=0[a]")
        args = ["ffmpeg", "-y", "-i", str(video), "-i", str(tts_audio), "-filter_complex", graph,
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)]
    else:
        args = ["ffmpeg", "-y", "-i", str(video), "-i", str(tts_audio), "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-shortest", "-movflags", "+faststart", str(output)]
    _run(args)


def _matches(expected, path, language):
    if not settings.studio_asr_checkpoint:
        return True
    from calliope.studio import transcription
    result = transcription.transcribe_dialogue(str(path), settings.studio_asr_checkpoint, language)
    verdict = transcription.assess_transcript_v2(expected, result, language=language)
    return verdict.get("status") == "ok"

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def _trim_silence(source: Path, target: Path) -> None:
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source), "-af", SILENCE_TRIM,
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(target),
    ])


def _resolve_dub_indexes(shot, context, turns, explicit=None):
    """Sélectionne les répliques à remplacer.

    Retourne ``None`` quand aucun post-doublage n'a été demandé : la prise
    native reste alors intacte. ``post_dub=True`` sans sélection ciblée est
    encore accepté pour compatibilité, mais il déclenche un remplacement
    complet explicitement signalé dans les métadonnées.
    """
    raw = explicit
    if raw is None:
        if shot.get("post_dub_turns") is not None:
            raw = shot["post_dub_turns"]
        elif (context.get("shot") or {}).get("post_dub_turns") is not None:
            raw = context["shot"]["post_dub_turns"]
        elif context.get("post_dub_turns") is not None:
            raw = context["post_dub_turns"]
    if raw is None:
        requested = (
            shot.get("post_dub")
            or (context.get("shot") or {}).get("post_dub")
            or (context.get("director_contract") or {}).get("post_dub")
        )
        if requested:
            return list(range(len(turns)))
        return None
    if not isinstance(raw, (list, tuple)):
        raise RuntimeError("La sélection de post-doublage doit être une liste d'index.")
    indexes = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError("Chaque réplique à doubler doit être un index entier.")
        if value < 0 or value >= len(turns):
            raise RuntimeError(f"Réplique {value + 1} hors du plan ({len(turns)} répliques).")
        if value not in indexes:
            indexes.append(value)
    return sorted(indexes)


def _reference_for(key, voice, name, engine, directory, series_id=None):
    """Référence vocale stable, liée à la description et à la série.

    Le cache n'est jamais réutilisé si la description de voix ou le fichier a
    changé. Le sidecar JSON rend la provenance vérifiable ; ``approved`` reste
    faux tant qu'aucune écoute humaine ne l'a validée.
    """
    cache = directory.parent.parent / "tts-voices"
    cache.mkdir(parents=True, exist_ok=True)
    voice_hash = _sha256(str(voice).strip().encode("utf-8"))[:16]
    scope = f"series-{series_id}-" if series_id is not None else ""
    path = cache / f"{scope}{key}-{voice_hash}.wav"
    sidecar = path.with_suffix(".json")
    if path.is_file() and sidecar.is_file():
        meta = json.loads(sidecar.read_text(encoding="utf-8") or "{}")
        if (
            meta.get("voice_hash") == voice_hash
            and meta.get("sha256") == _sha256(path.read_bytes())
        ):
            return path.read_bytes(), meta | {"path": str(path), "reused": True}
    text = f"Je suis {name}. Voici ma voix, claire et bien articulee, pour cette histoire."
    audio = engine.synthesize(text, voice, 2.0, 10)
    path.write_bytes(audio)
    meta = {
        "key": key, "name": name, "voice": voice, "voice_hash": voice_hash,
        "sha256": _sha256(audio), "text": text, "approved": False,
        "app": settings.studio_tts_modal_app,
        "function": settings.studio_tts_modal_function,
        "cfg": 2.0, "timesteps": 10, "path": str(path), "reused": False,
    }
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return audio, meta

def apply_to_shot(shot, turn_indexes=None):
    """Applique un post-doublage explicite à une prise déjà rendue.

    Le jeu natif n'est plus remplacé par défaut : sans sélection de répliques,
    la fonction ne touche pas au fichier. Chaque réplique doublée est placée
    dans sa fenêtre de contrat ; un débordement est refusé au lieu d'être
    repoussé silencieusement sur la réplique suivante.
    """
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
    selected = _resolve_dub_indexes(shot, context, turns, turn_indexes)
    if selected is None or not selected:
        return shot
    voices = {e["key"]: e.get("voice_description") or e.get("description") or ""
              for e in context.get("entities", [])}
    names = {e["key"]: e.get("name") or e["key"] for e in context.get("entities", [])}
    source_path = (metadata.get("tts") or {}).get("source_path") or take["output_path"]
    engine = ModalTTSEngine(settings.studio_tts_modal_app, settings.studio_tts_modal_function)
    available = engine.check_available()
    if not available["ok"]:
        raise RuntimeError(available["message"])
    directory = Path(source_path).parent / f"tts-{take['id']}"
    directory.mkdir(parents=True, exist_ok=True)
    with service.database() as conn:
        row = conn.execute(
            "SELECT series_id FROM studio_episodes WHERE project_id=?", (shot["project_id"],)
        ).fetchone()
    series_id = row[0] if row else None
    references = {}
    for index in selected:
        speaker = str(turns[index].get("speaker") or "").strip()
        if speaker in references:
            continue
        voice = voice_for(speaker, voices.get(speaker))
        audio, meta = _reference_for(
            speaker, voice, names.get(speaker) or speaker, engine, directory, series_id,
        )
        references[speaker] = meta | {"bytes": audio}
    signature = {
        "dialogue": turns, "turn_indexes": selected, "voices": voices,
        "references": {key: meta["sha256"] for key, meta in references.items()},
        "app": settings.studio_tts_modal_app, "function": settings.studio_tts_modal_function,
        "cfg": settings.studio_tts_cfg, "timesteps": settings.studio_tts_timesteps,
        "native_gain": settings.studio_tts_native_gain,
    }
    previous = metadata.get("tts") or {}
    if previous.get("signature") == signature and Path(take["output_path"]).is_file():
        return shot
    language = str(context.get("language") or "fr")
    attempts = max(1, int(settings.studio_tts_turn_retries))
    duration = float(
        shot.get("duration_sec")
        or take.get("metadata", {}).get("duration_sec")
        or metadata.get("duration_sec")
        or 8
    )
    files, windows, cursor = [], [], 0.15
    for index in selected:
        turn = turns[index]
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        speaker = str(turn.get("speaker") or "").strip()
        voice = voice_for(speaker, voices.get(speaker))
        start = turn.get("start_sec")
        start = float(start) if isinstance(start, (int, float)) else cursor
        if start >= duration:
            raise RuntimeError(f"Réplique {index + 1} placée après la fin du plan.")
        end = turn.get("end_sec")
        if not isinstance(end, (int, float)):
            following = [
                float(item["start_sec"]) for item in turns[index + 1:]
                if isinstance(item.get("start_sec"), (int, float))
            ]
            end = min(following) if following else None
        window = float(end) - start if isinstance(end, (int, float)) else None
        best = None
        for attempt in range(attempts):
            cfg = settings.studio_tts_cfg if attempt % 2 == 0 else 1.6
            audio = engine.synthesize(
                text, voice, cfg, settings.studio_tts_timesteps,
                reference=references[speaker]["bytes"],
            )
            raw = directory / f"turn-{index:02d}-{attempt}-raw.wav"
            raw.write_bytes(audio)
            candidate = directory / f"turn-{index:02d}-{attempt}.wav"
            _trim_silence(raw, candidate)
            if _matches(text, candidate, language):
                best = candidate
                break
        if best is None:
            raise RuntimeError(f"Post-doublage non valide pour la replique {index + 1}.")
        length = _wav_duration(best)
        if window is not None and length > window + TTS_FIT_TOLERANCE_SEC:
            raise RuntimeError(
                f"Réplique {index + 1} trop longue pour sa fenêtre "
                f"({length:.2f} s pour {window:.2f} s): raccourcissez le texte "
                "ou rendez le temps au plan, sans décaler les répliques suivantes."
            )
        final = directory / f"turn-{index:02d}.wav"
        final.write_bytes(best.read_bytes())
        files.append((final, start))
        windows.append((start, min(duration, start + length)))
        cursor = start + length + 0.08
    if not files:
        return shot
    timeline = directory / "dialogue.wav"
    build_timeline(files, duration, timeline)
    muxed = directory / "video-tts.mp4"
    mux_video(source_path, timeline, muxed, settings.studio_tts_native_gain, windows)
    from calliope.studio.export import inspect_media
    metadata.update(inspect_media(muxed))
    for key in ("dialogue_qc", "transcription_cache", "director_qc_review"):
        metadata.pop(key, None)
    full = len(selected) == len(turns)
    metadata["tts"] = {
        "source_path": source_path,
        "strategy": "full_replacement" if full else "targeted_post_dub",
        "replaced_turns": list(selected),
        "preserved_turns": [index for index in range(len(turns)) if index not in selected],
        "windows": [[round(start, 3), round(end, 3)] for start, end in windows],
        "voice_references": {
            key: {field: meta[field] for field in ("path", "sha256", "voice_hash", "approved")}
            for key, meta in references.items()
        },
        "voice_identity_status": "unverified",
        "voice_identity_reason": (
            "Référence TTS non approuvée par une écoute humaine sur la série publiée."
        ),
        "lip_sync_status": "unverified",
        "signature": signature,
    }
    with service.database() as conn:
        conn.execute("UPDATE studio_takes SET output_path=?, metadata_json=? WHERE id=?",
                     (str(muxed), json.dumps(metadata), take["id"]))
        conn.execute("UPDATE studio_shots SET approved=0,trim_start_sec=NULL,trim_end_sec=NULL "
                     "WHERE clip_id=?", (shot["id"],))
        service.touch(conn, shot["project_id"])
        conn.commit()
        return service.public_shot(conn, shot["id"])
