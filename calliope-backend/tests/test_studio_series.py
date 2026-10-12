import asyncio
import base64
import json
from pathlib import Path

import pytest

from calliope.config import settings
from calliope.queue.manager import queue_manager
from tests.test_studio import mock_studio_telemetry as mock_studio_telemetry
from tests.test_studio import png_bytes, video_bytes


@pytest.fixture(autouse=True)
def isolated_production(monkeypatch, client):
    previous = queue_manager.paused
    queue_manager.paused = True
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    monkeypatch.setattr(settings, "studio_image_base_url", "http://localhost:9002/v1")
    yield
    queue_manager.paused = previous


def series(client, title="The lighthouse", concept="A keeper hears tomorrow's radio."):
    response = client.post("/api/studio/series", json={"title": title, "concept": concept})
    assert response.status_code == 200, response.text
    return response.json()


def episode(client, identifier, idea="The signal names the keeper.", request="first"):
    return client.post(
        f"/api/studio/series/{identifier}/episodes",
        json={"idea": idea, "request_id": request},
    )


def miniature_target(project_id):
    from calliope.studio import service

    with service.database() as conn:
        row = conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        context = json.loads(row[0])
        context["target_seconds"] = 0.6
        conn.execute(
            "UPDATE studio_episodes SET context_json=? WHERE project_id=?",
            (json.dumps(context), project_id),
        )
        conn.commit()


def test_series_bibles_and_episode_requests_are_separate(client):
    a = series(client)
    b = series(client, "Sunny bakery", "A cheerful baker saves the village.")
    response = client.patch(
        f"/api/studio/series/{a['id']}",
        json={"bible": {"world_rules": "Radio messages come from tomorrow."}},
    )
    assert response.status_code == 200, response.text
    assert client.get(f"/api/studio/series/{b['id']}").json()["bible"]["world_rules"] == ""
    first = episode(client, a["id"])
    assert first.status_code == 200, first.text
    assert episode(client, a["id"]).json()["project_id"] == first.json()["project_id"]
    second = episode(client, b["id"])
    assert second.status_code == 200, second.text
    assert second.json()["project_id"] != first.json()["project_id"]
    assert len(client.get(f"/api/studio/series/{a['id']}").json()["episodes"]) == 1
    assert len(client.get(f"/api/studio/series/{b['id']}").json()["episodes"]) == 1
    assert episode(client, a["id"], idea="A different request").status_code == 409


def test_next_episode_waits_for_previous_and_missing_services_create_no_jobs(client, monkeypatch):
    a = series(client)
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", None)
    result = episode(client, a["id"])
    assert result.status_code == 422
    assert not client.get(f"/api/studio/series/{a['id']}").json()["episodes"]
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    assert episode(client, a["id"]).status_code == 200
    assert episode(client, a["id"], request="second").status_code == 409


def test_blank_concept_and_unknown_bible_fields_are_rejected(client):
    assert client.post("/api/studio/series", json={"title": "X", "concept": " "}).status_code == 422
    a = series(client)
    assert a["target_seconds"] == 65
    result = client.patch(f"/api/studio/series/{a['id']}", json={"bible": {"other_series_id": 3}})
    assert result.status_code == 422


def planned_episode(context):
    return {
        "title": "The signal",
        "summary": "The keeper discovers the radio warns about his own future.",
        "events": ["The keeper hears his name on tomorrow's radio."],
        "ongoing_plot": "Who is broadcasting?",
        "bible": {
            "universe": "An isolated coastal lighthouse.",
            "visual_style": "Realistic, cold blue light.",
        },
        "entities": [
            {
                "key": "keeper",
                "kind": "character",
                "name": "The keeper",
                "description": "Adult keeper in a yellow coat.",
            }
        ],
        "scenes": [
            {
                "heading": "INT. LIGHTHOUSE",
                "action": "A radio crackles.",
                "shots": [
                    {
                        "description": "The keeper hears his name.",
                        "prompt": "A keeper listens to a radio.",
                        "image_prompt": "Keeper in a lighthouse, blue light.",
                        "dialogue": "That's my name. Who is calling?",
                        "speaker": "keeper",
                        "character_keys": ["keeper"],
                        "location_key": "",
                        "reference_key": "keeper-at-radio",
                        "duration_sec": 5,
                        "audio_mode": "native",
                    },
                    {
                        "description": "The keeper discovers tomorrow's date on the radio.",
                        "prompt": "The keeper sees an impossible date and reacts.",
                        "image_prompt": "Keeper at the same radio, blue light.",
                        "dialogue": "", "speaker": "", "character_keys": ["keeper"],
                        "duration_sec": 1.6, "audio_mode": "native",
                    }
                ],
            }
        ],
        "audio": {
            "music": "tension",
            "music_gain_db": -28,
            "voice_gain_db": -5,
            "ambience": "room",
            "sfx": [{"effect": "click", "shot_index": 0, "at_sec": 0.2, "gain_db": -20}],
        },
    }


