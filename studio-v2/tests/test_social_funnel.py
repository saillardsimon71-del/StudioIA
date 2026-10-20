import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_x_scheduler import api_server, client_for


def comment(text="STUDIO", author="222", root="900", event="123"):
    return {
        "id": event, "author_id": author, "text": text,
        "referenced_tweets": [{"type": "replied_to", "id": root}],
    }


def test_exact_opt_in_sends_one_dm_and_persists_delivery(tmp_path, api_server):
    from social.funnel import FunnelStore, process_comment
    from social.common import read_config
    base, requests, _ = api_server
    store = FunnelStore(tmp_path / "v2.db")
    config = read_config()
    result = process_comment(comment(), {"900"}, client_for(base), store, config)
    assert result == "sent"
    assert process_comment(comment(event="124"), {"900"}, client_for(base), store, config) == "duplicate"
    assert len(requests) == 1
    assert requests[0]["path"] == "/2/dm_conversations/with/222/messages"
    assert config["landing_url"] in requests[0]["body"]["text"]
    assert "STOP" in requests[0]["body"]["text"]
    assert store.delivery("222", config["campaign_id"])["remote_id"] == "901"


@pytest.mark.parametrize("item", [
    comment(text="Je déteste STUDIO"),
    comment(root="not-an-opt-in-post"),
    comment(author="111"),
    {"id": "200", "author_id": "222", "text": "STUDIO"},
])
def test_keyword_without_explicit_consent_never_sends(item, tmp_path, api_server):
    from social.funnel import FunnelStore, process_comment
    from social.common import read_config
    base, requests, _ = api_server
    client = client_for(base)
    client.user_id = "111"
    assert process_comment(item, {"900"}, client, FunnelStore(tmp_path / "v2.db"), read_config()) == "ignored"
    assert not requests


def test_stop_is_durable_and_blocks_later_requests(tmp_path, api_server):
    from social.funnel import FunnelStore, process_comment
    from social.common import read_config
    base, requests, _ = api_server
    store = FunnelStore(tmp_path / "v2.db")
    store.opt_out("222")
    assert process_comment(comment(), {"900"}, client_for(base), store, read_config()) == "opted_out"
    assert not requests


def test_dm_timeout_is_not_retried_automatically(tmp_path):
    from social.funnel import FunnelStore, process_comment
    from social.common import read_config
    from social.x_client import XError

    class InterruptedX:
        user_id = "111"

        def send_dm(self, user_id, text):
            raise XError("Timeout après transmission.", uncertain=True)

    store = FunnelStore(tmp_path / "v2.db")
    config = read_config()
    assert process_comment(comment(), {"900"}, InterruptedX(), store, config) == "uncertain"
    assert process_comment(comment(event="999"), {"900"}, InterruptedX(), store, config) == "duplicate"
    assert store.delivery("222", config["campaign_id"])["status"] == "uncertain"


