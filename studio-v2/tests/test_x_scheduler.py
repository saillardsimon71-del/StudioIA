import json
import hashlib
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.scenario import Project, Shot
from core.storage import StudioRepository


def validated_qc(project, ok=True):
    return {"ok": ok, "project_sha256": hashlib.sha256(project.model_dump_json().encode()).hexdigest()}


@pytest.fixture
def api_server():
    requests = []
    state = {"post_status": 201, "username": "AvenirIA0"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/2/users/me"):
                self.reply(200, {"data": {"id": "111", "username": state["username"]}})
            elif self.path.startswith("/2/media/upload?"):
                self.reply(200, {"data": {"id": "700", "processing_info": {"state": "succeeded"}}})
            elif self.path.startswith("/2/dm_events") and state.get("dm_pages"):
                index = 1 if "pagination_token=older" in self.path else 0
                state.setdefault("dm_gets", []).append(self.path)
                self.reply(200, state["dm_pages"][index])
            else:
                self.reply(200, {"data": [], "meta": {"result_count": 0}})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            content_type = self.headers.get("Content-Type", "")
            body = json.loads(raw) if "application/json" in content_type else raw
            requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
            if self.path == "/2/tweets":
                self.reply(state["post_status"], {"data": {"id": "900", "text": "Publié"}, "title": "test"})
            elif self.path.endswith("/messages"):
                self.reply(201, {"data": {"dm_event_id": "901", "dm_conversation_id": "111-222"}})
            elif self.path.endswith("/initialize"):
                self.reply(200, {"data": {"id": "700", "expires_after_secs": 86400}})
            elif self.path.endswith("/finalize"):
                self.reply(200, {"data": {"id": "700"}})
            elif self.path.endswith("/append") and state.get("empty_append"):
                self.send_response(204)
                self.end_headers()
            else:
                self.reply(200, {})

        def reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests, state
    server.shutdown()
    server.server_close()
    thread.join()


def make_queue(tmp_path):
    from social.x_automation import enqueue_validated_episodes
    repo = StudioRepository(tmp_path / "v2.db")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fixture-only-hook")
    project = Project(
        id="fixture", title="Fixture",
        idea="Un personnage refuse un pacte.",
        shots=[Shot(id="one", duration_s=5, action="Refuse.",
                    human_validated=True, rendered_video_ref=str(video), ltx_prompt="One shot.")],
    )
    repo.save_project(project)
    repo.record_export(
        project_id=project.id, output_path=video, duration_s=5,
        width=160, height=288, qc=validated_qc(project),
    )
    enqueue_validated_episodes(
        repo, tmp_path, now=datetime.fromisoformat("2026-10-20T06:00:00+00:00"), jitter_minutes=10,
    )
    return repo, project


def client_for(base):
    from social.x_client import XClient
    return XClient("fixture-key", "fixture-secret", "fixture-token", "fixture-token-secret", base_url=base)


def test_api_payload_uses_user_oauth_and_ai_disclosure(api_server):
    base, requests, _ = api_server
    client = client_for(base)
    result = client.create_post("Un build réel.", made_with_ai=True, media_id="700")
    assert result == "900"
    assert requests[0]["path"] == "/2/tweets"
    assert requests[0]["body"] == {
        "text": "Un build réel.", "made_with_ai": True, "media": {"media_ids": ["700"]},
    }
    assert requests[0]["auth"].startswith("OAuth ")


def test_same_due_post_is_never_published_twice(tmp_path, api_server):
    from social.x_scheduler import run_once
    base, requests, _ = api_server
    make_queue(tmp_path)
    now = datetime.fromisoformat("2026-10-20T09:10:00+02:00")
    assert run_once(tmp_path, client_for(base), now=now)["posted"] == 1
    assert run_once(tmp_path, client_for(base), now=now)["posted"] == 0
    assert len([r for r in requests if r["path"] == "/2/tweets"]) == 1
    posts = json.loads((tmp_path / "x_queue.json").read_text(encoding="utf-8"))["posts"]
    assert posts[0]["status"] == "posted"
    assert posts[0]["remote_id"] == "900"


def test_server_error_becomes_uncertain_without_blind_retry(tmp_path, api_server):
    from social.x_scheduler import run_once
    base, requests, state = api_server
    make_queue(tmp_path)
    state["post_status"] = 503
    now = datetime.fromisoformat("2026-10-20T09:10:00+02:00")
    assert run_once(tmp_path, client_for(base), now=now)["posted"] == 0
    state["post_status"] = 201
    run_once(tmp_path, client_for(base), now=now)
    posts = json.loads((tmp_path / "x_queue.json").read_text(encoding="utf-8"))["posts"]
    assert posts[0]["status"] == "uncertain"
    assert len([r for r in requests if r["path"] == "/2/tweets"]) == 1
    assert "Fixture" in (tmp_path / "today_posts.txt").read_text(encoding="utf-8")


def test_four_published_posts_block_fifth_on_same_paris_date(tmp_path, api_server):
    from social.common import locked_queue
    from social.x_scheduler import run_once
    base, requests, _ = api_server
    make_queue(tmp_path)
    with locked_queue(tmp_path) as queue:
        queue["posts"].extend([
            {"id": f"old-{i}", "episode_id": "old", "status": "posted",
             "posted_at": "2026-10-20T07:00:00+02:00", "text": "Historique."}
            for i in range(4)
        ])
    result = run_once(tmp_path, client_for(base), now=datetime.fromisoformat("2026-10-20T09:10:00+02:00"))
    assert result["posted"] == 0
    assert not requests


def test_missing_credentials_exports_text_without_claiming_success(tmp_path):
    from social.x_scheduler import run_once
    make_queue(tmp_path)
    result = run_once(tmp_path, None, now=datetime.fromisoformat("2026-10-20T09:10:00+02:00"))
    assert result["posted"] == 0
    assert result["blocked"]
    assert (tmp_path / "today_posts.txt").is_file()
    posts = json.loads((tmp_path / "x_queue.json").read_text(encoding="utf-8"))["posts"]
    assert all(p["status"] == "pending" for p in posts)


def test_revoked_episode_approval_prevents_transmission(tmp_path, api_server):
    from social.x_scheduler import run_once
    base, requests, _ = api_server
    repo, project = make_queue(tmp_path)
    project.shots[0].human_validated = False
    repo.save_project(project)
    run_once(tmp_path, client_for(base), now=datetime.fromisoformat("2026-10-20T09:10:00+02:00"))
    assert not requests
    posts = json.loads((tmp_path / "x_queue.json").read_text(encoding="utf-8"))["posts"]
    assert all(p["status"] == "cancelled" for p in posts)


def test_wrong_x_account_is_blocked_before_post(tmp_path, api_server):
    from social.x_scheduler import run_once
    base, requests, state = api_server
    state["username"] = "WrongAccount"
    make_queue(tmp_path)
    result = run_once(tmp_path, client_for(base), now=datetime.fromisoformat("2026-10-20T09:10:00+02:00"))
    assert result["blocked"]
    assert not requests


def test_media_upload_finishes_before_post(api_server, tmp_path):
    base, requests, _ = api_server
    video = tmp_path / "teaser.mp4"
    video.write_bytes(b"fixture-upload")
    client = client_for(base)
    assert client.upload_video(video) == "700"
    client.create_post("Teaser.", media_id="700")
    assert [r["path"] for r in requests] == [
        "/2/media/upload/initialize", "/2/media/upload/700/append",
        "/2/media/upload/700/finalize", "/2/tweets",
    ]


def test_media_append_accepts_success_without_json_body(api_server, tmp_path):
    base, _, state = api_server
    state["empty_append"] = True
    video = tmp_path / "teaser.mp4"
    video.write_bytes(b"fixture-upload")
    assert client_for(base).upload_video(video) == "700"


def test_funnel_post_waits_for_operational_landing(tmp_path, api_server, monkeypatch):
    from social.common import locked_queue
    from social.x_scheduler import run_once
    import social.funnel as funnel
    base, requests, _ = api_server
    make_queue(tmp_path)
    with locked_queue(tmp_path) as queue:
        for post in queue["posts"][:3]:
            post["status"] = "posted"
            post["posted_at"] = "2026-10-20T18:10:00+02:00"
    monkeypatch.setattr(funnel, "readiness", lambda *a, **kw: {"ready": False, "missing": ["PUBLIC_FUNNEL"]})
    result = run_once(tmp_path, client_for(base), now=datetime.fromisoformat("2026-10-21T09:10:00+02:00"))
    assert result["posted"] == 0
    assert result["blocked"]
    assert not requests


def test_browser_requires_explicit_opt_in_before_starting(tmp_path, monkeypatch):
    from social.x_browser import BrowserXClient
    from social.x_client import XError
    monkeypatch.delenv("X_ALLOW_UNSUPPORTED_BROWSER", raising=False)
    with pytest.raises(XError, match="désactivé"):
        BrowserXClient(tmp_path)


def test_dm_event_lookup_traverses_all_pages(api_server):
    base, _, state = api_server
    state["dm_pages"] = [
        {"data": [{"id": "101", "sender_id": "333", "text": "Salut"}], "meta": {"next_token": "older"}},
        {"data": [{"id": "100", "sender_id": "222", "text": "STOP"}], "meta": {}},
    ]
    events = list(client_for(base).dm_events())
    assert [e["id"] for e in events] == ["101", "100"]
    assert len(state["dm_gets"]) == 2