def test_automatic_production_exports_and_next_episode_uses_only_own_history(
    client, monkeypatch, tmp_path
):
    from calliope.engines.modal_h3 import ModalH3Engine
    from calliope.studio import production, service

    contexts = []

    async def plan(context, idea, objective):
        contexts.append(context)
        return planned_episode(context)

    async def image(request, *, check_cancel=None):
        return service.normalized_image(png_bytes())

    async def assess(shot):
        return {"status": "ok", "reason": ""}

    monkeypatch.setattr(production, "plan_episode", plan)
    monkeypatch.setattr(production, "assess_take", assess)
    monkeypatch.setattr(service, "_generate_image", image)
    content = video_bytes(tmp_path, audio=True)

    def submit(self, request):
        return "test-call"

    def poll(self, call_id):
        return {"video_base64": base64.b64encode(content).decode(), "metadata": {}}

    monkeypatch.setattr(ModalH3Engine, "spawn", submit)
    monkeypatch.setattr(ModalH3Engine, "poll", poll)
    a, b = series(client), series(client, "Sunny bakery", "The baker loses her recipe.")
    first = episode(client, a["id"]).json()
    miniature_target(first['project_id'])
    job = queue_manager.get_job(first["job_id"])
    outputs = asyncio.run(
        production.run_production(job, json.loads(job["payload_json"]), lambda: False)
    )
    queue_manager.mark_done(job["id"], outputs)
    board = client.get(f"/api/studio/projects/{first['project_id']}").json()
    assert board["production"]["status"] == "done"
    assert Path(board["exports"][0]["output_path"]).is_file()
    assert board["exports"][0]["metadata"]["has_audio"]
    assert board["scenes"][0]["shots"][0]["approved"]
    next_episode = episode(client, a["id"], request="second").json()
    miniature_target(next_episode['project_id'])
    job = queue_manager.get_job(next_episode["job_id"])
    asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
    assert "tomorrow's radio" in json.dumps(contexts[-1])
    assert len(contexts[-1]["history"]) == 1
    assert "Sunny bakery" not in json.dumps(contexts[-1])
    assert not client.get(f"/api/studio/series/{b['id']}").json()["episodes"]


def test_audio_changes_invalidate_export_without_invalidating_selected_takes(client, tmp_path):
    from tests.test_studio import new_board, upload

    project_id, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    take = upload(client, shot["id"], video_bytes(tmp_path, audio=True), "video")
    client.post(f"/api/studio/takes/{take['id']}/select").raise_for_status()
    client.post(
        f"/api/studio/shots/{shot['id']}/approve", json={"approved": True}
    ).raise_for_status()
    result = client.patch(
        f"/api/studio/projects/{project_id}/audio", json={"music": "warm", "music_gain_db": -30}
    )
    assert result.status_code == 200, result.text
    updated = client.get(f"/api/studio/projects/{project_id}").json()
    assert updated["audio"]["music"] == "warm"
    assert updated["scenes"][0]["shots"][0]["selected_video_id"] == take["id"]
    assert updated["scenes"][0]["shots"][0]["approved"]


