import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from test_x_scheduler import api_server, client_for

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.scenario import Character, Project, Shot
from core.storage import StudioRepository
from qc.media import ffmpeg_exe, inspect_media


@pytest.fixture
def episode(tmp_path):
    data = tmp_path / "data"
    repo = StudioRepository(data / "v2.db")
    video = data / "episode.mp4"
    subprocess.run([
        ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi",
        "-i", "color=c=navy:s=160x288:r=24:d=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(video),
    ], check=True)
    project = Project(
        id="fixture-episode", title="Le pacte de Zeste",
        idea="Zeste refuse un pacte qui brille.",
        characters=[Character(id="zeste", name="Zeste", description="Un citron détective.")],
        shots=[Shot(
            id="shot-1", duration_s=6, action="Zeste repousse le contrat.",
            rendered_video_ref=str(video), human_validated=True,
            ltx_prompt="Vertical cinematic shot. A lemon detective pushes a glowing contract away.",
        )],
    )
    repo.save_project(project)
    repo.record_take(
        project_id=project.id, shot_id="shot-1", status="completed",
        video_path=str(video), actual_cost_eur=0.18,
        ltx_prompt=project.shots[0].ltx_prompt, metadata={"provider": "modal"},
    )
    repo.record_export(
        project_id=project.id, output_path=video, duration_s=6,
        width=160, height=288, qc={"ok": True},
    )
    return repo, data, project, video


def test_validated_episode_enqueues_four_posts_once(episode):
    from social.x_automation import enqueue_validated_episodes
    repo, data, project, _ = episode
    now = datetime.fromisoformat("2026-10-20T06:00:00+00:00")
    assert enqueue_validated_episodes(repo, data, now=now, jitter_minutes=10) == 4
    assert enqueue_validated_episodes(repo, data, now=now, jitter_minutes=10) == 0
    queue = json.loads((data / "x_queue.json").read_text(encoding="utf-8"))
    assert len(queue["posts"]) == 4
    assert len({p["id"] for p in queue["posts"]}) == 4
    assert [p["kind"] for p in queue["posts"]] == ["hook", "behind", "lesson", "funnel"]
    assert "0,18" in queue["posts"][1]["text"]
    assert all("1M" not in p["text"] and "$0.18" not in p["text"] for p in queue["posts"])
    assert all(p["episode_id"] == project.id for p in queue["posts"])
    assert [p["scheduled_at"] for p in queue["posts"]] == [
        "2026-10-20T09:10:00+02:00", "2026-10-20T14:10:00+02:00",
        "2026-10-20T18:10:00+02:00", "2026-10-21T09:10:00+02:00",
    ]


def test_fictional_episode_through_four_posts_teaser_and_one_opt_in_dm(episode, api_server, monkeypatch):
    from social.common import read_config
    from social.funnel import FunnelStore, process_comment
    from social.x_automation import enqueue_validated_episodes
    from social.x_scheduler import run_once
    import social.funnel as funnel
    repo, data, _, _ = episode
    base, requests, _ = api_server
    enqueue_validated_episodes(repo, data, now=datetime.fromisoformat("2026-10-20T06:00:00+00:00"), jitter_minutes=10)
    posts = json.loads((data / "x_queue.json").read_text(encoding="utf-8"))["posts"]
    assert len(posts) == 4
    monkeypatch.setattr(funnel, "readiness", lambda *a, **kw: {"ready": True, "missing": []})
    client = client_for(base)
    for post in posts:
        assert run_once(data, client, now=datetime.fromisoformat(post["scheduled_at"]))["posted"] == 1
    queue = json.loads((data / "x_queue.json").read_text(encoding="utf-8"))
    assert all(p["status"] == "posted" for p in queue["posts"])
    assert len(list((data / "social" / "teasers").glob("*.mp4"))) == 1
    root = queue["posts"][-1]["remote_id"]
    comment = {"id": "123", "author_id": "222", "text": "STUDIO",
               "referenced_tweets": [{"type": "replied_to", "id": root}]}
    store = FunnelStore(data / "v2.db")
    assert process_comment(comment, {root}, client, store, read_config()) == "sent"
    assert process_comment(comment, {root}, client, store, read_config()) == "duplicate"
    assert len([r for r in requests if r["path"] == "/2/tweets"]) == 4
    assert len([r for r in requests if r["path"].endswith("/messages")]) == 1


