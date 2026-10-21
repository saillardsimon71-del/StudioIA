"""Tests unitaires de l'intégration image MetaAI, sans serveur ni compte Meta réels."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from api.app import create_app  # noqa: E402
from core.config import V2Settings  # noqa: E402


def _png() -> bytes:
    image = Image.new("RGB", (17, 29), (30, 140, 220))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _Response:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _MetaAIService:
    def __init__(self, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(payload={"success": True, "image_urls": ["https://cdn.example/image.png"]})

    def get(self, url):
        assert url == "https://cdn.example/image.png"
        return _Response(content=_png())


def test_generate_metaai_image_downloads_and_registers_asset(tmp_path, monkeypatch):
    monkeypatch.setattr("api.app.httpx.Client", _MetaAIService)
    settings = V2Settings(data_dir=tmp_path / "data", metaai_api_url="http://metaai.test:8000")
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={
            "prompt": "A cinematic lemon detective",
            "kind": "character",
            "name": "Inspecteur Zeste",
        })
        assert response.status_code == 200, response.text
        asset = response.json()
        assert asset["kind"] == "character"
        assert asset["name"] == "Inspecteur Zeste"
        assert (asset["width"], asset["height"]) == (17, 29)
        saved = Path(asset["file_path"])
        assert saved.is_file() and saved.suffix == ".png"
        with Image.open(saved) as image:
            assert image.size == (17, 29)


def test_metaai_api_key_is_sent_only_as_bearer_header(tmp_path, monkeypatch):
    observed = {}

    class CapturingService(_MetaAIService):
        def post(self, url, **kwargs):
            observed.update(kwargs)
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", CapturingService)
    settings = V2Settings(data_dir=tmp_path / "data", metaai_api_key="server-secret")
    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "a red fox"})
        assert response.status_code == 200, response.text
        assert observed["headers"]["Authorization"] == "Bearer server-secret"
        public_settings = client.get("/api/settings").json()
        assert public_settings["metaai_api_key_configured"] is True
        assert "metaai_api_key" not in public_settings
        assert "server-secret" not in str(public_settings)


def test_metaai_generate_rejects_invalid_kind_without_calling_service(tmp_path):
    settings = V2Settings(data_dir=tmp_path / "data")
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "anything", "kind": "video"})
    assert response.status_code == 400