def test_script_edit_is_atomic_and_refuses_a_foreign_shot(client):
    from tests.test_studio import new_board

    first, board = new_board(client)
    _, other = new_board(client)
    mine = board["scenes"][0]["shots"][0]
    theirs = other["scenes"][0]["shots"][0]
    result = client.patch(
        f"/api/studio/projects/{first}/script",
        json={
            "shots": [
                {"id": mine["id"], "dialogue": "Change me."},
                {"id": theirs["id"], "dialogue": "Foreign."},
            ]
        },
    )
    assert result.status_code == 404
    unchanged = client.get(f"/api/studio/projects/{first}").json()["scenes"][0]["shots"][0]
    assert unchanged["dialogue"] == mine["dialogue"]
    result = client.patch(
        f"/api/studio/projects/{first}/script",
        json={"shots": [{"id": mine["id"], "dialogue": "A new line."}]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["scenes"][0]["shots"][0]["dialogue"] == "A new line."


def test_planner_rejects_unidentified_and_multiple_speakers():
    from calliope.studio.series_planner import normalize_plan

    plan = planned_episode({})
    shot = plan["scenes"][0]["shots"][0]
    shot["dialogue"] = "Keeper: Hello.\nStranger: Goodbye."
    with pytest.raises(ValueError, match="plusieurs voix"):
        normalize_plan(plan, {"entities": []})
    shot["dialogue"] = "Hello."
    shot["speaker"] = "foreign-person"
    with pytest.raises(ValueError, match="locuteur"):
        normalize_plan(plan, {"entities": []})


def test_agnes_25_images_send_references_as_documented_json(monkeypatch, tmp_path):
    import httpx

    from calliope.studio import service

    reference = tmp_path / "reference.png"
    reference.write_bytes(png_bytes())
    original_client = httpx.AsyncClient
    monkeypatch.setattr(settings, "studio_image_base_url", "https://apihub.agnes-ai.com/v1")
    monkeypatch.setattr(settings, "studio_image_api_key", "test-image-key")

    def receive(request):
        assert request.url.path == "/v1/images/generations"
        body = json.loads(request.content)
        assert body["size"] == "1K"
        assert body["ratio"] == "9:16"
        assert body["extra_body"]["image"] == [
            "data:image/png;base64," + base64.b64encode(reference.read_bytes()).decode()
        ]
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(**kw, transport=httpx.MockTransport(receive)),
    )
    content, metadata = asyncio.run(
        service._generate_image(
            {
                "image_base_url": settings.studio_image_base_url,
                "image_model": "agnes-image-2.5-flash",
                "aspect_ratio": "9:16",
                "prompt": "Use this character in a workshop.",
                "reference_paths": [str(reference)],
            }
        )
    )
    assert content.startswith(b"\x89PNG")
    assert metadata["width"] > 0


def test_agnes_text_shortcut_uses_25_without_exposing_key(client, monkeypatch):
    monkeypatch.setattr(settings, "studio_image_api_key", "test-private-image-key")
    monkeypatch.setattr(type(settings), "save_config_file", lambda self: None)
    result = client.patch("/api/studio/config", json={"use_agnes_for_text": True})
    assert result.status_code == 200, result.text
    assert result.json()["llm_model"] == "agnes-2.5-flash"
    assert "test-private-image-key" not in result.text


def test_planner_requires_requested_episode_duration():
    import copy

    from calliope.studio.series_planner import normalize_plan

    plan = planned_episode({})
    with pytest.raises(ValueError, match="trop courte"):
        normalize_plan(plan, {"entities": [], "target_seconds": 70})
    plan = planned_episode({})
    plan["scenes"][0]["shots"] = plan["scenes"][0]["shots"][:1] * 14
    result = normalize_plan(copy.deepcopy(plan), {"entities": [], "target_seconds": 70})
    assert sum(s["duration_sec"] for s in result["scenes"][0]["shots"]) == 70


def test_production_resumes_original_modal_call_without_another_take(client, monkeypatch, tmp_path):
    from calliope.engines.modal_h3 import ModalH3Engine
    from calliope.studio import production, service

    async def plan(context, idea, objective):
        return planned_episode(context)

    async def image(request, *, check_cancel=None):
        return service.normalized_image(png_bytes())

    async def assess(shot):
        return {"status": "ok", "reason": ""}

    calls = []
    content = video_bytes(tmp_path, audio=True)

    def spawn(self, request):
        calls.append(
            "midroll-call" if "Subscribe, copy the link" in request["prompt"] else "original-call"
        )
        return calls[-1]

    def interrupted(self, call_id):
        assert call_id == "original-call"
        raise RuntimeError("Temporary polling failure")

    monkeypatch.setattr(production, "plan_episode", plan)
    monkeypatch.setattr(production, "assess_take", assess)
    monkeypatch.setattr(service, "_generate_image", image)
    monkeypatch.setattr(ModalH3Engine, "spawn", spawn)
    monkeypatch.setattr(ModalH3Engine, "poll", interrupted)
    first = episode(client, series(client)["id"]).json()
    miniature_target(first['project_id'])
    job = queue_manager.get_job(first["job_id"])
    with pytest.raises(RuntimeError, match="Temporary polling"):
        asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
    queue_manager.mark_failed(job["id"], "Temporary polling failure")
    assert (
        client.post(f"/api/studio/projects/{first['project_id']}/production/resume").status_code
        == 200
    )
    monkeypatch.setattr(
        ModalH3Engine,
        "poll",
        lambda self, cid: {"video_base64": base64.b64encode(content).decode(), "metadata": {}},
    )
    asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
    board = service.board(first["project_id"])
    takes = board["scenes"][0]["shots"][0]["takes"]
    assert len([t for t in takes if t["kind"] == "video"]) == 1
    assert calls == ["original-call", "midroll-call", "original-call"]
    assert board["production"]["status"] == "done"


def test_pause_before_writing_dispatches_no_provider(client, monkeypatch):
    from calliope.studio import production, service

    async def forbidden(*args):
        pytest.fail("A paused episode must not call a provider")

    monkeypatch.setattr(production, "plan_episode", forbidden)
    first = episode(client, series(client)["id"]).json()
    client.post(f"/api/studio/projects/{first['project_id']}/production/pause").raise_for_status()
    job = queue_manager.get_job(first["job_id"])
    assert (
        asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
        == []
    )
    assert service.board(first["project_id"])["production"]["status"] == "paused"


def test_audio_assets_cannot_cross_episode_boundaries(client, tmp_path):
    from tests.test_studio import new_board

    first, _ = new_board(client)
    other, _ = new_board(client)
    imported = client.post(
        f"/api/studio/projects/{first}/audio/upload",
        data={"kind": "music"},
        files={"file": ("bed.mp4", video_bytes(tmp_path, audio=True), "video/mp4")},
    )
    assert imported.status_code == 200, imported.text
    rejected = client.patch(
        f"/api/studio/projects/{other}/audio", json={"music_asset_id": imported.json()["id"]}
    )
    assert rejected.status_code == 422


def test_generation_replay_accepts_existing_pre_series_request(client):
    import hashlib

    from calliope.studio import service
    from tests.test_studio import new_board

    _, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    data = {"kind": "video", "request_id": "legacy-request", "n_takes": 1}
    queued = service.enqueue_takes(shot["id"], data)
    take = queued["takes"][0]
    with service.database() as conn:
        row = conn.execute(
            "SELECT request_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()
        parameters = json.loads(row[0])
        parameters.pop("seed")
        for key in ("trace_id", "context", "expected_dialogue"):
            parameters.pop(key, None)
        parameters['prompt'] = shot['prompt']
        parameters.pop("reference_paths", None)
        digest = hashlib.sha256(
            json.dumps(
                {"parameters": parameters, "n_takes": 1, "seed": None}, sort_keys=True
            ).encode()
        ).hexdigest()
        conn.execute(
            "UPDATE studio_takes SET request_hash=?,request_json=? WHERE id=?",
            (digest, json.dumps(parameters | {'seed': take['seed']}), take["id"]),
        )
        conn.commit()
    assert service.enqueue_takes(shot["id"], data)["takes"][0]["id"] == take["id"]


def test_70_second_script_keeps_large_output_budget_on_json_retry(monkeypatch):
    from calliope.agent import llm
    from calliope.studio.series_planner import plan_episode

    budgets = []
    plan = planned_episode({})
    plan["scenes"][0]["shots"][0]["duration_sec"] = 4.4
    plan["scenes"][0]["shots"] = plan["scenes"][0]["shots"][:1] * 14

    class Client:
        async def chat(self, messages, **options):
            budgets.append(options["max_tokens"])
            return "not JSON" if len(budgets) == 1 else json.dumps(plan)

        async def close(self):
            pass

    monkeypatch.setattr(llm, "LLMClient", Client)
    result = asyncio.run(plan_episode({"target_seconds": 70, "entities": []}, "A warning", ""))
    assert len(result["scenes"][0]["shots"]) == 15
    # Le nombre de plans est libre ; le budget suit la cible, ~23 plans estimés.
    assert budgets == [16000, 16000]


def test_planner_repairs_short_episode_before_any_gpu_render(monkeypatch):
    from calliope.studio import series_planner

    requests = []

    async def generate(messages, **options):
        requests.append(list(messages))
        plan = planned_episode({})
        if len(requests) > 1:
            plan["scenes"][0]["shots"][0]["duration_sec"] = 4.4
            plan["scenes"][0]["shots"] = plan["scenes"][0]["shots"][:1] * 14
        return plan

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = asyncio.run(
        series_planner.plan_episode(
            {"target_seconds": 70, "entities": []}, "The radio answers early.", ""
        )
    )
    assert len(requests) == 2
    assert "70" in requests[1][-1]["content"]
    assert "6.6" in requests[1][-1]["content"]
    assert len(result["scenes"][0]["shots"]) == 15


def test_dialogue_free_shots_keep_native_ambient_sound():
    from calliope.studio.series_planner import normalize_plan

    plan = planned_episode({})
    shot = plan["scenes"][0]["shots"][0]
    shot["dialogue"] = ""
    shot["speaker"] = ""
    result = normalize_plan(plan, {"entities": []})
    assert result["scenes"][0]["shots"][0]["audio_mode"] == "native"


def test_midroll_language_and_speaker_survive_episode_persistence(client):
    from calliope.studio import production, series_planner, service
    from calliope.studio.h3_prompt import compile_h3_prompt

    created = series(client)
    client.patch(f"/api/studio/series/{created['id']}", json={"language": "fr"})
    project = episode(client, created["id"]).json()["project_id"]
    plan = series_planner.insert_midroll(planned_episode({}), {"entities": [], "language": "fr"})
    production.save_episode_plan(project, plan)
    board = service.board(project)
    ending = board["scenes"][0]["shots"][1]
    with service.database() as conn:
        context = service.shot_context(conn, ending["id"])
    assert context["shot"]["speaker"] == "keeper"
    assert context["shot"]["language"] == "fr"
    assert context["shot"]["purpose"] == "midroll"
    compiled = compile_h3_prompt(ending | context["shot"], context, first_frame=True)
    assert "<d>[French] Abonne-toi, copie le lien et commente GO !</d>" in compiled


def test_resume_adds_missing_midroll_once_to_an_existing_episode(client):
    from calliope.studio import production, service

    created = series(client)
    project = episode(client, created["id"]).json()["project_id"]
    production.save_episode_plan(project, planned_episode({}))
    production.ensure_midroll(project)
    production.ensure_midroll(project)
    board = service.board(project)
    assert len(board["scenes"]) == 1
    assert len(board["scenes"][0]["shots"]) == 3
    assert board["scenes"][0]["shots"][0]["dialogue"] == "That's my name. Who is calling?"
    assert board["scenes"][0]["shots"][1]["dialogue"] == (
        "Subscribe, copy the link and comment GO!"
    )


@pytest.mark.parametrize("language", ["en", "fr"])
def test_shot_language_controls_speech_validation_in_a_french_series(
    client, monkeypatch, tmp_path, language
):
    from calliope.studio import production, series_planner, service, transcription
    from tests.test_studio_pacing import make_clip

    created = series(client)
    client.patch(f"/api/studio/series/{created['id']}", json={"language": "fr"})
    project = episode(client, created["id"]).json()["project_id"]
    plan = series_planner.insert_midroll(planned_episode({}), {"entities": [], "language": language})
    production.save_episode_plan(project, plan)
    ending = service.board(project)["scenes"][0]["shots"][1]
    clip = make_clip(tmp_path / "midroll.mp4", 4, tone_windows=[(0.1, 3.8)])
    take = service.store_upload(ending["id"], clip.read_bytes(), "video")
    service.select_take(take["id"])

    def recognize(path, checkpoint, language):
        text = {
            "en": (
                "Subscribe, copy the link and comment GO!"
            ),
            "fr": (
                "Abonne-toi, copie le lien et commente GO !"
            ),
        }[language]
        return {
            "status": "ok", "text": text,
            "segments": [{"words": [{"word": w, "probability": 0.99} for w in text.split()]}],
            "speech_intervals": [[0.1, 3.8]], "reason": "",
        }

    monkeypatch.setattr(transcription, "transcribe_dialogue", recognize)
    verdict = asyncio.run(production.assess_take(production.load_shot(ending["id"])))
    assert verdict["status"] == "ok"
    metadata = production.selected_video(production.load_shot(ending["id"]))["metadata"]
    assert metadata["transcription_cache"]["signature"][-1] == language


def test_resume_preserves_a_legacy_english_midroll_after_language_change(client):
    from calliope.studio import production, series_planner, service

    created = series(client)
    client.patch(f"/api/studio/series/{created['id']}", json={"language": "fr"})
    project = episode(client, created["id"]).json()["project_id"]
    old = series_planner.insert_midroll(planned_episode({}), {"entities": [], "language": "en"})
    production.save_episode_plan(project, old)
    ending_id = service.board(project)["scenes"][0]["shots"][1]["id"]
    production.ensure_midroll(project)
    production.ensure_midroll(project)
    board = service.board(project)
    assert len(board["scenes"]) == 1
    assert board["scenes"][0]["shots"][0]["dialogue"] == "That's my name. Who is calling?"
    ending = board["scenes"][0]["shots"][1]
    assert ending["id"] == ending_id
    assert ending["dialogue"] == (
        "Subscribe, copy the link and comment GO!"
    )
    with service.database() as conn:
        assert service.shot_context(conn, ending_id)["shot"]["language"] == "en"
