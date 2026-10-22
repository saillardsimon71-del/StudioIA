"""Provenance Meta AI côté Studio IA et protection de la bibliothèque persistante."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from api.app import create_app  # noqa: E402
from core.config import V2Settings  # noqa: E402
from core.metaai import MetaAIImageClient, MetaAIError  # noqa: E402
from core.scenario import Character, Decor, DialogueLine, Project, Shot  # noqa: E402

IMAGE_URL = "https://scontent.xx.fbcdn.net/v/t39/generated.png"


def _png(size: tuple[int, int] = (512, 768), color=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, *, status: int = 200, payload=None, content: bytes = b"", url: str = IMAGE_URL):
        self.status_code = status
        self._payload = payload or {}
        self.content = content
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://metaai.test/image")
            raise httpx.HTTPStatusError("error", request=request, response=httpx.Response(self.status_code, request=request))
        return None

    def json(self):
        return self._payload


class FakeService:
    """Service local scripté : les preuves sont produites par l'appel, comme en réel."""

    def __init__(self, *, mutate=None, image: bytes | None = None, post_status: int = 200, conversations=None):
        self.mutate = mutate
        self.image = image if image is not None else _png()
        self.post_status = post_status
        self.conversations = conversations
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.calls += 1
        body = kwargs.get("json") or {}
        request_id = body.get("request_id")
        conversation = f"conversation-{request_id}"
        payload = {
            "success": True,
            "image_urls": [IMAGE_URL],
            "prompt": body.get("prompt", ""),
            "request_id": request_id,
            "conversation_id": conversation,
            "attribution": "fresh_assistant_response",
            "prompt_echo_verified": True,
            "service_version": "test",
        }
        if self.conversations is not None:
            payload["conversation_id"] = self.conversations.pop(0) if self.conversations else payload["conversation_id"]
        if self.mutate is not None:
            self.mutate(payload)
        return FakeResponse(status=self.post_status, payload=payload)

    def get(self, url):
        return FakeResponse(content=self.image)


def _app(tmp_path, service: FakeService, **settings_kwargs):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data", **settings_kwargs))
    app.state.repo  # base initialisée
    return app, service


def _install(monkeypatch, service):
    monkeypatch.setattr("api.app.httpx.Client", lambda **kwargs: service)


def _shot_project() -> Project:
    return Project(
        id="prov-project", idea="Un citron inspecte un coffre.", title="Preuve",
        characters=[Character(id="zeste", name="Zeste", description="Yellow lemon detective")],
        decors=[Decor(id="cuisine", name="Cuisine", description="Dim kitchen with steel counters")],
        shots=[
            Shot(id="s1", duration_s=4, action="Zeste opens the safe.", initial_state="Zeste faces a closed safe.",
                 final_state="The safe is open.", decor_id="cuisine"),
            Shot(id="s2", duration_s=4, action="Zeste closes the safe.", initial_state="Zeste stands by the open safe."),
        ],
    )


def test_valid_generation_records_verified_provenance(tmp_path, monkeypatch):
    app, service = _app(tmp_path, FakeService())
    _install(monkeypatch, service)
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "A red cube", "kind": "frame", "name": "Cube"})
        assert response.status_code == 200, response.text
        asset = response.json()
        assert asset["source"] == "metaai" and asset["verified"] is True
        assert asset["provenance"]["attribution"] == "fresh_assistant_response"
        assert asset["provenance"]["request_id"] and asset["provenance"]["conversation_id"]
        assert asset["provenance"]["prompt"] == "A red cube"
        assert Path(asset["file_path"]).is_file()
        generations = app.state.repo.list_metaai_generations()
        assert len(generations) == 1 and generations[0]["asset_id"] == asset["id"]


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda payload: payload.update(request_id="another-request"), "ne correspond pas à la requête"),
        (lambda payload: payload.update(prompt="A previous prompt"), "ne correspond pas au prompt"),
        (lambda payload: payload.update(attribution="sidebar_thumbnail"), "conversation neuve"),
        (lambda payload: payload.pop("prompt_echo_verified"), "message réellement envoyé"),
        (lambda payload: payload.update(conversation_id=""), "conversation Meta AI absente"),
        (lambda payload: payload.update(image_urls=["https://images.example.invalid/avatar.png"]), "CDN Meta"),
        (lambda payload: payload.update(image_urls=[]), "aucune image"),
        (lambda payload: payload.update(success=False), "success absent"),
    ],
)
def test_unproven_responses_never_reach_the_library(tmp_path, monkeypatch, mutate, reason):
    app, service = _app(tmp_path, FakeService(mutate=mutate))
    _install(monkeypatch, service)
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "A red cube"})
        assert response.status_code == 502, response.text
        assert reason in response.json()["detail"]
        assert client.get("/api/assets").json() == []
    library = Path(app.state.settings.media_dir) / "library"
    assert not any(library.rglob("*")) if library.exists() else True


