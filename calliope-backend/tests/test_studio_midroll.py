import copy
import json

import pytest

from calliope.studio import series_planner


def story():
    return {
        "entities": [
            {
                "key": "keeper",
                "kind": "character",
                "name": "Keeper",
                "description": "A keeper in a yellow coat.",
            },
            {
                "key": "tower",
                "kind": "location",
                "name": "Tower",
                "description": "An old lighthouse.",
            },
        ],
        "scenes": [
            {
                "heading": "Tower",
                "shots": [
                    {
                        "description": f"Event {index}",
                        "dialogue": f"Line {index}.",
                        "speaker": "keeper",
                        "character_keys": ["keeper"],
                        "location_key": "tower",
                        "duration_sec": 4,
                        "audio_mode": "native",
                        "prompt": "Keeper speaks.",
                    }
                    for index in range(6)
                ],
            }
        ],
        "audio": {"sfx": [{"effect": "click", "shot_index": 4, "at_sec": 0.2}], "holds": []},
    }


def insert(plan, language="fr"):
    operation = getattr(series_planner, "insert_midroll", None)
    assert callable(operation), "The studio still adds a promotional outro."
    return operation(plan, {"entities": [], "language": language})


def persisted_episode(client, monkeypatch, plan=None):
    from calliope.config import settings
    from calliope.queue.manager import queue_manager
    from calliope.studio import production, series, service

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(series, "require_services", lambda **kwargs: None)
    identifier = client.post(
        "/api/studio/series", json={"title": "Tower", "concept": "A keeper.",
                                   "directing_profile":{"cta_placement":"middle"}}
    ).json()["id"]
    project = series.create_episode(identifier, {"idea": "A warning.", "request_id": "saved"})[
        "project_id"
    ]
    planned = plan or insert(story())
    planned.update(
        summary="The keeper hears a warning.",
        events=["A warning."],
        ongoing_plot="Who is calling?",
        bible={},
        editorial_review={
            "hook": "A voice names the keeper.",
            "payoff": "The keeper finds the radio.",
        },
    )
    production.save_episode_plan(project, planned)
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='paused' WHERE project_id=?", (project,))
        conn.commit()
    return project


def test_continuation_skips_the_midroll(client, monkeypatch, tmp_path):
    from calliope.studio import service
    from tests.test_studio import video_bytes

    project = persisted_episode(client, monkeypatch)
    shots = service.board(project)["scenes"][0]["shots"]
    take = service.store_upload(shots[2]["id"], video_bytes(tmp_path, audio=True), "video")
    service.select_take(take["id"])
    with service.database() as conn:
        previous = service._previous_video(conn, shots[4]["id"])
    assert previous["id"] == take["id"]


def test_editorial_review_is_exposed_and_marked_stale_after_edit(client, monkeypatch):
    from calliope.studio import service

    project = persisted_episode(client, monkeypatch)
    board = service.board(project)
    assert board["episode"]["editorial_review"]["hook"] == "A voice names the keeper."
    assert board["episode"]["editorial_stale"] is False
    service.update_shot(
        project, board["scenes"][0]["shots"][0]["id"], {"dialogue": "A new warning."}
    )
    assert service.board(project)["episode"]["editorial_stale"] is True


def test_script_edit_updates_the_spoken_turn_and_preserves_its_speaker(client, monkeypatch):
    from calliope.studio import service
    from calliope.studio.h3_prompt import compile_h3_prompt

    plan = insert(story())
    plan["scenes"][0]["shots"][0]["dialogue_turns"] = [{"speaker": "keeper", "text": "Line 0."}]
    project = persisted_episode(client, monkeypatch, plan)
    shot = service.board(project)["scenes"][0]["shots"][0]
    response = client.patch(
        f"/api/studio/projects/{project}/script",
        json={
            "shots": [{"id": shot["id"], "dialogue": "A new warning."}],
        },
    )
    assert response.status_code == 200, response.text
    with service.database() as conn:
        context = service.shot_context(conn, shot["id"])
    saved = service.board(project)["scenes"][0]["shots"][0]
    prompt = compile_h3_prompt(saved, context)
    assert "A new warning.</d>" in prompt
    assert "Line 0.</d>" not in prompt
    assert saved["dialogue_turns"][0]["speaker"] == "keeper"


