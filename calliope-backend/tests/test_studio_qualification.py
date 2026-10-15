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


@pytest.mark.parametrize("listed", [[], ["Blueberry"]])
def test_planner_includes_a_known_speaker_omitted_from_visual_references(listed):
    shot = exchange()
    shot.pop("dialogue_turns")
    shot.update(speaker="Mango", dialogue="Look at me.", character_keys=listed)
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    normalized = plan["scenes"][0]["shots"][0]
    assert normalized["speaker"] == "mango"
    assert normalized["dialogue"] == "Look at me."
    assert "mango" in normalized["character_keys"]
    assert "speaking characters visibly" in normalized["prompt"]
    assert "speaking characters visibly" in normalized["image_prompt"]


def test_planner_includes_both_known_speakers_in_a_continuous_exchange():
    shot = exchange()
    shot["character_keys"] = []
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    shots = plan["scenes"][0]["shots"]
    assert len(shots) == 1
    assert shots[0]["character_keys"] == ["mango", "blueberry"]
    assert len(shots[0]["dialogue_turns"]) == 2
    assert "Mango, Blueberry" in shots[0]["image_prompt"]


def test_unspoken_speaker_field_does_not_block_a_reaction_shot():
    shot = exchange()
    shot.pop("dialogue_turns")
    shot.update(speaker="Mango", dialogue="", character_keys=["blueberry"])
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    normalized = plan["scenes"][0]["shots"][0]
    assert normalized["speaker"] == ""
    assert normalized["character_keys"] == ["blueberry"]
    assert normalized["audio_mode"] == "native"


def test_planner_identifies_all_undeclared_locations_and_unassigned_system_voices():
    shots = []
    for location in ("ultra-luxury-maternity", "academy-corridor", "maximilian-office"):
        shot = exchange()
        shot["location_key"] = location
        shots.append(shot)
    shots[0]["dialogue_turns"] = [{
        "speaker": "", "text": 'REGISTRY: "Value confirmed."'
    }]
    with pytest.raises(ValueError) as failure:
        series_planner.normalize_plan({"scenes": [{"shots": shots}]}, context())
    error = str(failure.value)
    for index, shot in enumerate(shots):
        assert f"scenes[0].shots[{index}].location_key" in error
        assert shot["location_key"] in error
    assert "dialogue_turns[0].speaker" in error
    assert "REGISTRY" in error


def test_dialogue_turn_speaker_is_recovered_from_an_unambiguous_known_prefix():
    shot = exchange()
    shot["dialogue_turns"][0].update(speaker="", text='Mango: "Look at me."')
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    turn = plan["scenes"][0]["shots"][0]["dialogue_turns"][0]
    assert turn["speaker"] == "mango"
    assert turn["text"] == "Look at me."


def test_planner_maps_a_chime_notification_to_an_existing_audible_effect():
    plan = {"scenes": [{"shots": [exchange()]}], "audio": {
        "sfx": [{"effect": "chime", "shot_index": 0, "at_sec": 0.2}]
    }}
    normalized = series_planner.normalize_plan(plan, context())
    assert normalized["audio"]["sfx"][0]["effect"] == "beep"


def test_planner_still_rejects_an_unsupported_sound_effect():
    plan = {"scenes": [{"shots": [exchange()]}], "audio": {
        "sfx": [{"effect": "unsupported", "shot_index": 0, "at_sec": 0.2}]
    }}
    with pytest.raises(ValueError):
        series_planner.normalize_plan(plan, context())


@pytest.mark.asyncio
async def test_planner_recovers_visibility_on_last_attempt_and_preserves_raw_outputs(
    monkeypatch, tmp_path
):
    from calliope.config import settings

    missing_voice = exchange()
    missing_voice["dialogue_turns"][0]["speaker"] = ""
    missing_visual_reference = exchange()
    missing_visual_reference.pop("dialogue_turns")
    missing_visual_reference.update(speaker="mango", dialogue="Look at me.",
                                    character_keys=["blueberry"], duration_sec=5)
    consequence = exchange()
    consequence.update(description="Mango sees the proof and loses his smile.", duration_sec=4)
    responses = [
        {"scenes": [{"shots": []}]},
        {"scenes": [{"shots": [missing_voice]}]},
        {"scenes": [{"shots": [missing_visual_reference, consequence]}]},
    ]
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        return copy.deepcopy(responses[len(requests) - 1])

    monkeypatch.setattr(settings, "assets_dir", tmp_path)
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    config = context() | {"target_seconds": 13, "_planner_trace": {
        "project_id": 15, "job_id": 162
    }}
    plan = await series_planner.plan_episode(config, "A confrontation", "")
    assert len(requests) == 3
    assert plan["scenes"][0]["shots"][0]["character_keys"] == ["blueberry", "mango"]
    assert "_planner_trace" not in requests[0][1]["content"]
    traces = sorted(tmp_path.glob("15/studio/diagnostics/planner-162-*-attempt-*.json"))
    assert len(traces) == 3
    records = [json.loads(path.read_text(encoding="utf-8")) for path in traces]
    assert "scène vide" in records[0]["validation_error"]
    assert "Locuteur inconnu" in records[1]["validation_error"]
    assert records[2]["validation_error"] is None
    assert records[2]["response"] == responses[2]
    assert records[2]["messages"] == requests[2]


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


def test_social_series_does_not_add_music_automatically():
    shot = exchange()
    config = context() | {"directing_profile": {"preset": "fast_dialogue_social_fiction"}}
    plan = {"scenes": [{"shots": [shot]}], "audio": {"music": "none"}}
    result = series_planner.normalize_plan(copy.deepcopy(plan), config)
    assert result["audio"]["music"] == "none"
    assert result["audio"]["music_gain_db"] == -12
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
        "/api/studio/series", json={"title": "Labels", "concept": "A reversal.",
                                   "directing_profile": {"cta_placement": "middle"}}
    ).json()
    project = client.post(f"/api/studio/series/{created['id']}/episodes",
                          json={"idea": "A reversal", "request_id": "one"}).json()["project_id"]
    shot = exchange()
    shot["screen_text"] = [{"key": "score", "text": "12,349", "x": 0.5, "y": 0.2,
                            "entity_key": "Mango"}]
    payoff = exchange()
    payoff.update(description="Mango sees the proof.", dialogue_turns=[], duration_sec=1)
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot, payoff]}]}, context())
    plan["entities"] = context()["entities"]
    plan = series_planner.insert_midroll(plan, context())
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
    async def assess(*args):
        return {"status": "ok", "reason": ""}

    monkeypatch.setattr(production, "assess_take", assess)
    for current in service.board(project)["scenes"][0]["shots"]:
        take = service.store_upload(current["id"], media[0].read_bytes(), "video")
        service.select_take(take["id"])
        approved = client.post(f"/api/studio/shots/{current['id']}/approve", json={"approved": True})
        assert approved.status_code == 200, approved.text
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