def test_unapproved_or_failed_export_never_enters_queue(episode):
    from social.x_automation import enqueue_validated_episodes
    repo, data, project, video = episode
    project.shots[0].human_validated = False
    repo.save_project(project)
    assert enqueue_validated_episodes(repo, data) == 0
    project.shots[0].human_validated = True
    repo.save_project(project)
    repo.record_export(
        project_id=project.id, output_path=video, duration_s=6,
        width=160, height=288, qc={"ok": False},
    )
    assert enqueue_validated_episodes(repo, data) == 0


def test_changed_approved_shot_requires_a_new_export(episode):
    from social.x_automation import enqueue_validated_episodes
    repo, data, project, video = episode
    project.shots[0].rendered_video_ref = str(video.with_name("new-take.mp4"))
    repo.save_project(project)
    assert enqueue_validated_episodes(repo, data) == 0


def test_unknown_cost_is_never_reported_as_zero(episode):
    from social.x_automation import enqueue_validated_episodes
    repo, data, _, _ = episode
    with repo._connect() as conn:
        conn.execute("UPDATE takes SET actual_cost_eur = NULL")
    enqueue_validated_episodes(repo, data)
    texts = "\n".join(p["text"] for p in json.loads((data / "x_queue.json").read_text(encoding="utf-8"))["posts"])
    assert "non mesuré" in texts
    assert "0,00" not in texts


def test_imported_take_does_not_claim_a_free_gpu_render(episode):
    from social.x_automation import enqueue_validated_episodes
    repo, data, _, _ = episode
    with repo._connect() as conn:
        conn.execute("UPDATE takes SET status = 'imported', actual_cost_eur = 0")
    enqueue_validated_episodes(repo, data)
    text = json.loads((data / "x_queue.json").read_text(encoding="utf-8"))["posts"][1]["text"]
    assert "non mesuré" in text


def test_schedule_handles_paris_dst_without_utc_drift():
    from social.common import allocate_slots
    slots = allocate_slots([], 4, datetime.fromisoformat("2026-10-24T17:00:00+02:00"), 10)
    assert slots == [
        "2026-10-24T18:10:00+02:00", "2026-10-25T09:10:00+01:00",
        "2026-10-25T14:10:00+01:00", "2026-10-25T18:10:00+01:00",
    ]


def test_posts_fit_standard_x_even_when_source_prompt_is_long(episode):
    from social.common import weighted_length
    from social.x_automation import enqueue_validated_episodes
    repo, data, project, _ = episode
    project.shots[0].ltx_prompt = " ".join(["A cinematic lemon detective in a kitchen."] * 20)
    repo.save_project(project)
    repo.record_export(
        project_id=project.id, output_path=Path(project.shots[0].rendered_video_ref),
        duration_s=6, width=160, height=288, qc={"ok": True},
    )
    enqueue_validated_episodes(repo, data)
    posts = json.loads((data / "x_queue.json").read_text(encoding="utf-8"))["posts"]
    assert all(weighted_length(p["text"]) <= 280 for p in posts)
    assert posts[0]["prompt_full"] == project.shots[0].ltx_prompt
    assert weighted_length("https://example.com/a-very-long-path") == 23


def test_real_teaser_is_at_most_five_seconds(episode):
    from social.x_automation import build_teaser
    _, data, _, video = episode
    teaser = build_teaser(video, data / "social" / "teaser.mp4")
    media = inspect_media(teaser)
    assert 4.8 <= media["duration_s"] <= 5.1
    assert media["video_codec"] == "h264"


def test_queue_file_is_valid_after_repeated_locked_updates(tmp_path):
    from social.common import locked_queue
    with locked_queue(tmp_path) as queue:
        queue["posts"].append({"id": "one"})
    with locked_queue(tmp_path) as queue:
        assert queue["posts"] == [{"id": "one"}]
        queue["posts"].append({"id": "two"})
    assert json.loads((tmp_path / "x_queue.json").read_text())["posts"] == [
        {"id": "one"}, {"id": "two"},
    ]
