import io
import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from tests.test_studio import new_board, upload, video_bytes


def tone_wav(seconds=0.8, rate=48000, frequency=220.0):
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * frequency * i / rate)))
        for i in range(int(seconds * rate))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()


def selected_shot(client, project_id, shot_id):
    from calliope.studio import service

    return next(
        shot for scene in service.board(project_id)["scenes"] for shot in scene["shots"]
        if shot["id"] == shot_id
    )


def prepare_dialogue_shot(client, tmp_path, turns, post_dub_turns=None, duration=3.0):
    from calliope.studio import service

    project_id, board = new_board(client)
    shot_id = board["scenes"][0]["shots"][0]["id"]
    take = upload(client, shot_id, video_bytes(tmp_path, audio=True), "video")
    client.post(f"/api/studio/takes/{take['id']}/select").raise_for_status()
    context = {"dialogue_turns": turns, "language": "fr"}
    if post_dub_turns is not None:
        context["post_dub_turns"] = post_dub_turns
    with service.database() as conn:
        conn.execute(
            "INSERT INTO studio_shot_context(clip_id,context_json) VALUES(?,?) "
            "ON CONFLICT(clip_id) DO UPDATE SET context_json=excluded.context_json",
            (shot_id, json.dumps(context)),
        )
        conn.execute("UPDATE clips SET duration_sec=? WHERE id=?", (duration, shot_id))
        conn.execute(
            "INSERT INTO studio_productions(project_id,status,stage,progress,message) "
            "VALUES(?,'pending','qc',0,'') ON CONFLICT(project_id) DO UPDATE SET status='pending'",
            (project_id,),
        )
        conn.commit()
    return project_id, selected_shot(client, project_id, shot_id)


def test_tts_does_not_replace_native_audio_without_explicit_selection(client, monkeypatch, tmp_path):
    from calliope.studio import tts

    project_id, shot = prepare_dialogue_shot(
        client, tmp_path,
        [{"speaker": "keeper", "text": "Bonjour.", "start_sec": 0.0, "end_sec": 1.0}],
    )
    before = shot["takes"][0]["output_path"]
    calls = []

    class ForbiddenEngine:
        def __init__(self, *args, **kwargs):
            calls.append("engine")

        def check_available(self):
            return {"ok": True}

        def synthesize(self, *args, **kwargs):
            calls.append("synthesize")
            return tone_wav()

    monkeypatch.setattr(tts, "ModalTTSEngine", ForbiddenEngine)
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"studio_tts_enabled": True}))
    assert tts.apply_to_shot(shot) is shot
    assert calls == []
    assert shot["takes"][0]["output_path"] == before
    assert "tts" not in shot["takes"][0]["metadata"]



def test_tts_targeted_dub_keeps_other_turns_and_marks_identity_unverified(
    client, monkeypatch, tmp_path
):
    from calliope.studio import tts

    project_id, shot = prepare_dialogue_shot(
        client, tmp_path,
        [
            {"speaker": "keeper", "text": "Bonjour.", "start_sec": 0.0, "end_sec": 1.0},
            {"speaker": "keeper", "text": "Au revoir.", "start_sec": 1.5, "end_sec": 2.5},
        ],
        post_dub_turns=[1],
    )

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            pass

        def check_available(self):
            return {"ok": True}

        def synthesize(self, text, voice, cfg_value, inference_timesteps, reference=None):
            return tone_wav(0.5 if text.startswith("Je suis") else 0.8)

    monkeypatch.setattr(tts, "ModalTTSEngine", FakeEngine)
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"studio_tts_enabled": True}))
    monkeypatch.setattr(tts, "_matches", lambda *args: True)
    updated = tts.apply_to_shot(shot)
    selected = next(t for t in updated["takes"] if t["id"] == updated["selected_video_id"])
    info = selected["metadata"]["tts"]
    assert info["strategy"] == "targeted_post_dub"
    assert info["replaced_turns"] == [1]
    assert info["preserved_turns"] == [0]
    assert info["voice_identity_status"] == "unverified"
    assert info["lip_sync_status"] == "unverified"
    assert len(info["windows"]) == 1
    reference = next(iter(info["voice_references"].values()))
    assert reference["approved"] is False
    assert len(reference["sha256"]) == 64
    sidecar = Path(reference["path"]).with_suffix(".json")
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["approved"] is False


