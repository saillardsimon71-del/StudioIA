"""Provenance fail-closed checks and explicit single-reference retirement."""
from __future__ import annotations

import io
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from api.app import create_app  # noqa: E402
from core.config import V2Settings  # noqa: E402
from core.scenario import Character, Decor, Project, Shot  # noqa: E402


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (18, 18), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


class _Response:
    def __init__(self, payload=None, content=b"", url="https://scontent.xx.fbcdn.net/image.png"):
        self.payload = payload or {}
        self.content = content
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Service:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        body = kwargs["json"]
        return _Response({
            "success": True,
            "prompt": body["prompt"],
            "request_id": body["request_id"],
            "conversation_id": "fresh-conversation",
            "image_urls": ["https://scontent.xx.fbcdn.net/image.png"],
        })

    def get(self, url):
        return _Response(content=_image_bytes(), url=url)


def test_unmatched_request_is_rejected_before_asset_insertion(tmp_path, monkeypatch):
    class WrongPromptService(_Service):
        def post(self, url, **kwargs):
            payload = super().post(url, **kwargs).json()
            payload["prompt"] = "a previous prompt"
            return _Response(payload)

    monkeypatch.setattr("api.app.httpx.Client", WrongPromptService)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "a blue pyramid"})
        assert response.status_code == 502
        assert client.get("/api/assets").json() == []


def test_untrusted_media_origin_is_rejected_without_asset_insertion(tmp_path, monkeypatch):
    class UntrustedService(_Service):
        def post(self, url, **kwargs):
            payload = super().post(url, **kwargs).json()
            payload["image_urls"] = ["https://images.example.invalid/avatar.png"]
            return _Response(payload)

    monkeypatch.setattr("api.app.httpx.Client", UntrustedService)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "a green sphere"})
        assert response.status_code == 502
        assert client.get("/api/assets").json() == []


def test_reused_conversation_is_rejected_for_later_request(tmp_path, monkeypatch):
    monkeypatch.setattr("api.app.httpx.Client", _Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        first = client.post("/api/assets/generate", json={"prompt": "a red cube"})
        second = client.post("/api/assets/generate", json={"prompt": "a green sphere"})
        assert first.status_code == 200
        assert first.json()["metaai_generation"]["conversation_id"] == "fresh-conversation"
        assert second.status_code == 502
        assert len(client.get("/api/assets").json()) == 1


def test_read_only_audit_and_single_asset_retirement_preserve_other_assets_and_files(tmp_path):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    cfg = app.state.settings
    cfg.media_dir.mkdir(parents=True, exist_ok=True)
    suspect_file = cfg.media_dir / "suspect.png"
    other_file = cfg.media_dir / "other.png"
    suspect_file.write_bytes(_image_bytes())
    other_file.write_bytes(_image_bytes())
    suspect = app.state.repo.add_asset(
        kind="character", name="À vérifier", file_path=suspect_file,
        width=18, height=18, asset_id="asset-review-only",
    )
    other = app.state.repo.add_asset(
        kind="decor", name="À conserver", file_path=other_file,
        width=18, height=18, asset_id="asset-keep",
    )
    project = Project(
        id="project-review", title="Audit", idea="Inspection visuelle",
        characters=[Character(id="c1", name="Personnage", description="Description", image_ref=str(suspect_file))],
        decors=[Decor(id="d1", name="Décor", description="Description", asset_id=suspect["id"])],
        shots=[Shot(id="s1", duration_s=2, action="A shot", image_ref=str(suspect_file),
                    decor_asset_id=suspect["id"], image_validated=True)],
    )
    app.state.repo.save_project(project)

    with TestClient(app) as client:
        audit = client.get("/api/assets/audit").json()
        reviewed = next(asset for asset in audit["assets"] if asset["id"] == suspect["id"])
        assert reviewed["file_exists"] is True
        assert {ref["entity_type"] for ref in reviewed["references"]} == {
            "character", "decor", "shot", "shot_decor",
        }
        assert suspect_file.is_file() and other_file.is_file()

        removed = client.delete(f"/api/assets/{suspect['id']}")
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"deleted": True, "detached_references": 4, "file_preserved": True}
        remaining = client.get("/api/assets").json()
        assert [item["id"] for item in remaining] == [other["id"]]
        assert suspect_file.is_file() and other_file.is_file()
        saved = app.state.repo.get_project(project.id)
        assert saved.characters[0].image_ref is None
        assert saved.decors[0].asset_id is None
        assert saved.shots[0].image_ref is None and saved.shots[0].image_validated is False
        assert saved.shots[0].decor_asset_id is None
