import asyncio
import copy
import subprocess
from pathlib import Path

import pytest

from calliope.studio import service
from calliope.studio import source_video as source


@pytest.fixture(autouse=True)
def no_paid_text(monkeypatch):
    from calliope.studio import series_planner

    async def forbidden(*args, **kwargs):
        raise AssertionError("Aucun appel texte externe autorisé dans ces tests.")

    monkeypatch.setattr(series_planner, "generate_structured", forbidden)
    monkeypatch.setattr(source, "generate_structured", forbidden)


@pytest.fixture
def mp4(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=160x284:r=24:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=160x284:r=24:d=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def series_id(client):
    return client.post(
        "/api/studio/series",
        json={
            "title": "Fruit City",
            "concept": "Une ville de fruits.",
            "language": "fr",
        },
    ).json()["id"]


def test_real_analysis_detects_cut_and_extracts_frames_without_asr(mp4, tmp_path):
    analysis = source.analyze_media(mp4, tmp_path / "frames", "en", "")
    assert analysis["duration_sec"] == pytest.approx(4, abs=0.05)
    assert len(analysis["shots"]) == 2
    assert analysis["shots"][0]["end_sec"] == pytest.approx(2, abs=0.1)
    assert analysis["shots"][1]["start_sec"] == pytest.approx(2, abs=0.1)
    assert analysis["transcription"]["status"] == "unknown"
    assert analysis["visual_status"] == "unverified"
    assert all(__import__("pathlib").Path(s["frame_path"]).is_file() for s in analysis["shots"])


def test_word_timing_assigns_boundary_words_once():
    shots = [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]
    segments = [
        {
            "start": 1,
            "end": 3,
            "text": "Hello my friend",
            "words": [
                {"word": "Hello", "start": 1, "end": 1.4},
                {"word": "my", "start": 1.8, "end": 2.2},
                {"word": "friend", "start": 2.5, "end": 3},
            ],
        }
    ]
    source.assign_transcript(shots, segments)
    assert shots[0]["transcript"] == "Hello"
    assert shots[1]["transcript"] == "my friend"


def test_import_review_and_stale_approval(client, series_id, mp4):
    response = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("example.mp4", mp4.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 200, response.text
    item = client.get(
        f"/api/studio/series/{series_id}/video-sources/{response.json()['id']}"
    ).json()
    assert item["status"] == "ready", item
    path = f"/api/studio/series/{series_id}/video-sources/{item['id']}"
    assert client.post(path + "/approve", json={"expected_hash": item["hash"]}).status_code == 422
    reviewed = copy.deepcopy(item["analysis"]["shots"])
    for shot in reviewed:
        shot["speech_reviewed"] = True
        shot["notes"] = "Deux fruits se disputent dans la rue."
    changed = client.patch(path, json={"expected_hash": item["hash"], "shots": reviewed})
    assert changed.status_code == 200, changed.text
    assert client.post(path + "/approve", json={"expected_hash": item["hash"]}).status_code == 409
    approved = client.post(path + "/approve", json={"expected_hash": changed.json()["hash"]})
    assert approved.status_code == 200 and approved.json()["approved"]
    again = client.patch(path, json={"expected_hash": changed.json()["hash"], "shots": reviewed})
    assert not again.json()["approved"]
    other = client.post("/api/studio/series", json={"title": "Other", "concept": "Other"}).json()[
        "id"
    ]
    assert client.get(f"/api/studio/series/{other}/video-sources/{item['id']}").status_code == 404


def test_invalid_video_leaves_no_source_or_job(client, series_id):
    response = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("bad.mp4", b"not a video", "video/mp4")},
    )
    assert response.status_code == 422
    with service.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM studio_video_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_adaptation_uses_canon_and_refuses_unknown_speakers(monkeypatch):
    context = {
        "language": "fr",
        "entities": [
            {"key": "momo", "kind": "character", "name": "Momo"},
            {"key": "street", "kind": "location", "name": "Rue"},
        ],
        "source_video": {
            "analysis": {
                "duration_sec": 4,
                "shots": [
                    {"start_sec": 0, "end_sec": 4, "transcript": "Hello.", "notes": "Il salue."},
                ],
            }
        },
    }
    response = {
        "title": "Salut",
        "summary": "Momo salue.",
        "events": ["Momo salue."],
        "ongoing_plot": "",
        "beats": [
            {
                "source_index": 0,
                "action": "Momo salue.",
                "dialogue_turns": [{"speaker": "momo", "text": "Salut."}],
            }
        ],
    }

    async def llm(messages, **kwargs):
        assert "Hello." in messages[-1]["content"]
        assert "momo" in messages[-1]["content"]
        return copy.deepcopy(response)

    monkeypatch.setattr(source, "generate_structured", llm)
    story = asyncio.run(source.adapt_story(context, "Une adaptation originale.", ""))
    assert story["source_locked"]
    assert story["beats"][0]["dialogue_turns"] == [{"speaker": "momo", "text": "Salut."}]
    assert story["source_timeline"] == [{"start_sec": 0, "end_sec": 4}]
    response["beats"][0]["dialogue_turns"][0]["speaker"] = "stranger"
    with pytest.raises(ValueError, match="locuteur"):
        asyncio.run(source.adapt_story(context, "", ""))