def test_avatar_sized_image_is_rejected(tmp_path, monkeypatch):
    app, service = _app(tmp_path, FakeService(image=_png((64, 64))))
    _install(monkeypatch, service)
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "A red cube"})
        assert response.status_code == 502
        assert "trop petite" in response.json()["detail"]
        assert client.get("/api/assets").json() == []


def test_non_image_bytes_are_rejected(tmp_path, monkeypatch):
    app, service = _app(tmp_path, FakeService(image=b"<html>not an image</html>"))
    _install(monkeypatch, service)
    with TestClient(app) as client:
        response = client.post("/api/assets/generate", json={"prompt": "A red cube"})
        assert response.status_code == 502
        assert client.get("/api/assets").json() == []


def test_reused_metaai_conversation_is_refused_by_the_database(tmp_path, monkeypatch):
    shared = ["conversation-shared", "conversation-shared"]
    app, service = _app(tmp_path, FakeService(conversations=shared))
    _install(monkeypatch, service)
    with TestClient(app) as client:
        first = client.post("/api/assets/generate", json={"prompt": "A red cube", "kind": "frame", "name": "Cube"})
        second = client.post("/api/assets/generate", json={"prompt": "A green sphere", "kind": "frame", "name": "Sphere"})
        assert first.status_code == 200, first.text
        assert second.status_code == 502, second.text
        assert "déjà produit une image" in second.json()["detail"]
        assets = client.get("/api/assets").json()
        assert len(assets) == 1 and assets[0]["provenance"]["prompt"] == "A red cube"
    library = Path(app.state.settings.media_dir) / "library"
    assert sum(1 for path in library.rglob("*") if path.is_file()) == 1


def test_identical_image_for_same_destination_is_reused_not_duplicated(tmp_path, monkeypatch):
    app, service = _app(tmp_path, FakeService())
    _install(monkeypatch, service)
    with TestClient(app) as client:
        first = client.post("/api/assets/generate", json={
            "prompt": "Cantine", "kind": "decor", "name": "Cantine", "description": "Steel tables",
        }).json()
        second = client.post("/api/assets/generate", json={
            "prompt": "Cantine", "kind": "decor", "name": "Cantine", "description": "Steel tables",
        }).json()
        assert second.get("reused") is True and second["id"] == first["id"]
        assert len(client.get("/api/assets").json()) == 1


def test_identical_image_for_another_plan_stays_a_distinct_asset(tmp_path, monkeypatch):
    app, service = _app(tmp_path, FakeService())
    _install(monkeypatch, service)
    with TestClient(app) as client:
        first = client.post("/api/assets/generate", json={"prompt": "A cube", "kind": "frame", "name": "Plan 1",
                                                          "description": "state one"}).json()
        second = client.post("/api/assets/generate", json={"prompt": "A cube", "kind": "frame", "name": "Plan 2",
                                                           "description": "state two"}).json()
        assert second["id"] != first["id"]
        assert len(client.get("/api/assets").json()) == 2


def test_batch_generation_skips_existing_isolates_failures_and_stops_on_outage(tmp_path, monkeypatch):
    app, service = _app(tmp_path, FakeService())
    project = _shot_project()
    existing = tmp_path / "existing.png"
    existing.write_bytes(_png())
    asset = app.state.repo.add_asset(kind="character", name="Zeste", description="Yellow lemon detective",
                                    file_path=existing, width=512, height=768)
    project.characters[0].image_ref = asset["file_path"]
    app.state.repo.save_project(project)
    _install(monkeypatch, service)

    with TestClient(app) as client:
        report = client.post(f"/api/projects/{project.id}/generate-images", json={}).json()
        assert report["results"][0]["status"] == "skipped"
        assert report["generated"] == 3  # 1 décor + 2 plans ; personnage déjà présent
        assert len(report["results"]) == 4
        assert not report["stopped"]
        saved = app.state.repo.get_project(project.id)
        assert all(shot.image_ref and Path(shot.image_ref).is_file() for shot in saved.shots)
        assert saved.decors[0].asset_id and Path(app.state.repo.get_asset(saved.decors[0].asset_id)["file_path"]).is_file()

    outage = FakeService(post_status=503)
    _install(monkeypatch, outage)
    with TestClient(app) as client:
        other = _shot_project()
        other.id = "outage-project"
        app.state.repo.save_project(other)
        stopped = client.post("/api/projects/outage-project/generate-images", json={}).json()
        assert stopped["stopped"] is True
        assert stopped["failed"] >= 1
        assert outage.calls == 1  # aucune boucle sur le service indisponible


