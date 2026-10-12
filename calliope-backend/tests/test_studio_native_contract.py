import copy
import json

import pytest

from calliope.studio import production, series_planner, service
from tests.test_studio_midroll import persisted_episode, story
from tests.test_studio_export import media as media


@pytest.fixture(autouse=True)
def source_story_for_realization_tests(monkeypatch):
    async def write(context, idea, objective, trace=None):
        plan = native_plan()
        return {"title": "Le coffre", "narrative_outline": plan["narrative_outline"],
                "beats": [{"action": "Le gardien réagit.", "dialogue_turns": [
                    {"speaker": shot["speaker"], "text": shot["dialogue"]}]} for shot in plan["scenes"][0]["shots"]]}, {}
    monkeypatch.setattr(series_planner, "_develop_creative_story", write)


def realization_plan():
    plan = native_plan()
    for shot in plan["scenes"][0]["shots"]:
        shot["state_changes"] = []
    return plan


def test_native_identifier_case_is_normalized_without_changing_prose():
    plan = native_plan()
    for entity in plan["entities"]:
        entity["key"] = entity["key"].upper()
    for kind in ("characters", "locations", "props"):
        plan["render_canon"][kind] = {key.upper(): value for key, value in plan["render_canon"][kind].items()}
    for shot in plan["scenes"][0]["shots"]:
        shot["character_keys"] = [key.upper() for key in shot["character_keys"]]
        shot["location_key"] = shot["location_key"].upper()
        shot["speaker"] = shot["speaker"].upper()
        shot["prop_keys"] = ["CASE"]
        for field in ("start_state", "end_state"):
            shot[field] = {key.upper(): value for key, value in shot[field].items()}
    result = series_planner.normalize_plan(plan, {"entities": []})
    assert set(result["render_canon"]["characters"]) == {"keeper"}
    assert result["scenes"][0]["shots"][0]["prop_keys"] == ["case"]
    assert result["scenes"][0]["shots"][0]["start_state"] == {"case": "Closed on the desk."}
    assert result["render_canon"]["style"] == "Ink animation."


def test_native_identifier_case_collision_is_rejected():
    plan = native_plan()
    plan["render_canon"]["props"]["CASE"] = "A different case."
    with pytest.raises(ValueError, match="collision"):
        series_planner.normalize_plan(plan, {"entities": []})