def test_source_rhythm_refuses_drift_and_new_cuts():
    story = {"source_timeline": [{"start_sec": 0, "end_sec": 4}]}
    plan = {
        "scenes": [
            {
                "shots": [
                    {"source_beat_index": 0, "duration_sec": 2, "continue_from_previous": False},
                    {"source_beat_index": 0, "duration_sec": 2, "continue_from_previous": True},
                ]
            }
        ]
    }
    source.validate_rhythm(story, plan)
    plan["scenes"][0]["shots"][1]["continue_from_previous"] = False
    with pytest.raises(ValueError, match="coupe"):
        source.validate_rhythm(story, plan)
    plan["scenes"][0]["shots"][1]["continue_from_previous"] = True
    plan["scenes"][0]["shots"][1]["duration_sec"] = 5
    with pytest.raises(ValueError, match="durée"):
        source.validate_rhythm(story, plan)


def test_export_validates_actual_windows_against_measured_source():
    snapshot = {
        "source_reference": {
            "timeline": [{"start_sec": 0, "end_sec": 2}, {"start_sec": 2, "end_sec": 4}]
        },
        "clips": [
            {"state": {"source_beat_index": 0}, "continue_from_previous": False},
            {"state": {"source_beat_index": 0}, "continue_from_previous": True},
            {"state": {"source_beat_index": 1}, "continue_from_previous": False},
        ],
    }
    source.validate_export_rhythm(snapshot, [1, 1, 2])
    with pytest.raises(ValueError, match="durée"):
        source.validate_export_rhythm(snapshot, [0.4, 0.4, 2])
    snapshot["clips"][1]["continue_from_previous"] = False
    with pytest.raises(ValueError, match="coupe"):
        source.validate_export_rhythm(snapshot, [1, 1, 2])


def test_source_export_keeps_acting_time_after_a_short_line():
    from calliope.studio import export

    clip = {
        "audio_mode": "native",
        "dialogue": "Salut.",
        "beat_sec": 4,
        "prompt_contract_version": 2,
        "preserve_source_timing": True,
        "transcript": {"status": "ok", "speech_intervals": [[0.2, 1.0]]},
    }
    assert export._window_for(clip, {"duration_sec": 4.1}) == (0, 4)


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2] / "work/ep02-v4/final/EPISODE-02-V4-golden.mp4"
    ).is_file(),
    reason="V4 locale absente",
)
def test_v4_known_scene_changes_are_detected(tmp_path):
    film = Path(__file__).resolve().parents[2] / "work/ep02-v4/final/EPISODE-02-V4-golden.mp4"
    result = source.analyze_media(film, tmp_path / "frames", "fr", "")
    starts = [shot["start_sec"] for shot in result["shots"]]
    assert any(abs(start - 21.750) < 0.1 for start in starts)
    assert any(abs(start - 42.583) < 0.1 for start in starts)