def test_kit_subscribe_requires_consent_and_waits_for_confirmation(tmp_path):
    from social.funnel import FunnelStore, KitClient, subscribe
    from social.common import read_config
    requests = []

    def provider(request):
        body = json.loads(request.content or b"{}")
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/v4/subscribers":
            return httpx.Response(201, json={"subscriber": {
                "id": 10, "first_name": None, "email_address": "builder@example.com",
                "state": "inactive", "created_at": "2026-10-20T00:00:00Z", "fields": {},
            }})
        if request.url.path == "/v4/forms/20/subscribers/10":
            return httpx.Response(201, json={"subscriber": {"id": 10, "state": "inactive"}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    kit = KitClient("fixture-key", form_id="20", sequence_id="30",
                    transport=httpx.MockTransport(provider))
    store = FunnelStore(tmp_path / "v2.db")
    with pytest.raises(ValueError, match="consentement"):
        subscribe("builder@example.com", False, kit, store, read_config())
    assert not requests
    result = subscribe("builder@example.com", True, kit, store, read_config())
    assert result["status"] == "pending_confirmation"
    assert requests == [
        ("POST", "/v4/subscribers", {"email_address": "builder@example.com", "state": "inactive"}),
        ("POST", "/v4/forms/20/subscribers/10", {"referrer": read_config()["landing_url"]}),
    ]
    assert store.lead("builder@example.com")["status"] == "pending_confirmation"


def test_confirmed_kit_subscriber_enters_sequence_once(tmp_path):
    from social.funnel import FunnelStore, KitClient, sync_confirmed_leads
    requests = []

    def provider(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"subscriber": {"id": 10, "state": "active"}})
        return httpx.Response(201, json={"subscriber": {"id": 10, "state": "active"}})

    store = FunnelStore(tmp_path / "v2.db")
    store.save_lead("builder@example.com", "10", "pending_confirmation")
    kit = KitClient("fixture-key", form_id="20", sequence_id="30",
                    transport=httpx.MockTransport(provider))
    assert sync_confirmed_leads(kit, store) == 1
    assert sync_confirmed_leads(kit, store) == 0
    assert requests == [
        ("GET", "/v4/subscribers/10"),
        ("POST", "/v4/sequences/30/subscribers/10"),
    ]
    assert store.lead("builder@example.com")["status"] == "enrolled"


def test_public_app_does_not_expose_studio_admin_or_fake_signup(tmp_path, monkeypatch):
    from social.funnel_server import create_funnel_app
    import social.funnel_server as server
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>Bible</h1>", encoding="utf-8")
    monkeypatch.setattr(server, "WEB", web)
    monkeypatch.delenv("KIT_API_KEY", raising=False)
    app = create_funnel_app(data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/funnel").status_code == 200
        assert client.get("/api/settings").status_code == 404
        assert client.get("/api/projects").status_code == 404
        assert client.post("/api/funnel/subscribe", json={
            "email": "builder@example.com", "consent": True,
        }).status_code == 503
        assert client.post("/api/funnel/subscribe", json={
            "email": "builder@example.com", "consent": False,
        }).status_code == 422
        assert client.get("/funnel/bible.pdf").status_code == 503


def test_public_app_rejects_bad_email_and_honeypot(tmp_path):
    from social.funnel_server import create_funnel_app
    with TestClient(create_funnel_app(data_dir=tmp_path)) as client:
        assert client.post("/api/funnel/subscribe", json={
            "email": "invalid-address", "consent": True,
        }).status_code == 422
        assert client.post("/api/funnel/subscribe", json={
            "email": "builder@example.com", "consent": True, "website": "spam",
        }).status_code == 202


def test_kit_setup_creates_five_emails_once_with_real_relative_delays(tmp_path, monkeypatch):
    import social.funnel as funnel
    from social.common import read_config
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v4/sequences":
            return httpx.Response(201, json={"sequence": {"id": 30}})
        return httpx.Response(201, json={"email": {"id": len(requests)}})

    original = funnel.KitClient
    monkeypatch.setattr(funnel, "KitClient", lambda key, **kw: original(
        key, **kw, transport=httpx.MockTransport(provider)))
    for key, value in {"KIT_API_KEY": "fixture", "KIT_FORM_ID": "20", "KIT_SENDER_EMAIL": "owner@example.com"}.items():
        monkeypatch.setenv(key, value)
    config = read_config()
    config["checkout_url"] = "https://example.com/formation-197"
    store = funnel.FunnelStore(tmp_path / "v2.db")
    assert funnel.configure_kit(store, config) == {"sequence_id": "30", "emails": 5}
    assert funnel.configure_kit(store, config)["emails"] == 5
    assert len(requests) == 6
    assert [r[1]["delay_value"] for r in requests[1:]] == [0, 1, 1, 1, 1]
    assert all(r[1]["published"] for r in requests[1:])
    assert config["checkout_url"] in requests[-1][1]["content"]
    assert '<a href="' in requests[1][1]["content"]


def test_partial_sequence_setup_cannot_accept_subscribers(tmp_path, monkeypatch):
    from social.funnel import FunnelStore, KitClient
    monkeypatch.setenv("KIT_API_KEY", "fixture")
    monkeypatch.setenv("KIT_FORM_ID", "20")
    monkeypatch.delenv("KIT_SEQUENCE_ID", raising=False)
    store = FunnelStore(tmp_path / "v2.db")
    store.set_state("kit_sequence_id", "30")
    assert KitClient.from_env(store) is None
    store.set_state("kit_setup_complete", True)
    assert KitClient.from_env(store).sequence_id == "30"
