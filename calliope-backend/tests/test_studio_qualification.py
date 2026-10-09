import copy
import json
import math
import wave

import pytest

from calliope.studio import audio, h3_prompt, series_planner
from tests.test_studio_export import media as media


def exchange():
    return {
        "prompt": "Mango and Blueberry face each other in one static two-shot.",
        "image_prompt": "A two-shot of Mango and Blueberry.",
        "duration_sec": 6,
        "character_keys": ["mango", "blueberry"],
        "dialogue_turns": [
            {"speaker": "Mango", "text": "Still think you belong here?"},
            {"speaker": "Blueberry", "text": "Check the number again.",
             "reaction": "Mango's smile disappears."},
        ],
    }


def context():
    return {"language": "en", "entities": [
        {"key": "mango", "name": "Mango", "kind": "character", "description": "Orange face."},
        {"key": "blueberry", "name": "Blueberry", "kind": "character", "description": "Blue face."},
    ]}


def test_two_speakers_are_normalized_without_an_artificial_cut():
    plan = series_planner.normalize_plan({"scenes": [{"shots": [exchange()]}]}, context())
    shots = plan["scenes"][0]["shots"]
    assert len(shots) == 1
    assert shots[0]["dialogue_turns"][0]["speaker"] == "mango"
    assert shots[0]["dialogue_turns"][1]["speaker"] == "blueberry"
    assert shots[0]["dialogue"] == "Still think you belong here? Check the number again."
    assert shots[0]["speaker"] == ""
    assert shots[0]["audio_mode"] == "native"


def test_compiler_attributes_ordered_turns_and_keeps_ids_across_shots():
    shot = exchange()
    shot["dialogue_turns"][0]["speaker"] = "mango"
    shot["dialogue_turns"][1]["speaker"] = "blueberry"
    original = copy.deepcopy(shot)
    first = h3_prompt.compile_h3_prompt(shot, context(), first_frame=True)
    reply = h3_prompt.compile_h3_prompt(
        {"speaker": "blueberry", "dialogue": "Yes.", "prompt": "She nods."}, context()
    )
    assert "Mango (S1)" in first
    assert "Blueberry (S2)" in first and "Blueberry (S2)" in reply
    assert first.index("Still think") < first.index("Check the number")
    assert first.count("<d>") == 2
    assert "<d>[English] Check the number again.</d>" in first
    assert "Mango's smile disappears." in first
    assert "One continuous shot." in first
    assert shot == original


@pytest.mark.parametrize("bad", [
    {"speaker": "stranger", "text": "Hello."},
    {"speaker": "mango", "text": "Mango: Hello. Blueberry: No."},
])
def test_each_turn_requires_unambiguous_plain_speech(bad):
    shot = exchange()
    shot["dialogue_turns"].append(bad)
    with pytest.raises(ValueError):
        series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())


def test_no_dialogue_does_not_request_complete_silence():
    shot = exchange()
    shot.pop("dialogue_turns")
    shot["soundscape"] = "Quiet footsteps and soft clothing movement in the hallway."
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    normalized = plan["scenes"][0]["shots"][0]
    assert normalized["audio_mode"] == "native"
    compiled = h3_prompt.compile_h3_prompt(normalized, context())
    assert "overall_soundscape: Quiet footsteps" in compiled
    assert "<d>" not in compiled
    assert "remain silent" in compiled


def test_explicit_total_silence_remains_available():
    compiled = h3_prompt.compile_h3_prompt({"prompt": "A reveal.", "audio_mode": "silent"})
    assert "overall_soundscape: N/A" in compiled


def test_synthesized_music_has_measured_level_before_configured_gain(tmp_path):
    path, _ = audio.render_stems({"music": "tension", "ambience": "none"}, [3], tmp_path)
    with wave.open(str(path)) as stream:
        import array
        samples = array.array("h", stream.readframes(stream.getnframes()))
    rms = math.sqrt(sum((s / 32768) ** 2 for s in samples) / len(samples))
    assert -16 < 20 * math.log10(rms) < -10