def test_cut_review_revokes_approval_and_preserves_previous_frames(client, series_id, mp4):
    from urllib.parse import parse_qs, urlsplit

    uploaded = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("source.mp4", mp4.read_bytes(), "video/mp4")},
    ).json()
    item = source.get_source(series_id, uploaded["id"])
    for shot in item["analysis"]["shots"]:
        shot.update(speech_reviewed=True, transcript="Hello.", notes="Un salut.")
    item = source.review_source(series_id, item["id"], item["hash"], item["analysis"]["shots"])
    item = source.review_source(series_id, item["id"], item["hash"], approve=True)
    old_frame = Path(parse_qs(urlsplit(item["analysis"]["shots"][0]["frame_url"]).query)["path"][0])
    endpoint = f"/api/studio/series/{series_id}/video-sources/{item['id']}/cuts"
    edited = client.post(endpoint, json={"expected_hash": item["hash"], "cuts": [1, 2, 3]})
    assert edited.status_code == 200, edited.text
    value = edited.json()
    assert len(value["analysis"]["shots"]) == 4
    assert not value["approved"] and value["hash"] != item["hash"]
    assert not any(s["speech_reviewed"] for s in value["analysis"]["shots"])
    assert sum(bool(s["transcript"]) for s in value["analysis"]["shots"]) == 2
    assert old_frame.is_file()
    assert (
        client.post(endpoint, json={"expected_hash": item["hash"], "cuts": [2]}).status_code == 409
    )
    assert (
        client.post(endpoint, json={"expected_hash": value["hash"], "cuts": [0.5]}).status_code
        == 422
    )


def test_real_export_keeps_an_approved_fast_source_rhythm(mp4, tmp_path):
    from calliope.studio import export

    snapshot = {
        "aspect_ratio": "9:16",
        "source_reference": {"timeline": [{"start_sec": i, "end_sec": i + 1} for i in range(4)]},
        "clips": [
            {
                "path": str(mp4),
                "audio_mode": "silent",
                "trim_start_sec": i,
                "trim_end_sec": i + 1,
                "state": {"source_beat_index": i},
            }
            for i in range(4)
        ],
    }
    result = asyncio.run(export.assemble(snapshot, tmp_path / "remade.mp4"))
    assert result["width"] == 1080 and result["height"] == 1920
    assert result["duration_sec"] == pytest.approx(4, abs=0.1)


def test_episode_freezes_approved_source_and_reuses_request(client, series_id, mp4, monkeypatch):
    import json

    from calliope.config import settings
    from calliope.queue.manager import queue_manager

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    uploaded = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("example.mp4", mp4.read_bytes(), "video/mp4")},
    ).json()
    item = client.get(f"/api/studio/series/{series_id}/video-sources/{uploaded['id']}").json()
    payload = {
        "idea": "Une adaptation originale.",
        "request_id": "remake",
        "video_source_id": item["id"],
        "video_source_hash": item["hash"],
        "studio_director": True,
    }
    endpoint = f"/api/studio/series/{series_id}/episodes"
    assert client.post(endpoint, json=payload).status_code == 422
    shots = item["analysis"]["shots"]
    for shot in shots:
        shot.update(speech_reviewed=True, notes="Une rencontre dans la ville.")
    item = source.review_source(series_id, item["id"], item["hash"], shots)
    item = source.review_source(series_id, item["id"], item["hash"], approve=True)
    payload["video_source_hash"] = item["hash"]
    result = client.post(endpoint, json=payload)
    assert result.status_code == 200, result.text
    project = result.json()["project_id"]
    assert client.post(endpoint, json=payload).json()["project_id"] == project
    assert (
        client.post(
            endpoint, json=payload | {"video_source_id": None, "video_source_hash": None}
        ).status_code
        == 409
    )
    with service.database() as conn:
        frozen = json.loads(
            conn.execute(
                "SELECT context_json FROM studio_episodes WHERE project_id=?", (project,)
            ).fetchone()[0]
        )
    assert frozen["target_seconds"] == 4
    assert frozen["source_video"]["id"] == item["id"]
    assert frozen["image_mode"] == "manual_chatgpt"
    shots[0]["notes"] = "Nouvelle note."
    source.review_source(series_id, item["id"], item["hash"], shots)
    assert frozen["source_video"]["analysis"]["shots"][0]["notes"] == "Une rencontre dans la ville."


@pytest.mark.asyncio
async def test_planner_uses_source_adaptation_instead_of_free_writing(monkeypatch):
    from calliope.studio import series_planner

    async def adapt(context, idea, objective):
        return {"source_locked": True, "title": "Source", "beats": []}

    monkeypatch.setattr(source, "adapt_story", adapt)
    result, workshop = await series_planner._develop_creative_story(
        {"source_video": {"analysis": {}}}, "Une idée", ""
    )
    assert result["source_locked"]
    assert workshop["source"] == "video_adaptation"


def test_review_refuses_modified_timestamps(client, series_id, mp4):
    uploaded = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("example.mp4", mp4.read_bytes(), "video/mp4")},
    ).json()
    item = source.get_source(series_id, uploaded["id"])
    shots = item["analysis"]["shots"]
    shots[0]["end_sec"] = 999
    with pytest.raises(__import__("fastapi").HTTPException) as exc:
        source.review_source(series_id, item["id"], item["hash"], shots)
    assert exc.value.status_code == 422