def test_plain_script_edit_cannot_flatten_an_exchange(client, monkeypatch):
    from calliope.studio import service

    plan = insert(story())
    plan["scenes"][0]["shots"][0]["dialogue_turns"] = [
        {"speaker": "keeper", "text": "Line 0."},
        {"speaker": "keeper", "text": "One more question."},
    ]
    project = persisted_episode(client, monkeypatch, plan)
    shot = service.board(project)["scenes"][0]["shots"][0]
    response = client.patch(
        f"/api/studio/projects/{project}/script",
        json={
            "shots": [{"id": shot["id"], "dialogue": "A new exchange."}],
        },
    )
    assert response.status_code == 422
    assert (
        service.board(project)["scenes"][0]["shots"][0]["dialogue_turns"] == shot["dialogue_turns"]
    )


@pytest.mark.asyncio
async def test_promo_is_excluded_from_canon_history(client, monkeypatch):
    from calliope.studio import production, series, service

    project = persisted_episode(client, monkeypatch)
    with service.database() as conn:
        revision = conn.execute(
            "SELECT revision FROM studio_projects WHERE project_id=?", (project,)
        ).fetchone()[0]
    await production.commit_episode(project, revision)
    with service.database() as conn:
        history = series.context(conn, service.board(project)["episode"]["series_id"])["history"]
    assert len(history[0]["shots"]) == 6
    assert all("comment" not in shot["dialogue"].lower() for shot in history[0]["shots"])


@pytest.mark.asyncio
async def test_migrating_the_promo_does_not_hide_story_edits_from_the_recap(client, monkeypatch):
    from calliope.studio import production, service

    project = persisted_episode(client, monkeypatch, story())
    first = service.board(project)["scenes"][0]["shots"][0]
    service.update_shot(project, first["id"], {"dialogue": "My new story event."})
    production.ensure_midroll(project)
    recaps = []

    async def recap(messages, **kwargs):
        recaps.append(json.loads(messages[-1]["content"]))
        return {
            "summary": "The story has changed.",
            "events": ["A new event."],
            "ongoing_plot": "Why?",
        }

    monkeypatch.setattr("calliope.agent.llm.generate_structured", recap)
    with service.database() as conn:
        revision = conn.execute(
            "SELECT revision FROM studio_projects WHERE project_id=?", (project,)
        ).fetchone()[0]
    await production.commit_episode(project, revision)
    assert len(recaps) == 1
    assert recaps[0][0]["dialogue"] == "My new story event."
    assert len(recaps[0]) == 6


def test_legacy_migration_preserves_story_edits_and_old_takes(client, monkeypatch, tmp_path):
    from calliope.studio import production, service
    from tests.test_studio import video_bytes

    plan = story()
    plan["scenes"].append(
        {
            "heading": "Outro",
            "shots": [
                {
                    "purpose": "outro",
                    "duration_sec": 8,
                    "speaker": "keeper",
                    "character_keys": ["keeper"],
                    "dialogue": "Want to make videos too?",
                    "audio_mode": "native",
                    "prompt": "Old promotion.",
                }
            ],
        }
    )
    project = persisted_episode(client, monkeypatch, plan)
    board = service.board(project)
    old_id = board["scenes"][-1]["shots"][0]["id"]
    take = service.store_upload(old_id, video_bytes(tmp_path, audio=True), "video")
    service.select_take(take["id"])
    service.update_shot(
        project, board["scenes"][0]["shots"][0]["id"], {"dialogue": "My edited line."}
    )
    production.ensure_midroll(project)
    after = service.board(project)
    production.ensure_midroll(project)
    final = service.board(project)
    assert final["project"]["revision"] == after["project"]["revision"]
    shots = [shot for scene in final["scenes"] for shot in scene["shots"]]
    assert shots[0]["dialogue"] == "My edited line."
    assert shots[3]["id"] == old_id
    assert shots[3]["selected_video_id"] is None
    assert shots[3]["takes"][0]["id"] == take["id"]
    assert shots[-1]["description"] == "Event 5"