def test_tts_rejects_a_line_that_overflows_its_contract_window(client, monkeypatch, tmp_path):
    from calliope.studio import tts

    project_id, shot = prepare_dialogue_shot(
        client, tmp_path,
        [{"speaker": "keeper", "text": "Trop long.", "start_sec": 0.0, "end_sec": 0.4}],
        post_dub_turns=[0],
    )

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            pass

        def check_available(self):
            return {"ok": True}

        def synthesize(self, *args, **kwargs):
            return tone_wav(1.0)

    monkeypatch.setattr(tts, "ModalTTSEngine", FakeEngine)
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"studio_tts_enabled": True}))
    monkeypatch.setattr(tts, "_matches", lambda *args: True)
    with pytest.raises(RuntimeError, match="fenêtre"):
        tts.apply_to_shot(shot)


def test_voice_reference_is_keyed_by_description_and_file_hash(tmp_path):
    from calliope.studio import tts

    calls = []

    class FakeEngine:
        def synthesize(self, text, voice, cfg, timesteps):
            calls.append(voice)
            return tone_wav(0.4, frequency=200 + len(calls) * 20)

    engine = FakeEngine()
    directory = tmp_path / "project" / "videos" / "tts-1"
    directory.mkdir(parents=True)
    first, first_meta = tts._reference_for("durio", "voix A", "Durio", engine, directory, 7)
    again, again_meta = tts._reference_for("durio", "voix A", "Durio", engine, directory, 7)
    other, other_meta = tts._reference_for("durio", "voix B", "Durio", engine, directory, 7)
    assert calls == ["voix A", "voix B"]
    assert again == first and again_meta["reused"] is True
    assert other != first and other_meta["path"] != first_meta["path"]
    assert first_meta["approved"] is False
    Path(first_meta["path"]).write_bytes(tone_wav(0.2, frequency=330))
    _, refreshed = tts._reference_for("durio", "voix A", "Durio", engine, directory, 7)
    assert refreshed["reused"] is False
    assert calls == ["voix A", "voix B", "voix A"]


def test_editorial_rejects_invalid_post_dub_targets():
    from calliope.studio import editorial

    plan = {
        "post_dub": True,
        "scenes": [{"shots": [{
            "duration_sec": 6.0,
            "dialogue_turns": [{"speaker": "a", "text": "Un."}, {"speaker": "b", "text": "Deux."}],
            "post_dub_turns": [2],
        }]}],
    }
    result = editorial.validate_plan(plan)
    assert result["ok"] is False
    assert any("hors plan" in error for error in result["errors"])


def test_editorial_warns_when_global_post_dub_has_no_selected_lines():
    from calliope.studio import editorial

    plan = {
        "post_dub": True,
        "scenes": [{"shots": [{
            "duration_sec": 6.0,
            "dialogue_turns": [{"speaker": "a", "text": "Un."}, {"speaker": "b", "text": "Deux."}],
        }]}],
    }
    result = editorial.validate_plan(plan)
    assert any("sans répliques ciblées" in warning for warning in result["warnings"])