def test_subsecond_source_is_blocked_before_adaptation(client, series_id, mp4, tmp_path):
    rapid = tmp_path / "rapid.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(mp4),
            "-vf",
            "setpts=PTS/3",
            "-c:v",
            "libx264",
            str(rapid),
        ],
        check=True,
        capture_output=True,
    )
    uploaded = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("rapid.mp4", rapid.read_bytes(), "video/mp4")},
    ).json()
    item = source.get_source(series_id, uploaded["id"])
    assert item["status"] == "ready"
    assert item["analysis"]["shots"][0]["end_sec"] < 1
    for shot in item["analysis"]["shots"]:
        shot["speech_reviewed"] = True
    item = source.review_source(series_id, item["id"], item["hash"], item["analysis"]["shots"])
    with pytest.raises(__import__("fastapi").HTTPException) as exc:
        source.review_source(series_id, item["id"], item["hash"], approve=True)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_adaptation_instructs_the_series_language(monkeypatch):
    async def llm(messages, **options):
        assert "English" in messages[0]["content"]
        return {
            "title": "Hello",
            "beats": [
                {
                    "source_index": 0,
                    "action": "Il salue.",
                    "dialogue_turns": [{"speaker": "momo", "text": "Hello."}],
                }
            ],
        }

    monkeypatch.setattr(source, "generate_structured", llm)
    result = await source.adapt_story(
        {
            "language": "en",
            "entities": [{"key": "momo", "kind": "character"}],
            "source_video": {
                "analysis": {
                    "duration_sec": 2,
                    "shots": [{"start_sec": 0, "end_sec": 2, "transcript": "Bonjour."}],
                }
            },
        },
        "",
        "",
    )
    assert result["beats"][0]["dialogue_turns"][0]["text"] == "Hello."


def test_episode_rejects_a_source_reapproved_in_another_tab(client, series_id, mp4, monkeypatch):
    from calliope.config import settings
    from calliope.queue.manager import queue_manager

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    uploaded = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("source.mp4", mp4.read_bytes(), "video/mp4")},
    ).json()
    item = source.get_source(series_id, uploaded["id"])
    for shot in item["analysis"]["shots"]:
        shot["speech_reviewed"] = True
    item = source.review_source(series_id, item["id"], item["hash"], item["analysis"]["shots"])
    item = source.review_source(series_id, item["id"], item["hash"], approve=True)
    old_hash = item["hash"]
    item["analysis"]["shots"][0]["notes"] = "Une modification dans un autre onglet."
    item = source.review_source(series_id, item["id"], item["hash"], item["analysis"]["shots"])
    source.review_source(series_id, item["id"], item["hash"], approve=True)
    result = client.post(
        f"/api/studio/series/{series_id}/episodes",
        json={
            "idea": "Adaptation",
            "request_id": "stale-source",
            "video_source_id": item["id"],
            "video_source_hash": old_hash,
        },
    )
    assert result.status_code == 409, result.text
    with service.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM studio_episodes").fetchone()[0] == 0


