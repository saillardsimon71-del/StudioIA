"""Offline invariants for per-series voice-reference caching and hash locks."""

import hashlib

import pytest

from calliope.studio import tts


class FakeTTSEngine:
    def __init__(self):
        self.calls = []

    def synthesize(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return b"fake-reference-WAV-bytes"


def test_voice_reference_is_identical_across_projects_of_same_series(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"assets_dir": tmp_path}))
    first = tmp_path / "episode-1" / "takes" / "tts-111"
    second = tmp_path / "episode-2" / "takes" / "tts-222"
    engine = FakeTTSEngine()

    first_reference = tts._reference_for("durio", "French voice", "Durio", engine,
                                         first, series_id=30)
    second_reference = tts._reference_for("durio", "Different prompt", "Durio", engine,
                                          second, series_id=30)

    assert first_reference == second_reference
    assert len(engine.calls) == 1
    path = tmp_path / "series" / "30" / "tts-voices" / "durio.wav"
    assert path.read_bytes() == first_reference
    assert path.with_suffix(".sha256").read_text().strip() == hashlib.sha256(
        first_reference
    ).hexdigest()
    manifest = tts._reference_hashes([{"speaker": "durio", "text": "Bonjour."}],
                                     second, series_id=30)
    assert manifest == {"durio": hashlib.sha256(first_reference).hexdigest()}


def test_changed_voice_reference_fails_without_new_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"assets_dir": tmp_path}))
    directory = tmp_path / "project" / "takes" / "tts-5"
    engine = FakeTTSEngine()
    tts._reference_for("momo", "French voice", "Momo", engine, directory, series_id=30)

    path = tts._voice_reference_path("momo", directory, series_id=30)
    path.write_bytes(b"unexpected-new-voice")
    with pytest.raises(RuntimeError, match="absente ou modifiée"):
        tts._reference_for("momo", "French voice", "Momo", engine, directory,
                           series_id=30)
    assert len(engine.calls) == 1


def test_deleted_locked_voice_reference_never_silently_regenerates(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"assets_dir": tmp_path}))
    directory = tmp_path / "project" / "takes" / "tts-5"
    engine = FakeTTSEngine()
    tts._reference_for("momo", "French voice", "Momo", engine, directory, series_id=30)
    tts._voice_reference_path("momo", directory, series_id=30).unlink()
    with pytest.raises(RuntimeError, match="absente ou modifiée"):
        tts._reference_for("momo", "French voice", "Momo", engine, directory, series_id=30)
    assert len(engine.calls) == 1


def test_standalone_projects_keep_legacy_reference_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(update={"assets_dir": tmp_path}))
    directory = tmp_path / "project" / "studio" / "takes" / "tts-1"
    assert tts._voice_reference_path("momo", directory) == (
        tmp_path / "project" / "studio" / "tts-voices" / "momo.wav"
    )


def test_asr_not_configured_never_means_spoken_words_verified(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "settings", tts.settings.model_copy(
        update={"studio_asr_checkpoint": ""},
    ))
    assert tts._matches("La porte est fermée.", tmp_path / "synthetic.wav", "fr") is False
