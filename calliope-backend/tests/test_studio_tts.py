import io
import json
import subprocess
import wave

import pytest

from tests.test_studio import video_bytes
from tests.test_studio_director import prepared  # noqa: F401


def wav_bytes(seconds=0.3, rate=48000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def duration(path):
    output = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nk=1:nw=1", str(path),
    ])
    return float(output.decode().strip())


def test_build_timeline_and_mux(tmp_path):
    from calliope.studio import tts

    first = tmp_path / "a.wav"
    first.write_bytes(wav_bytes(0.2))
    second = tmp_path / "b.wav"
    second.write_bytes(wav_bytes(0.2))
    timeline = tmp_path / "dialogue.wav"
    tts.build_timeline([(first, 0.0), (second, 0.5)], 1.0, timeline)
    assert timeline.is_file()
    assert 0.9 <= duration(timeline) <= 1.1
    video = tmp_path / "video.mp4"
    video.write_bytes(video_bytes(tmp_path, audio=True))
    muxed = tmp_path / "video-tts.mp4"
    tts.mux_video(video, timeline, muxed, 0.12)
    assert muxed.is_file()
    assert duration(muxed) > 0


@pytest.mark.parametrize("matches", [True, False])
def test_apply_to_shot_uses_tts_engine(client, prepared, monkeypatch, tmp_path, matches):
    from calliope.studio import service, tts
    from calliope.studio.export import inspect_media

    project, shots = prepared
    shot_id = shots[0]["id"]
    take = service.store_upload(shot_id, video_bytes(tmp_path, audio=True), "video")
    service.select_take(take["id"])
    with service.database() as conn:
        metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()[0])
        metadata.update(model_revision="original-revision", director_shot_hash="original-contract",
                        dialogue_qc={"status": "ok"}, director_qc_review={"approved": True})
        conn.execute("UPDATE studio_takes SET metadata_json=? WHERE id=?",
                     (json.dumps(metadata), take["id"]))
        conn.execute("UPDATE studio_shots SET approved=1,trim_start_sec=0.1,trim_end_sec=0.4 "
                     "WHERE clip_id=?", (shot_id,))
        conn.commit()
    shot = next(s for scene in service.board(project)["scenes"] for s in scene["shots"] if s["id"] == shot_id)

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            pass

        def check_available(self):
            return {"ok": True}

        def synthesize(self, text, voice, cfg_value, inference_timesteps, reference=None):
            return wav_bytes()

    monkeypatch.setattr(tts, "ModalTTSEngine", FakeEngine)
    # An existing local checkpoint is simulated; the actual ASR invocation is
    # deliberately mocked via _matches, so no model is loaded or downloaded.
    checkpoint = tmp_path / "fake-asr.pt"
    checkpoint.write_bytes(b"mock")
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={
        "studio_tts_enabled": True, "studio_asr_checkpoint": str(checkpoint),
    }))
    monkeypatch.setattr(tts, "_matches", lambda *args: matches)
    if not matches:
        with pytest.raises(RuntimeError, match="valid"):
            tts.apply_to_shot(shot)
        assert service.board(project)["scenes"][0]["shots"][0]["approved"]
        return
    updated = tts.apply_to_shot(shot)
    selected = next(t for t in updated["takes"] if t["id"] == updated["selected_video_id"])
    assert selected["output_path"].endswith("video-tts.mp4")
    assert inspect_media(selected["output_path"])["has_audio"] is True
    assert selected["metadata"]["model_revision"] == "original-revision"
    assert selected["metadata"]["director_shot_hash"] == "original-contract"
    assert "dialogue_qc" not in selected["metadata"]
    assert "director_qc_review" not in selected["metadata"]
    assert not updated["approved"]
    assert updated["trim_start_sec"] is None and updated["trim_end_sec"] is None
    second_take = service.store_upload(shots[1]["id"], video_bytes(tmp_path, audio=True), "video")
    service.select_take(second_take["id"])
    second_shot = next(s for scene in service.board(project)["scenes"] for s in scene["shots"]
                       if s["id"] == shots[1]["id"])
    second_updated = tts.apply_to_shot(second_shot)
    assert next(t for t in second_updated["takes"] if t["id"] == second_take["id"])["output_path"] != selected["output_path"]
    monkeypatch.setattr(FakeEngine, "synthesize", lambda *args, **kwargs: pytest.fail("Dub repeated"))
    repeated = tts.apply_to_shot(updated)
    assert next(t for t in repeated["takes"] if t["id"] == take["id"])["output_path"] == selected["output_path"]


def test_tts_rejects_repeated_speech_before_muxing(monkeypatch, tmp_path):
    from calliope.studio import transcription, tts
    from tests.test_studio_transcription import _words_transcript

    expected = "Bonjour mon ami."
    transcript = _words_transcript(expected + " " + expected)
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"studio_asr_checkpoint": "local.pt"}))
    monkeypatch.setattr(transcription, "transcribe_dialogue", lambda *args: transcript)
    assert not tts._matches(expected, tmp_path / "speech.wav", "fr")


def test_post_dub_without_asr_stops_before_remote_tts(client, prepared, monkeypatch, tmp_path):
    from calliope.studio import service, tts

    project, shots = prepared
    take = service.store_upload(shots[0]["id"], video_bytes(tmp_path, audio=True), "video")
    service.select_take(take["id"])
    shot = next(s for scene in service.board(project)["scenes"]
                for s in scene["shots"] if s["id"] == shots[0]["id"])

    class ForbiddenEngine:
        def __init__(self, *args, **kwargs):
            pytest.fail("Paid TTS must never start before local ASR preflight")

    monkeypatch.setattr(tts, "ModalTTSEngine", ForbiddenEngine)
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={
        "studio_tts_enabled": True, "studio_asr_checkpoint": "",
    }))
    with pytest.raises(RuntimeError, match="checkpoint ASR local requis"):
        tts.apply_to_shot(shot)

    assert service.board(project)["scenes"][0]["shots"][0]["selected_video_id"] == take["id"]
