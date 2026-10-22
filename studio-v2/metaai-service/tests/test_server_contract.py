"""Contrat HTTP du service Meta AI : statuts explicites, aucune fuite de secret."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from studioia_metaai.dom import ProvenanceError, RefusalError  # noqa: E402
from studioia_metaai.server import create_app  # noqa: E402
from studioia_metaai.service import (  # noqa: E402
    DuplicateRequestError,
    ServiceBusyError,
    ServiceConfigurationError,
)

PAYLOAD = {
    "success": True,
    "image_urls": ["https://scontent.xx.fbcdn.net/generated.png"],
    "request_id": "req-1",
    "prompt": "A red cube",
    "conversation_id": "conv-1",
    "attribution": "fresh_assistant_response",
    "prompt_echo_verified": True,
}


class FakeService:
    def __init__(self, result=None, error=None):
        self.result = result or dict(PAYLOAD)
        self.error = error
        self.calls = []

    def health(self):
        return {"status": "ok", "version": "test", "browser_ready": False,
                "cookies_configured": True, "generated_this_session": len(self.calls)}

    def generate(self, prompt, request_id=None):
        self.calls.append((prompt, request_id))
        if self.error:
            raise self.error
        return {**self.result, "prompt": prompt, "request_id": request_id or self.result["request_id"]}

    def reset(self):
        self.calls.append(("reset", None))

    def close(self):
        pass


def _client(service, monkeypatch, api_key: str = "") -> TestClient:
    monkeypatch.delenv("META_AI_API_KEY", raising=False)
    if api_key:
        monkeypatch.setenv("META_AI_API_KEY", api_key)
    return TestClient(create_app(service=service))


def test_health_and_image_contract(monkeypatch):
    service = FakeService()
    with _client(service, monkeypatch) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        response = client.post("/image", json={"prompt": "A red cube", "request_id": "req-1"})
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == "req-1" and body["attribution"] == "fresh_assistant_response"
        assert service.calls == [("A red cube", "req-1")]
        assert client.post("/reset").status_code == 200


@pytest.mark.parametrize(
    "error, status",
    [
        (DuplicateRequestError("déjà exécuté"), 409),
        (ServiceBusyError("occupé"), 429),
        (ServiceConfigurationError("cookies absents"), 503),
        (RefusalError("refus"), 422),
        (ProvenanceError("provenance invalide"), 502),
        (ProvenanceError("délai imparti dépassé"), 504),
        (RuntimeError("boom"), 500),
    ],
)
def test_errors_map_to_explicit_statuses_without_traceback(monkeypatch, error, status):
    with _client(FakeService(error=error), monkeypatch) as client:
        response = client.post("/image", json={"prompt": "A red cube"})
        assert response.status_code == status
        detail = response.json()["detail"]
        assert "Traceback" not in detail and "/home/" not in detail


def test_api_key_is_enforced_when_configured(monkeypatch):
    with _client(FakeService(), monkeypatch, api_key="server-secret") as client:
        assert client.post("/image", json={"prompt": "A cube"}).status_code == 401
        ok = client.post("/image", json={"prompt": "A cube"}, headers={"Authorization": "Bearer server-secret"})
        assert ok.status_code == 200


def test_prompt_is_required_and_bounded(monkeypatch):
    with _client(FakeService(), monkeypatch) as client:
        assert client.post("/image", json={"prompt": ""}).status_code == 422
        assert client.post("/image", json={"prompt": "x" * 4001}).status_code == 422