def test_editorial_warns_on_an_unreconnected_state_between_shots():
    from calliope.studio import editorial

    plan = {"scenes": [{"shots": [
        {"duration_sec": 5.0, "end_state": {"lunettes": "sur le visage"}},
        {"duration_sec": 5.0, "start_state": {"lunettes": "dans la poche"}},
    ]}]}
    result = editorial.validate_plan(plan)
    assert any("état non raccordé" in warning for warning in result["warnings"])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_delivery_verification_rejects_96khz_and_short_audio(tmp_path):
    from calliope.studio.export import verify_delivery

    source = tmp_path / "bad.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=128x72:r=24:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-ar", "96000", "-ac", "2", str(source),
    ], check=True)
    with pytest.raises(ValueError, match="96000 Hz") as error:
        verify_delivery(source)
    assert "audio plus court" in str(error.value)

    good = tmp_path / "good.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=128x72:r=24:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-ar", "48000", "-ac", "2", str(good),
    ], check=True)
    assert verify_delivery(good)["sample_rate"] == 48000


def test_render_shot_never_auto_approves_replaced_voices(client, monkeypatch, tmp_path):
    import asyncio

    from calliope.studio import production, service

    project_id, shot = prepare_dialogue_shot(
        client, tmp_path,
        [{"speaker": "keeper", "text": "Bonjour.", "start_sec": 0.0, "end_sec": 1.0}],
        post_dub_turns=[0],
    )

    async def fake_assess(_shot):
        return {"status": "ok", "replaced_turns": [0], "lip_sync_status": "unverified"}

    monkeypatch.setattr(production, "assess_take", fake_assess)
    monkeypatch.setattr(
        production, "reusable_video", lambda current: production.selected_video(current)
    )
    assert production.selected_video(shot) is not None
    with pytest.raises(production.ProductionPausedError):
        asyncio.run(production.render_shot(
            {"id": 424242, "project_id": project_id, "video_batch_last": True},
            shot,
            lambda: False,
        ))
    board = service.board(project_id)
    assert board["scenes"][0]["shots"][0]["approved"] is False
    assert board["production"]["status"] == "paused"
    assert "Voix remplacées" in board["production"]["message"]


def test_silent_native_shot_with_unexpected_speech_is_not_auto_approved(
    client, monkeypatch, tmp_path
):
    import asyncio

    from calliope.studio import production, service, transcription

    project_id, shot = prepare_dialogue_shot(client, tmp_path, [])

    def fake_transcribe(*args):
        return {
            "status": "ok", "text": "NOOOOO !", "reason": "",
            "segments": [{"words": [
                {"word": "NOOOOO", "start": 1.0, "end": 1.4, "probability": 0.95}
            ]}],
            "speech_intervals": [[1.0, 1.4]],
        }

    monkeypatch.setattr(
        production, "settings",
        production.settings.model_copy(update={"studio_asr_checkpoint": "fake.pt"}),
    )
    monkeypatch.setattr(transcription, "transcribe_dialogue", fake_transcribe)
    verdict = asyncio.run(production.assess_take(shot))
    assert verdict["status"] == "unknown"
    assert "Parole détectée" in verdict["reason"]

    monkeypatch.setattr(
        production, "reusable_video", lambda current: production.selected_video(current)
    )
    asyncio.run(production.render_shot(
        {"id": 434343, "project_id": project_id, "video_batch_last": True}, shot, lambda: False,
    ))
    assert service.board(project_id)["scenes"][0]["shots"][0]["approved"] is False


def test_failed_dialogue_qc_leaves_the_shot_unapproved(client, monkeypatch, tmp_path):
    import asyncio

    from calliope.studio import production, service

    project_id, shot = prepare_dialogue_shot(
        client, tmp_path,
        [{"speaker": "keeper", "text": "Bonjour.", "start_sec": 0.0, "end_sec": 1.0}],
    )

    async def failing(_shot):
        return {"status": "extra", "reason": "mots hors script"}

    monkeypatch.setattr(production, "assess_take", failing)
    monkeypatch.setattr(
        production, "reusable_video", lambda current: production.selected_video(current)
    )
    asyncio.run(production.render_shot(
        {"id": 454545, "project_id": project_id, "video_batch_last": True}, shot, lambda: False,
    ))
    assert service.board(project_id)["scenes"][0]["shots"][0]["approved"] is False