def test_library_audit_and_retirement_preserve_files_and_detach_references(tmp_path):
    app, _ = _app(tmp_path, FakeService())
    settings = app.state.settings
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    verified_file = settings.media_dir / "verified.png"
    verified_file.write_bytes(_png())
    legacy_file = settings.media_dir / "legacy.png"
    legacy_file.write_bytes(_png(color=(20, 120, 20)))
    missing_file = settings.media_dir / "missing.png"
    missing_file.write_bytes(_png(color=(20, 20, 120)))

    verified = app.state.repo.add_verified_asset(
        kind="character", name="Zeste", description="Yellow lemon", file_path=verified_file,
        width=512, height=768,
        provenance={"attribution": "fresh_assistant_response", "request_id": "r1",
                    "conversation_id": "c1", "prompt": "Zeste"},
    )
    legacy = app.state.repo.add_asset(kind="decor", name="Cuisine", description="Kitchen",
                                     file_path=legacy_file, width=512, height=768)
    missing = app.state.repo.add_asset(kind="frame", name="Plan", description="Frame",
                                      file_path=missing_file, width=512, height=768)
    missing_file.unlink()

    project = _shot_project()
    project.characters[0].image_ref = verified["file_path"]
    project.decors[0].asset_id = legacy["id"]
    project.shots[0].image_ref = legacy["file_path"]
    project.shots[0].image_validated = True
    project.shots[0].decor_asset_id = legacy["id"]
    app.state.repo.save_project(project)

    with TestClient(app) as client:
        audit = client.get("/api/assets/audit").json()
        by_id = {item["id"]: item for item in audit["assets"]}
        assert by_id[verified["id"]]["verified"] is True
        assert by_id[legacy["id"]]["verified"] is False
        assert by_id[missing["id"]]["file_exists"] is False
        assert {ref["entity_type"] for ref in by_id[legacy["id"]]["references"]} == {
            "decor", "shot", "shot_decor",
        }
        assert legacy_file.is_file()

        removed = client.delete(f"/api/assets/{legacy['id']}")
        assert removed.status_code == 200
        assert removed.json()["detached_references"] == 3
        saved = app.state.repo.get_project(project.id)
        assert saved.decors[0].asset_id is None
        assert saved.shots[0].image_ref is None and saved.shots[0].image_validated is False
        assert saved.shots[0].decor_asset_id is None
        assert legacy_file.is_file() and verified_file.is_file()


def test_manual_upload_is_kept_as_unverified_import(tmp_path):
    app, _ = _app(tmp_path, FakeService())
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/assets/upload",
            data={"kind": "frame", "name": "Image importée", "description": "secours"},
            files={"file": ("manual.png", _png(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset = uploaded.json()
        assert asset["source"] == "import" and asset["verified"] is False
        assert Path(asset["file_path"]).is_file()
        assert client.get("/api/assets/audit").json()["unverified"] == 1


def test_client_maps_unreachable_service_without_leaking_details(tmp_path):
    settings = V2Settings(data_dir=tmp_path / "data", metaai_api_url="http://127.0.0.1:9")

    def boom(**kwargs):
        raise httpx.ConnectError("connection refused")

    client = MetaAIImageClient(settings, http_client_factory=boom)
    with pytest.raises(MetaAIError) as excinfo:
        client.generate("A red cube")
    assert excinfo.value.status_code == 503
    assert "ne répond pas" in str(excinfo.value)


def test_dialogue_does_not_leak_into_image_prompt(tmp_path, monkeypatch):
    prompts: list[str] = []

    class Recorder(FakeService):
        def post(self, url, **kwargs):
            prompts.append(kwargs["json"]["prompt"])
            return super().post(url, **kwargs)

    app, service = _app(tmp_path, Recorder())
    _install(monkeypatch, service)
    project = _shot_project()
    project.shots[0].dialogue = [DialogueLine(speaker_id="zeste", text="Le trésor est ici !")]
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/shots/s1/generate-image")
        assert response.status_code == 200, response.text
    assert "Le trésor" not in prompts[0]
    assert "Zeste faces a closed safe" in prompts[0]