def test_turns_and_soundscape_survive_persistence(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import production, series, service

    monkeypatch.setattr(series, "require_services", lambda: None)
    monkeypatch.setattr(queue_manager, "paused", True)
    created = client.post(
        "/api/studio/series", json={"title": "Test", "concept": "A dispute."}
    ).json()
    project = client.post(f"/api/studio/series/{created['id']}/episodes",
                          json={"idea": "A reversal", "request_id": "one"}).json()["project_id"]
    shot = exchange()
    shot["soundscape"] = "Very quiet room ambience."
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    plan["audio"]["sfx"] = [{"effect": "impact", "shot_index": 0, "at_sec": 2, "gain_db": -24}]
    production.save_episode_plan(project, plan)
    board = service.board(project)
    first = board["scenes"][0]["shots"][0]
    with service.database() as conn:
        saved = service.shot_context(conn, first["id"])
        config = json.loads(conn.execute(
            "SELECT settings_json FROM studio_audio WHERE project_id=?", (project,)
        ).fetchone()[0])
    assert len(saved["shot"]["dialogue_turns"]) == 2
    assert saved["shot"]["soundscape"] == "Very quiet room ambience."
    assert config["sfx"][0]["shot_id"] == first["id"]


def test_sound_cue_does_not_jump_to_another_shot_when_a_shot_is_deleted(client):
    from calliope.studio import service

    project = service.create_project({"title": "Cue", "aspect_ratio": "9:16"})["id"]
    with service.database() as conn:
        service.add_scene(conn, project, {"shots": [{"prompt": "A."}, {"prompt": "B."}]})
        conn.commit()
    shots = service.board(project)["scenes"][0]["shots"]
    audio.save_audio(project, {"sfx": [
        {"effect": "click", "shot_index": 1, "shot_id": shots[1]["id"]}
    ]})
    with service.database() as conn:
        conn.execute("DELETE FROM clips WHERE id=?", (shots[0]["id"],))
        conn.commit()
        frozen = audio.snapshot_audio(conn, project)
        assert audio.get_audio(conn, project)["sfx"][0]["shot_index"] == 0
    assert frozen["sfx"][0]["shot_index"] == 0
    assert frozen["sfx"][0]["shot_id"] == shots[1]["id"]


def test_social_series_receives_music_unless_its_profile_requests_none():
    shot = exchange()
    config = context() | {"directing_profile": {"preset": "fast_dialogue_social_fiction"}}
    plan = {"scenes": [{"shots": [shot]}], "audio": {"music": "none"}}
    result = series_planner.normalize_plan(copy.deepcopy(plan), config)
    assert result["audio"]["music"] == "tension"
    assert result["audio"]["music_gain_db"] == -18
    config["directing_profile"]["music"] = "none"
    assert series_planner.normalize_plan(plan, config)["audio"]["music"] == "none"


def test_ambience_has_its_requested_measured_level(tmp_path):
    _, path = audio.render_stems({"music": "none", "ambience": "room",
                                 "ambience_gain_db": -34}, [3], tmp_path)
    with wave.open(str(path)) as stream:
        import array
        samples = array.array("h", stream.readframes(stream.getnframes()))
    rms = math.sqrt(sum((s / 32768) ** 2 for s in samples) / len(samples))
    assert 20 * math.log10(rms) == pytest.approx(-34, abs=0.5)


def test_native_audio_cannot_be_silently_replaced_at_export(tmp_path, media):
    import asyncio

    from calliope.studio.export import assemble

    clip = media[1]
    with pytest.raises(ValueError, match="piste audio"):
        asyncio.run(assemble(
            {"aspect_ratio": "9:16", "clips": [{"path": str(clip), "audio_mode": "native"}]},
            tmp_path / "out.mp4",
        ))


def test_deleting_a_shot_keeps_its_render_evidence(client):
    from pathlib import Path

    from calliope.config import settings
    from calliope.studio import service

    project = service.create_project({"title": "Evidence"})["id"]
    with service.database() as conn:
        service.add_scene(conn, project, {"shots": [{"prompt": "First."}, {"prompt": "Second."}]})
        conn.commit()
    shot = service.board(project)["scenes"][0]["shots"][0]
    response = client.delete(f"/api/studio/projects/{project}/shots/{shot['id']}")
    assert response.status_code == 200
    archive = Path(settings.assets_dir) / str(project) / "studio/diagnostics"
    saved = json.loads(next(archive.glob("deleted-shot-*.json")).read_text(encoding="utf-8"))
    assert saved["shot"]["id"] == shot["id"]
    assert saved["shot"]["prompt"] == "First."
    assert "takes" in saved


def test_missing_second_turn_is_detected_even_if_most_of_the_shot_is_present():
    from calliope.studio import transcription

    turns = [{"speaker": "a", "text": "I am better than you and everyone knows it."},
             {"speaker": "b", "text": "Check the number again."}]
    text = turns[0]["text"]
    transcript = {"status": "ok", "text": text, "segments": [
        {"words": [{"word": word, "probability": 0.99} for word in text.split()]}
    ]}
    assert transcription.assess_turns(turns, transcript)["status"] == "missing"


def test_missing_native_ambience_is_rejected_before_export(client, media):
    import asyncio

    from calliope.studio import production, service

    project = service.create_project({"title": "Missing ambience"})["id"]
    with service.database() as conn:
        service.add_scene(conn, project, {"shots": [{"prompt": "A door opens."}]})
        conn.commit()
    shot = service.board(project)["scenes"][0]["shots"][0]
    take = service.store_upload(shot["id"], media[1].read_bytes(), "video")
    service.select_take(take["id"])
    verdict = asyncio.run(production.assess_take(production.load_shot(shot["id"])))
    assert verdict["status"] == "inaudible"


def test_turn_edits_and_exact_labels_reach_the_export_snapshot(client, monkeypatch, media):
    from calliope.queue.manager import queue_manager
    from calliope.studio import production, series, service

    monkeypatch.setattr(series, "require_services", lambda: None)
    monkeypatch.setattr(queue_manager, "paused", True)
    created = client.post(
        "/api/studio/series", json={"title": "Labels", "concept": "A reversal."}
    ).json()
    project = client.post(f"/api/studio/series/{created['id']}/episodes",
                          json={"idea": "A reversal", "request_id": "one"}).json()["project_id"]
    shot = exchange()
    shot["screen_text"] = [{"key": "score", "text": "12,349", "x": 0.5, "y": 0.2,
                            "entity_key": "Mango"}]
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    plan["entities"] = context()["entities"]
    production.save_episode_plan(project, plan)
    first = service.board(project)["scenes"][0]["shots"][0]
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='paused' WHERE project_id=?", (project,))
        conn.commit()
    edited = client.patch(f"/api/studio/projects/{project}/shots/{first['id']}", json={
        "dialogue_turns": [{"speaker": "Blueberry", "text": "I can prove it."}]
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["dialogue"] == "I can prove it."
    with service.database() as conn:
        saved = service.shot_context(conn, first["id"])
        assert saved["shot"]["dialogue_turns"][0]["speaker"] == "blueberry"
    cleared = client.patch(f"/api/studio/projects/{project}/shots/{first['id']}", json={
        "dialogue": "", "dialogue_turns": []
    })
    assert cleared.status_code == 200
    take = service.store_upload(first["id"], media[0].read_bytes(), "video")
    service.select_take(take["id"])
    client.post(f"/api/studio/shots/{first['id']}/approve", json={"approved": True})
    response = client.post(f"/api/studio/projects/{project}/export", json={"request_id": "label"})
    assert response.status_code == 200, response.text
    snapshot = json.loads(queue_manager.get_job(response.json()["job_id"])["payload_json"])
    assert snapshot["clips"][0]["screen_text"][0]["text"] == "12,349"


def test_display_canon_comes_from_the_exported_shots(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import series, service

    monkeypatch.setattr(series, "require_services", lambda: None)
    monkeypatch.setattr(queue_manager, "paused", True)
    created = client.post(
        "/api/studio/series", json={"title": "Canon", "concept": "A score."}
    ).json()
    project = client.post(f"/api/studio/series/{created['id']}/episodes",
                          json={"idea": "A score", "request_id": "one"}).json()["project_id"]
    with service.database() as conn:
        conn.execute("UPDATE studio_episodes SET canon_revision=1,plan_json=? WHERE project_id=?",
                     (json.dumps({"display_values": {"score": "999"}}), project))
        conn.execute(
            "INSERT INTO studio_exports(project_id,request_id,revision,aspect_ratio,snapshot_json,"
            "output_path) VALUES(?,?,?,?,?,?)",
            (project, "canon", 1, "9:16", json.dumps({"clips": [
                {"screen_text": [{"key": "score", "text": "100"}]}
            ]}), "export.mp4"),
        )
        conn.commit()
        assert series.context(conn, created["id"])["display_values"] == {"score": "100"}