@pytest.mark.asyncio
async def test_series_export_refuses_a_missing_or_ending_midroll(client, monkeypatch):
    from fastapi import HTTPException

    from calliope.studio import service

    project = persisted_episode(client, monkeypatch, story())
    with pytest.raises(HTTPException, match="incitation"):
        await service.enqueue_export(project, "missing-promo")
    project = persisted_episode(client, monkeypatch)
    shots = service.board(project)["scenes"][0]["shots"]
    with service.database() as conn:
        conn.execute("UPDATE clips SET order_index=99 WHERE id=?", (shots[3]["id"],))
        conn.commit()
    with pytest.raises(HTTPException, match="incitation"):
        await service.enqueue_export(project, "ending-promo")


def test_a_completed_legacy_episode_can_prepare_its_midroll_without_rendering(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import service

    plan = story()
    plan["scenes"].append(
        {
            "heading": "Outro",
            "shots": [
                {
                    "purpose": "outro",
                    "duration_sec": 8,
                    "speaker": "keeper",
                    "dialogue": "Old promotion.",
                    "prompt": "An old outro.",
                }
            ],
        }
    )
    project = persisted_episode(client, monkeypatch, plan)
    queue_manager.mark_done(service.board(project)["production"]["job_id"], [])
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='done' WHERE project_id=?", (project,))
        before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.commit()
    response = client.post(f"/api/studio/projects/{project}/midroll")
    assert response.status_code == 200, response.text
    shots = [shot for scene in response.json()["scenes"] for shot in scene["shots"]]
    assert shots[3]["purpose"] == "midroll"
    assert not any(shot["purpose"] == "outro" for shot in shots)
    assert not any(shot["takes"] for shot in shots)
    with service.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before


def test_incitation_is_in_the_middle_and_preserves_the_narrative_ending():
    plan = story()
    original = copy.deepcopy(plan["scenes"][0]["shots"])
    result = insert(plan)
    shots = [shot for scene in result["scenes"] for shot in scene["shots"]]
    assert shots[3]["purpose"] == "midroll"
    assert shots[3]["duration_sec"] == 4
    assert shots[3]["speaker"] == "keeper"
    assert shots[3]["location_key"] == "tower"
    assert shots[3]["dialogue"] == "Abonne-toi, copie le lien et commente GO !"
    assert shots[:3] + shots[4:] == original
    assert all(entity["key"] != "studio_cta_host" for entity in result["entities"])
    assert result["audio"]["sfx"][0]["shot_index"] == 5
    assert result["audio"]["holds"][-1] == {
        "shot_index": 3,
        "at_sec": 0,
        "duration_sec": 4,
        "drop_db": -90,
    }


def test_midroll_is_idempotent_and_localized():
    result = insert(story(), "en")
    frozen = copy.deepcopy(result)
    assert insert(result, "en") == frozen
    shot = result["scenes"][0]["shots"][3]
    assert shot["dialogue"] == "Subscribe, copy the link and comment GO!"
    assert [label["text"] for label in shot["screen_text"]] == [
        "Subscribe",
        "Copy the link",
        "Comment GO",
    ]


def test_legacy_outro_is_removed_without_removing_the_story_payoff():
    plan = story()
    plan["scenes"].append(
        {"shots": [{"purpose": "outro", "duration_sec": 8, "speaker": "studio_cta_host"}]}
    )
    result = insert(plan)
    shots = [shot for scene in result["scenes"] for shot in scene["shots"]]
    assert len(shots) == 7
    assert sum(shot.get("purpose") == "midroll" for shot in shots) == 1
    assert shots[-1]["description"] == "Event 5"
    assert not any(shot.get("purpose") == "outro" for shot in shots)


def test_middle_is_selected_by_duration_instead_of_shot_count():
    plan = story()
    durations = [1, 1, 1, 8, 8, 1]
    for shot, duration in zip(plan["scenes"][0]["shots"], durations):
        shot["duration_sec"] = duration
    shots = insert(plan)["scenes"][0]["shots"]
    assert shots[4]["purpose"] == "midroll"


def test_phone_text_is_a_postproduction_overlay_and_the_voice_is_spoken_once():
    from calliope.studio.h3_prompt import compile_h3_prompt

    plan = insert(story())
    shot = plan["scenes"][0]["shots"][3]
    assert [label["text"] for label in shot["screen_text"]] == [
        "Abonne-toi",
        "Copie le lien",
        "Commente GO",
    ]
    compiled = compile_h3_prompt(shot, {"entities": plan["entities"], "language": "fr"})
    assert compiled.count("<d>") == 1
    assert "spoken exactly once" in compiled
    assert "Generate no text" in compiled
    assert "without ambience" in compiled
    assert "zoom" in compiled.lower()
    assert "points" in compiled.lower()


def test_prepare_only_creates_an_explicit_review_gate_without_media_services(client, monkeypatch):
    from calliope.config import settings
    from calliope.queue.manager import queue_manager

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    monkeypatch.setattr(settings, "studio_image_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "studio_image_api_key", None)
    series = client.post(
        "/api/studio/series", json={"title": "Tower", "concept": "A keeper."}
    ).json()
    response = client.post(
        f"/api/studio/series/{series['id']}/episodes",
        json={
            "idea": "The radio warns the keeper.",
            "request_id": "draft",
            "prepare_only": True,
        },
    )
    assert response.status_code == 200, response.text
    job = queue_manager.get_job(response.json()["job_id"])
    assert json.loads(job["payload_json"])["prepare_only"] is True


@pytest.mark.asyncio
async def test_preparation_and_retry_never_render_until_explicit_resume(client, monkeypatch):
    from calliope.config import settings
    from calliope.queue.manager import queue_manager
    from calliope.studio import production, series, service

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(series, "require_services", lambda **kwargs: None)
    identifier = client.post(
        "/api/studio/series", json={"title": "Tower", "concept": "A keeper."}
    ).json()["id"]
    run = series.create_episode(
        identifier,
        {
            "idea": "A warning.",
            "request_id": "draft",
            "prepare_only": True,
        },
    )

    async def plan(*args):
        return insert(story()) | {
            "summary": "A warning.",
            "events": ["A warning."],
            "ongoing_plot": "Who is calling?",
            "bible": {},
        }

    async def media(*args):
        pytest.fail("Preparation must not generate media.")

    monkeypatch.setattr(production, "plan_episode", plan)
    monkeypatch.setattr(production, "ensure_entities", media)
    job = queue_manager.get_job(run["job_id"])
    payload = json.loads(job["payload_json"])
    for _ in range(2):
        assert await production.run_production(job, payload, lambda: False) == []
    board = service.board(run["project_id"])
    assert board["production"]["stage"] == "review"
    assert board["production"]["status"] == "paused"
    assert not any(shot["takes"] for scene in board["scenes"] for shot in scene["shots"])
    queue_manager.mark_done(job["id"], [])
    series.resume(run["project_id"])
    resumed = queue_manager.get_job(run["job_id"])
    assert not json.loads(resumed["payload_json"]).get("prepare_only")


def test_restarting_a_prepared_episode_preserves_its_review_pause(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import series, service

    project = persisted_episode(client, monkeypatch)
    with service.database() as conn:
        frozen = json.loads(
            conn.execute(
                "SELECT context_json FROM studio_episodes WHERE project_id=?", (project,)
            ).fetchone()[0]
        )
        frozen["prepare_only"] = True
        conn.execute(
            "UPDATE studio_episodes SET context_json=? WHERE project_id=?",
            (json.dumps(frozen), project),
        )
        conn.commit()
    queue_manager.mark_done(service.board(project)["production"]["job_id"], [])
    series.restart(project, "restart-prepared")
    board = service.board(project)
    job = queue_manager.get_job(board["production"]["job_id"])
    assert json.loads(job["payload_json"])["prepare_only"] is True


def test_phone_is_actually_composited_into_the_export(tmp_path):
    import asyncio
    import io
    import subprocess

    from PIL import Image

    from calliope.studio import export
    from tests.test_studio_pacing import make_clip

    source = make_clip(tmp_path / "source.mp4", 0.5, tone_windows=[(0, 0.5)])
    shot = insert(story())["scenes"][0]["shots"][3]
    destination = tmp_path / "phone.mp4"
    result = asyncio.run(
        export.assemble(
            {
                "aspect_ratio": "9:16",
                "audio": {"enabled": True, "music": "none", "holds": []},
                "clips": [
                    {
                        "path": str(source),
                        "audio_mode": "native",
                        "purpose": "midroll",
                        "screen_text": shot["screen_text"],
                    }
                ],
            },
            destination,
        )
    )
    overlay = result["screen_text"][0]
    assert overlay.get("phone") is True
    assert result["audio"]["holds"] == [
        {"shot_index": 0, "at_sec": 0, "duration_sec": 0.5, "drop_db": -90}
    ]
    assert [label["text"] for label in overlay["labels"]] == [
        "Abonne-toi",
        "Copie le lien",
        "Commente GO",
    ]
    frame = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.2",
            "-i",
            str(destination),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
    )
    image = Image.open(io.BytesIO(frame)).convert("RGB")
    width, height = image.size
    text_pixels = image.crop(
        (int(width * 0.52), int(height * 0.57), int(width * 0.84), int(height * 0.77))
    ).get_flattened_data()
    assert sum(min(pixel) > 180 for pixel in text_pixels) > 100
    scale = min(width / 160, height / 90)
    cy = (height - 90 * scale) / 2 + 90 * scale * 0.67
    cx = width * 0.68
    phone_width = min(160 * 0.42, 90 * 0.25) * scale
    outside = image.crop(
        (0, int(cy - 90 * scale * 0.09), int(cx - phone_width / 2 - 6), int(cy + 90 * scale * 0.09))
    )
    assert sum(min(pixel) > 180 for pixel in outside.get_flattened_data()) == 0


def test_imported_sound_effects_are_muted_during_incitation_but_voice_survives(tmp_path):
    import subprocess

    from calliope.studio import audio
    from tests.test_studio_sound_design import rms, samples

    source, effect = tmp_path / "voice.mov", tmp_path / "effect.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=24:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=3000:duration=3",
            "-c:v",
            "libx264",
            "-c:a",
            "pcm_s16le",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=3",
            str(effect),
        ],
        check=True,
        capture_output=True,
    )
    outputs = []
    for quiet in (False, True):
        config = audio.AudioSettings(music="none", voice_gain_db=0, ducking=False).model_dump()
        config["sfx"] = [{"shot_index": 0, "at_sec": 0, "gain_db": 0, "path": str(effect)}]
        if quiet:
            config["holds"] = [{"shot_index": 0, "at_sec": 1, "duration_sec": 1, "drop_db": -90}]
        destination = tmp_path / f"mixed-{quiet}.mp4"
        subprocess.run(
            audio.mix_command(
                ["ffmpeg", "-v", "error", "-y"],
                source,
                config,
                [3],
                tmp_path,
                destination,
            ),
            check=True,
            capture_output=True,
        )
        outputs.append(destination)
    normal, quiet = outputs
    lowpass = "lowpass=f=400,lowpass=f=400"
    assert rms(samples(quiet, 1.3, 0.4, lowpass)) < rms(samples(normal, 1.3, 0.4, lowpass)) * 0.02
    assert rms(samples(quiet, 0.3, 0.4, lowpass)) == pytest.approx(
        rms(samples(normal, 0.3, 0.4, lowpass)),
        rel=0.1,
    )
    assert rms(samples(quiet, 1.3, 0.4, "highpass=f=2000")) == pytest.approx(
        rms(samples(normal, 1.3, 0.4, "highpass=f=2000")),
        rel=0.03,
    )