def test_full_source_pipeline_stops_before_gpu_and_rechecks_timeline(
    client, series_id, mp4, monkeypatch
):
    import io
    import json
    import zipfile

    from calliope.config import settings
    from calliope.queue.manager import queue_manager
    from calliope.studio import director, production, series_planner
    from tests.test_studio import png_bytes
    from tests.test_studio_director import contract
    from tests.test_studio_native_contract import native_plan

    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "studio_video_profile", "ltx")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:9001/v1")
    draft = native_plan()
    for entity in draft["entities"]:
        created = client.post(
            f"/api/studio/series/{series_id}/entities",
            json={
                "kind": entity["kind"],
                "name": entity["name"],
                "description": entity["description"],
            },
        )
        assert created.status_code == 200, created.text
    # The series API generates the canonical key from the name.
    client.patch(
        f"/api/studio/series/{series_id}", json={"directing_profile": {"cta_placement": "none"}}
    )
    uploaded = client.post(
        f"/api/studio/series/{series_id}/video-sources",
        files={"file": ("example.mp4", mp4.read_bytes(), "video/mp4")},
    ).json()
    item = source.get_source(series_id, uploaded["id"])
    lines = ["Salut.", "À demain."]
    for index, shot in enumerate(item["analysis"]["shots"]):
        shot.update(
            speech_reviewed=True,
            transcript=["Hello.", "See you."][index],
            notes="Le gardien salue dans le phare.",
        )
    item = source.review_source(series_id, item["id"], item["hash"], item["analysis"]["shots"])
    item = source.review_source(series_id, item["id"], item["hash"], approve=True)
    result = client.post(
        f"/api/studio/series/{series_id}/episodes",
        json={
            "idea": "Une adaptation originale.",
            "request_id": "full-source",
            "video_source_id": item["id"],
            "video_source_hash": item["hash"],
        },
    )
    assert result.status_code == 200, result.text
    project = result.json()["project_id"]
    draft["scenes"][0]["shots"] = draft["scenes"][0]["shots"][:2]
    draft["audio"] = {"enabled": False, "sfx": [], "holds": []}
    for index, shot in enumerate(draft["scenes"][0]["shots"]):
        shot.update(
            duration_sec=2,
            source_beat_index=index,
            continue_from_previous=False,
            dialogue=lines[index],
            dialogue_turns=[{"speaker": "keeper", "text": lines[index]}],
            action="Keeper waves.",
            reaction="",
            state_changes=[],
        )
        shot.pop("end_state", None)

    async def adapt(messages, **options):
        return {
            "title": "Le salut",
            "summary": "Le gardien salue.",
            "events": ["Le salut."],
            "ongoing_plot": "",
            "narrative_outline": draft["narrative_outline"],
            "beats": [
                {
                    "source_index": i,
                    "action": "Le gardien salue.",
                    "dialogue_turns": [{"speaker": "keeper", "text": line}],
                }
                for i, line in enumerate(lines)
            ],
        }

    async def realize(messages, **options):
        return copy.deepcopy(draft)

    async def contracts(messages, **options):
        request = json.loads(messages[-1]["content"])
        required = request["requirements"]
        value = contract()
        value.update(
            duration_sec=2,
            casting=required["casting"],
            location=required["location"],
            action=required["action"],
            initial_state={"keeper": "Standing left."} | required["prop_states"]["initial_state"],
            final_state={"keeper": "Standing left."} | required["prop_states"]["final_state"],
            chronology=[{"at_sec": 0, "action": a} for a in required["action_sequence"]],
            dialogue=[t | {"start_sec": 0.1, "end_sec": 1.8} for t in required["dialogue"]],
        )
        return {"shots": [value]}

    monkeypatch.setattr(source, "generate_structured", adapt)
    monkeypatch.setattr(series_planner, "generate_structured", realize)
    monkeypatch.setattr(director, "generate_structured", contracts)
    job = queue_manager.get_job(result.json()["job_id"])
    assert (
        asyncio.run(production.run_production(job, json.loads(job["payload_json"]), lambda: False))
        == []
    )
    state = director.state(project)
    assert len(state["shots"]) == 2
    assert not director.preflight(project)["ok"]
    state = director.approve(project, state["storyboard_hash"])
    archive = zipfile.ZipFile(io.BytesIO(director.package(project)))
    assert len(json.loads(archive.read("manifest.json"))["shots"]) == 2
    manifest = json.loads(archive.read("manifest.json"))
    assert len([n for n in archive.namelist() if "composition-source-" in n]) == 2
    assert all(any(r["kind"] == "composition" for r in s["references"]) for s in manifest["shots"])
    assert (
        "cadrage et les poses uniquement" in archive.read(f"prompts-episode-{project}.txt").decode()
    )
    assert all(s["image"] is None for s in state["shots"])
    state = director.import_images(
        project, state["storyboard_hash"], [(s["filename"], png_bytes()) for s in state["shots"]]
    )
    assert not director.preflight(project)["ok"]  # Images imported, never auto-approved.
    for shot in state["shots"]:
        director.review_image(project, shot["image"]["take_id"], state["storyboard_hash"], True)
    assert (
        client.get(f"/api/studio/projects/{project}/source-video").json()["source"]["id"]
        == item["id"]
    )
    with service.database() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM studio_takes WHERE kind='video'").fetchone()[0] == 0
        )
        # A later expert edit must not bypass the source rhythm at preflight/export.
        conn.execute("UPDATE clips SET duration_sec=8 WHERE id=?", (state["shots"][0]["id"],))
        conn.commit()
    check = director.preflight(project)
    assert not check["ok"] and any("durée" in e.lower() for e in check["errors"])