@pytest.mark.asyncio
async def test_native_repair_can_replace_a_malformed_scene_list(monkeypatch):
    calls = []

    async def generate(messages, **options):
        calls.append(messages)
        plan = realization_plan()
        if len(calls) == 1:
            plan["scenes"] = None
            return plan
        return {"values": {"field_1": plan["scenes"]}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(calls) == 2
    assert result["scenes"]


@pytest.mark.asyncio
async def test_native_repair_applies_only_explicit_updates(monkeypatch):
    draft = realization_plan()
    draft["scenes"][0]["shots"][0]["prop_keys"] = ["unknown"]
    original = copy.deepcopy(draft)
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return draft
        return {"values": {"field_1": ["case"]}}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 2
    assert result["render_canon"] == original["render_canon"]
    assert result["scenes"][0]["shots"][0]["image_prompt"] == original["scenes"][0]["shots"][0]["image_prompt"]
    assert draft == original
    assert "values" in requests[1][0]["content"]


@pytest.mark.asyncio
async def test_native_repair_rejects_an_invalid_path_atomically(monkeypatch):
    requests = []
    draft = realization_plan()
    draft["scenes"][0]["shots"][0]["prop_keys"] = ["unknown"]

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return draft
        values = {"field_1": ["case"]}
        if len(requests) == 2:
            values["field_2"] = []
        return {"values": values}

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    assert len(requests) == 3
    assert '"unknown"' in requests[2][2]["content"]
    assert "values" in requests[2][-1]["content"]

@pytest.mark.asyncio
async def test_native_writer_receives_all_broken_links_and_exact_states(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        plan = realization_plan()
        shots = plan["scenes"][0]["shots"]
        if len(requests) > 1:
            from tests.test_studio_repair_values import closed_case_state_values
            return closed_case_state_values(messages)
        if len(requests) == 1:
            shots[1].update(
                continue_from_previous=True,
                start_state={"case": "Open on the floor."},
            )
            shots[2].update(
                continue_from_previous=True,
                start_state={"case": "Held by Keeper."},
            )
        return plan

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode(
        {"entities": [], "prompt_contract_version": 2, "target_seconds": 32.5, "language": "fr"},
        "Une fuite.", "",
    )
    repairs = [json.loads(request[-1]["content"]) for request in requests[1:]]
    assert [repair["take_index"] for repair in repairs] == [[0, index] for index in range(6)]
    assert repairs[1]["inherited_state"] == {"case": "Closed on the desk."}
    assert all("repair_fields" not in repair and "start_state" not in repair["take"] for repair in repairs)


def test_native_missing_state_reports_the_previous_value():
    plan = native_plan()
    shot = plan["scenes"][0]["shots"][1]
    shot.update(continue_from_previous=True, prop_keys=[], start_state={})
    with pytest.raises(ValueError) as error:
        series_planner.normalize_plan(plan, {"entities": []})
    assert "scenes[0].shots[1].start_state[case]" in str(error.value)
    assert '"Closed on the desk."' in str(error.value)
    assert "null" in str(error.value)


def test_failed_preparation_resume_preserves_review_before_media(client, monkeypatch):
    import json
    from calliope.queue.manager import queue_manager
    from calliope.studio import series

    monkeypatch.setattr(queue_manager, "paused", True)
    service_checks = []
    monkeypatch.setattr(series, "require_services", lambda **kwargs: service_checks.append(kwargs))
    identifier = client.post(
        "/api/studio/series", json={"title": "Tower", "concept": "A keeper."}
    ).json()["id"]
    run = series.create_episode(identifier, {
        "idea": "A warning.", "request_id": "failed-draft", "prepare_only": True,
    })
    with service.database() as conn:
        conn.execute("UPDATE jobs SET status='failed',error='Invalid draft' WHERE id=?", (run["job_id"],))
        conn.execute(
            "UPDATE studio_productions SET status='failed',stage='writing',error='Invalid draft' WHERE project_id=?",
            (run["project_id"],),
        )
        conn.commit()
    series.resume(run["project_id"])
    resumed = queue_manager.get_job(run["job_id"])
    assert json.loads(resumed["payload_json"]).get("prepare_only") is True
    assert service_checks[-1] == {"preparation_only": True}
    assert resumed["status"] == "pending"



def test_native_fields_survive_creation_and_edit(client, monkeypatch):
    plan = native_plan()
    plan["scenes"][0]["shots"][1].update(
        continue_from_previous=True, prop_keys=["case"],
        start_state={"case": "Closed."}, end_state={"case": "Open."}, cut_reason="Même action."
    )
    project = persisted_episode(client, monkeypatch, plan)
    shot = service.board(project)["scenes"][0]["shots"][1]
    assert shot["continue_from_previous"] is True
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
    assert context["prompt_contract_version"] == 2
    assert context["render_canon"]["style"] == "Ink animation."
    assert context["shot"]["start_state"] == {"case": "Closed."}
    response = client.patch(f"/api/studio/projects/{project}/shots/{shot['id']}", json={
        "start_state": {"case": "Open."}, "end_state": {"case": "Held by Keeper."},
        "cut_reason": "Réaction.", "prop_keys": ["case"], "action": "Keeper lifts the case.",
        "continue_from_previous": False
    })
    assert response.status_code == 200, response.text
    with service.database() as conn:
        edited = service.shot_context(conn, shot["id"])["shot"]
    assert edited["end_state"] == {"case": "Held by Keeper."}
    assert edited["action"] == "Keeper lifts the case."


def test_create_route_preserves_continuation(client):
    project = client.post("/api/studio/projects", json={"title": "Continuity"}).json()["id"]
    scene = client.post(f"/api/studio/projects/{project}/scenes", json={"heading": "Room"}).json()
    scene_id = scene["id"]
    result = client.post(f"/api/studio/projects/{project}/scenes/{scene_id}/shots", json={
        "continue_from_previous": True, "start_state": {"case": "Closed."}
    })
    assert result.status_code == 200, result.text
    shot = result.json()
    assert shot["continue_from_previous"] is True
    with service.database() as conn:
        assert service.shot_context(conn, shot["id"])["shot"]["start_state"] == {"case": "Closed."}


def native_plan():
    plan = story()
    plan.update(prompt_contract_version=2, speaker_ids={"keeper": "S1"}, narrative_outline={
        "opening": "Le coffre fuit.", "question": "Pourquoi ?", "progression": "Il est ouvert.",
        "payoff": "Un poisson en sort.", "ending": "Fin autonome."
    }, render_canon={
        "style": "Ink animation.",
        "characters": {"keeper": {"name": "Keeper", "appearance": "A yellow-coated keeper.",
                                  "voice": "A clear warm baritone."}},
        "locations": {"tower": "A round lighthouse room."},
        "props": {"case": "A small rectangular brass case with two black latches."}
    })
    for shot in plan["scenes"][0]["shots"]:
        shot.update(image_prompt="The brass case rests closed on the desk.",
                    prompt="Keeper stands at the desk.", prop_keys=["case"],
                    start_state={"case": "Closed on the desk."},
                    end_state={"case": "Closed on the desk."}, cut_reason="Réaction utile.")
    return plan


def test_native_normalization_rejects_missing_canon_and_unknown_props():
    plan = native_plan()
    del plan["render_canon"]
    with pytest.raises(ValueError, match="render_canon"):
        series_planner.normalize_plan(plan, {"entities": []})
    plan = native_plan()
    plan["scenes"][0]["shots"][0]["prop_keys"] = ["unknown"]
    with pytest.raises(ValueError, match="prop_keys"):
        series_planner.normalize_plan(plan, {"entities": []})


def test_native_cast_cannot_treat_a_location_as_a_character():
    plan = native_plan()
    plan["scenes"][0]["shots"][0]["character_keys"].append("tower")
    with pytest.raises(ValueError, match="référence"):
        series_planner.normalize_plan(plan, {"entities": []})


def test_native_voice_ids_follow_speech_not_entity_order():
    plan = native_plan()
    plan["entities"].insert(0, {"key": "extra", "kind": "character", "name": "Extra",
                                "description": "A silent visitor."})
    plan["render_canon"]["characters"]["extra"] = {
        "name": "Extra", "appearance": "A silent visitor.", "voice": "Quiet alto."
    }
    result = series_planner.normalize_plan(plan, {"entities": [], "speaker_ids": {"extra": "S1"}})
    assert result["speaker_ids"] == {"keeper": "S1"}
    assert result["scenes"][0]["shots"][0]["dialogue"] == "Line 0."


def test_native_midroll_does_not_inherit_conversational_blocking():
    from calliope.studio.h3_prompt import compile_h3_prompt
    plan = native_plan()
    for shot in plan["scenes"][0]["shots"]:
        shot["blocking"] = "All characters face each other across the desk, never the camera."
    result = series_planner.insert_midroll(plan, {"entities": [], "language": "fr"})
    midroll = next(s for c in result["scenes"] for s in c["shots"] if s.get("purpose") == "midroll")
    prompt = compile_h3_prompt(midroll, result | {"language": "fr"})
    assert "never the camera" not in prompt
    assert "points one finger toward the viewer" in prompt


def test_native_continuation_rejects_reset_but_allows_declared_cut():
    plan = native_plan()
    shot = plan["scenes"][0]["shots"][1]
    shot.update(continue_from_previous=True, start_state={"case": "Open on the floor."})
    with pytest.raises(ValueError, match="case"):
        series_planner.normalize_plan(copy.deepcopy(plan), {"entities": []})
    shot["continue_from_previous"] = False
    assert series_planner.normalize_plan(plan, {"entities": []})


def test_native_midroll_preserves_full_promise_and_story_states():
    result = series_planner.insert_midroll(native_plan(), {"entities": [], "language": "fr"})
    shots = result["scenes"][0]["shots"]
    promos = [shot for shot in shots if shot.get("purpose") == "midroll"]
    assert len(promos) == 1
    shot = promos[0]
    assert shot["dialogue"] == (
        "Abonne-toi, copie le lien et commente GO si tu veux recevoir un guide pour faire des vidéos gratuitement."
    )
    assert 4 < shot["duration_sec"] < 7
    assert not shot["screen_text"]
    assert "phone" not in " ".join(shot.get(key, "") for key in (
        "prompt", "image_prompt", "action", "framing")).lower()
    assert result["audio"]["holds"][-1]["duration_sec"] == shot["duration_sec"]
    assert shots[4]["start_state"] == shots[2]["end_state"]


def test_legacy_resume_preserves_saved_midroll(client, monkeypatch):
    project = persisted_episode(client, monkeypatch)
    with service.database() as conn:
        before = conn.execute("SELECT plan_json FROM studio_episodes WHERE project_id=?", (project,)).fetchone()[0]
    production.ensure_midroll(project)
    with service.database() as conn:
        after = conn.execute("SELECT plan_json FROM studio_episodes WHERE project_id=?", (project,)).fetchone()[0]
    assert after == before


def test_native_h3_orders_events_and_keeps_english_canon_separate():
    from calliope.studio.h3_prompt import compile_h3_prompt
    context = native_plan() | {"language": "fr", "speaker_ids": {"keeper": "S1"},
                               "bible": {"continuity": "Le coffre est déjà ouvert."}}
    shot = context["scenes"][0]["shots"][0] | {
        "action": "Keeper lifts the case.",
        "dialogue_turns": [{"speaker": "keeper", "text": "Qui a ouvert ça ?",
                            "action": "Keeper taps the latch.", "reaction": "Keeper recoils."}],
        "reaction": "A fish jumps out.", "camera": "The camera stays static.",
        "framing": "Wide shared composition."
    }
    prompt = compile_h3_prompt(shot, context, first_frame=True)
    assert prompt.index("lifts the case") < prompt.index("taps the latch") < prompt.index("Qui a ouvert ça ?")
    assert prompt.index("Qui a ouvert ça ?") < prompt.index("Keeper recoils") < prompt.index("fish jumps out")
    assert prompt.count("Qui a ouvert ça ?") == 1
    assert "déjà ouvert" not in prompt
    assert "warm baritone" in prompt and "Keeper (S1)" in prompt
    assert "<Picture 1> (from [Shot 1])" in prompt


def test_native_image_contains_start_not_future_or_author_bible():
    from calliope.studio.visual_identity import compile_image_request
    context = native_plan() | {"bible": {"continuity": "Le volet est déjà fermé."}}
    shot = context["scenes"][0]["shots"][0] | {
        "image_prompt": "The metal shutter is fully raised.",
        "action": "The metal shutter descends.", "reaction": "The red lamp lights up."
    }
    prompt = compile_image_request(shot, context)["prompt"]
    assert "fully raised" in prompt and "rectangular brass case" in prompt
    assert "shutter descends" not in prompt and "red lamp lights up" not in prompt
    assert "déjà fermé" not in prompt


@pytest.mark.asyncio
async def test_native_reference_preparation_uses_the_saved_render_canon(client, monkeypatch):
    plan = native_plan()
    plan["bible"] = {"visual_style": "Cette explication française reste dans la bible."}
    project = persisted_episode(client, monkeypatch, plan)
    requests = []
    async def generate(request, *, check_cancel=None):
        requests.append(request)
        return b"test-reference", {}
    monkeypatch.setattr(service, "_generate_image", generate)
    with service.database() as conn:
        job_id = conn.execute("SELECT job_id FROM studio_productions WHERE project_id=?", (project,)).fetchone()[0]
    await production.ensure_entities({"id": job_id, "project_id": project}, lambda: False)
    assert len(requests) == 2
    assert all("Ink animation." in request["prompt"] for request in requests)
    assert all("explication française" not in request["prompt"] for request in requests)
    assert any("yellow-coated keeper" in request["prompt"] for request in requests)
    assert any("round lighthouse room" in request["prompt"] for request in requests)


def test_transcript_verifies_words_not_voice_identity():
    from calliope.studio.transcription import assess_turns
    verdict = assess_turns([{"speaker": "keeper", "text": "Pose la mallette."}],
                          {"status": "ok", "text": "Pose la mallette.", "segments": []}, "fr")
    assert verdict["status"] == "ok"
    assert verdict["speaker_identity_status"] == "unverified"
    assert verdict["turns"][0]["expected_speaker"] == "keeper"


def test_useful_window_preserves_a_measured_silent_payoff():
    from calliope.studio.pacing import useful_window
    start, end = useful_window(3, 8, [(0.5, 2.5)], preserve_end_sec=6.5)
    assert start <= 0.5 and 6.5 <= end <= 8


def test_native_export_keeps_unreviewed_action_before_and_after_speech():
    from calliope.studio.export import _window_for
    clip = {"prompt_contract_version": 2, "beat_sec": 3, "audio_mode": "native",
            "dialogue": "Pose la mallette.", "action": "Keeper lifts the case.",
            "reaction": "A fish jumps out.", "transcript": {"status": "ok", "speech_intervals": [[2, 2.5]], "segments": [
                {"words": [{"word": "Pose", "start": 2, "end": 2.5}]}]}}
    start, end = _window_for(clip, {"duration_sec": 8})
    assert start == 0 and end == 8


def test_prompt_preview_is_read_only_and_checks_project_ownership(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, native_plan())
    shot = service.board(project)["scenes"][0]["shots"][0]
    with service.database() as conn:
        before = list(conn.iterdump())
    path = f"/api/studio/projects/{project}/shots/{shot['id']}/prompt-preview"
    response = client.get(path)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_status"] == "pending_source"
    assert data["prompt_contract_version"] == 2
    assert "rectangular brass case" in data["image_prompt"]
    assert "<d>[English] Line 0.</d>" in data["video_prompt"]
    with service.database() as conn:
        assert list(conn.iterdump()) == before
    assert client.get(f"/api/studio/projects/{project + 1}/shots/{shot['id']}/prompt-preview").status_code == 404


def test_native_edit_can_make_a_silent_character_speak(client, monkeypatch):
    plan = native_plan()
    plan["entities"].append({"key": "extra", "kind": "character", "name": "Extra", "description": "A visitor."})
    plan["render_canon"]["characters"]["extra"] = {
        "name": "Extra", "appearance": "A visitor in blue.", "voice": "A clear alto."
    }
    project = persisted_episode(client, monkeypatch, plan)
    shot = service.board(project)["scenes"][0]["shots"][0]
    result = client.patch(f"/api/studio/projects/{project}/shots/{shot['id']}", json={
        "dialogue_turns": [{"speaker": "extra", "text": "Stop."}]
    })
    assert result.status_code == 200
    preview = client.get(f"/api/studio/projects/{project}/shots/{shot['id']}/prompt-preview")
    assert preview.status_code == 200, preview.text
    assert "Extra (S1) says: <d>[English] Stop.</d>" in preview.json()["video_prompt"]


def test_native_raw_dialogue_edit_keeps_a_speaker_id(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, native_plan())
    shot = service.board(project)["scenes"][0]["shots"][0]
    service.update_shot(project, shot["id"], {"dialogue_turns": [], "dialogue": "Stop."})
    preview = client.get(f"/api/studio/projects/{project}/shots/{shot['id']}/prompt-preview")
    assert preview.status_code == 200, preview.text
    assert "Keeper (S1) says: <d>[English] Stop.</d>" in preview.json()["video_prompt"]


def test_native_live_edit_rejects_undeclared_prop_without_saving(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, native_plan())
    shot = service.board(project)["scenes"][0]["shots"][0]
    response = client.patch(f"/api/studio/projects/{project}/shots/{shot['id']}", json={"prop_keys": ["unknown"]})
    assert response.status_code == 422
    assert service.board(project)["scenes"][0]["shots"][0]["prop_keys"] == ["case"]


def test_native_silent_mode_keeps_choreography_without_spoken_tags():
    from calliope.studio.h3_prompt import compile_h3_prompt
    context = native_plan()
    shot = context["scenes"][0]["shots"][0] | {"audio_mode": "silent", "dialogue_turns": [
        {"speaker": "keeper", "text": "Ignored.", "action": "Keeper taps the latch.", "reaction": "Keeper recoils."}
    ]}
    prompt = compile_h3_prompt(shot, context)
    assert "taps the latch" in prompt and "Keeper recoils" in prompt
    assert "<d>" not in prompt and "Ignored." not in prompt


def test_native_first_frame_prompt_does_not_repeat_internal_scene_inventory():
    from calliope.studio.h3_prompt import compile_h3_prompt
    context = native_plan()
    shot = context["scenes"][0]["shots"][0]
    original = copy.deepcopy(shot)
    prompt = compile_h3_prompt(shot, context, first_frame=True)
    assert "At the start, case:" not in prompt
    assert context["render_canon"]["locations"]["tower"] not in prompt
    assert context["render_canon"]["props"]["case"] not in prompt
    assert shot["prompt"] in prompt and "Keeper (S1)" in prompt
    assert "After the last line" in prompt
    assert shot == original


def test_native_trim_preserves_first_and_last_turn_gestures():
    from calliope.studio.export import _window_for
    clip = {"prompt_contract_version": 2, "beat_sec": 3, "audio_mode": "native", "dialogue": "Stop.",
            "dialogue_turns": [{"speaker": "keeper", "text": "Stop.", "action": "Keeper lifts the case.",
                                "reaction": "A fish jumps out."}],
            "transcript": {"status": "ok", "speech_intervals": [[2, 2.5]]}}
    assert _window_for(clip, {"duration_sec": 8}) == (0, 8)


def test_native_cta_ends_after_its_actual_speech_without_a_held_silence():
    from calliope.studio.export import _window_for
    clip = {"prompt_contract_version": 2, "purpose": "midroll", "beat_sec": 8.5,
            "audio_mode": "native", "dialogue": series_planner.FRENCH_MIDROLL,
            "action": "Keeper points toward the viewer.",
            "transcript": {"status": "ok", "speech_intervals": [[0.6, 6.5]]}}
    start, end = _window_for(clip, {"duration_sec": 12})
    assert 0.4 <= start <= 0.6 and 6.5 <= end < 6.7


def test_native_cta_trim_discards_waiting_before_and_after_the_phrase(client, monkeypatch):
    result = series_planner.insert_midroll(native_plan(), {"entities": [], "language": "fr"})
    shot = next(s for c in result["scenes"] for s in c["shots"] if s.get("purpose") == "midroll")
    shot.update(id=700, prompt_contract_version=2)
    monkeypatch.setattr(production, "selected_video", lambda _: {"metadata": {"duration_sec": 12}})
    cuts = []
    monkeypatch.setattr(service, "save_trim", lambda conn, identifier, start, end:
                        cuts.append((start, end)) or True)
    production.trim_take(shot, [(0.6, 6.5)])
    assert len(cuts) == 1
    assert 0.4 <= cuts[0][0] <= 0.6 and 6.5 <= cuts[0][1] < 6.7


def test_native_cta_performance_is_brisk_even_with_a_natural_series_profile():
    from calliope.studio.h3_prompt import compile_h3_prompt
    result = series_planner.insert_midroll(native_plan(), {"entities": [], "language": "fr"})
    shot = next(s for c in result["scenes"] for s in c["shots"] if s.get("purpose") == "midroll")
    prompt = compile_h3_prompt(shot, result | {"language": "fr"})
    assert "rapid but intelligible" in prompt
    assert prompt.count(series_planner.FRENCH_MIDROLL) == 1
    assert "turns toward" not in shot["action"]


def test_native_full_take_does_not_store_a_rounded_trim(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, native_plan())
    shot = service.board(project)["scenes"][0]["shots"][0] | {
        "action": "Keeper lifts the case.", "reaction": "A fish jumps out."
    }
    monkeypatch.setattr(production, "selected_video", lambda current: {"metadata": {"duration_sec": 5.166667}})
    saved = []
    monkeypatch.setattr(service, "save_trim", lambda *args: saved.append(args) or False)
    production.trim_take(shot, [(0.5, 2.0)])
    assert not saved


def test_native_malformed_scene_is_a_repairable_validation_error():
    plan = native_plan()
    plan["scenes"] = [None]
    with pytest.raises(ValueError, match="scenes"):
        series_planner.normalize_plan(plan, {"entities": []})


def test_native_action_edit_marks_editorial_review_stale(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, native_plan())
    shot = service.board(project)["scenes"][0]["shots"][0]
    assert not service.board(project)["episode"]["editorial_stale"]
    service.update_shot(project, shot["id"], {"reaction": "A fish jumps out."})
    assert service.board(project)["episode"]["editorial_stale"]


@pytest.mark.asyncio
async def test_new_writer_rejects_a_legacy_result_in_native_production(monkeypatch):
    async def generate(messages, **options):
        return story()
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError, match="prompt_contract_version|updates"):
        await series_planner.plan_episode({"entities": [], "prompt_contract_version": 2, "target_seconds": 28}, "Une fuite.", "")


@pytest.mark.asyncio
async def test_native_writer_repair_keeps_the_real_cta_budget(monkeypatch):
    requests = []
    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        result = realization_plan()
        if len(requests) == 1:
            result["scenes"][0]["shots"][0]["prop_keys"] = ["undeclared"]
            return result
        return {"values": {"field_1": ["case"]}}
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = await series_planner.plan_episode({"entities": [], "prompt_contract_version": 2,
                                               "target_seconds": 32.5, "language": "fr"}, "Une fuite.", "")
    assert len(requests) == 2
    cta_duration = series_planner._midroll_spec({"language": "fr"})["duration_sec"]
    assert f"{cta_duration:g}-second midroll" in requests[1][-1]["content"]
    assert sum(s["duration_sec"] for scene in result["scenes"] for s in scene["shots"]) == 24 + cta_duration


@pytest.mark.asyncio
async def test_native_writer_repairs_only_the_latest_draft(monkeypatch):
    requests = []
    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        plan = realization_plan()
        if len(requests) == 1:
            plan["scenes"][0]["shots"][0]["prop_keys"] = ["unknown_1"]
            return plan
        value = ["unknown_2"] if len(requests) == 2 else ["case"]
        return {"values": {"field_1": value}}
    monkeypatch.setattr(series_planner, "generate_structured", generate)
    await series_planner.plan_episode({"entities": [], "prompt_contract_version": 2,
                                      "target_seconds": 32.5, "language": "fr"}, "Une fuite.", "")
    assert len(requests[2]) == 4
    assert "unknown_2" in requests[2][2]["content"]
    assert "unknown_1" not in requests[2][2]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_real_export_removes_phone_only_for_native_contract(media, tmp_path, version):
    import io
    import subprocess
    from PIL import Image
    from calliope.studio import export

    output = tmp_path / f"midroll-{version}.mp4"
    result = await export.assemble({"aspect_ratio": "9:16", "clips": [{
        "path": str(media[0]), "audio_mode": "native", "purpose": "midroll",
        "prompt_contract_version": version,
        "screen_text": [{"key": "story-label", "text": "123", "x": 0.5, "y": 0.2}]
    }]}, output)
    frame = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(output),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"
    ])
    pixel = Image.open(io.BytesIO(frame)).convert("RGB").getpixel((490, 700))
    assert (pixel[2] > 200) is (version == 2)
    assert result["screen_text"][0]["labels"][0]["text"] == "123"
