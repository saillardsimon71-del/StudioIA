"""Tests unitaires de l'intégration image MetaAI, sans serveur ni compte Meta réels."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient
from PIL import Image

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from api.app import create_app  # noqa: E402
from core.config import V2Settings  # noqa: E402
from core.scenario import Character, DialogueLine, Project, Shot  # noqa: E402


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


def _shot_project():
    return Project(
        id="auto-images", idea="Un citron inspecte un coffre.", title="Enquete",
        characters=[
            Character(id="zeste", name="Zeste", description="Yellow lemon detective wearing a beige coat"),
            Character(id="absent", name="Absent", description="Purple dragon with wings"),
        ],
        shots=[
            Shot(id="s1", duration_s=4, action="Medium close-up in a dim kitchen. Zeste opens the safe.",
                 initial_state="Zeste stands beside a closed safe.", final_state="The safe is open.",
                 dialogue=[DialogueLine(speaker_id="zeste", text="Le trésor est ici !")]),
            Shot(id="s2", duration_s=4, action="Wide shot. Zeste raises the treasure.",
                 initial_state="Zeste stands in front of the open safe.", continue_from_previous=True),
        ],
    )


def test_shot_image_uses_only_starting_frame_and_persists_association(tmp_path, monkeypatch):
    sent = []

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            sent.append(kwargs["json"]["prompt"])
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    settings = V2Settings(data_dir=tmp_path / "data")
    app = create_app(settings=settings)
    before = _shot_project()
    app.state.repo.save_project(before)
    with TestClient(app) as client:
        response = client.post("/api/projects/auto-images/shots/s1/generate-image")
        assert response.status_code == 200, response.text
        saved = response.json()
        assert Path(saved["shots"][0]["image_ref"]).is_file()
        assert saved["shots"][0]["image_validated"] is False
        assert saved["shots"][1] == before.shots[1].model_dump(mode="json")
        assets = client.get("/api/assets").json()
        assert len(assets) == 1 and assets[0]["kind"] == "frame"
        assert assets[0]["file_path"] == saved["shots"][0]["image_ref"]
    prompt = sent[0]
    assert "9:16" in prompt and "t=0" in prompt
    assert "Zeste stands beside a closed safe" in prompt
    assert "Yellow lemon detective" in prompt and "dim kitchen" in prompt
    assert "Medium close-up" in prompt
    assert "opens the safe" not in prompt and "The safe is open" not in prompt
    assert "Le trésor" not in prompt and "Purple dragon" not in prompt
    assert create_app(settings=settings).state.repo.get_project(before.id).shots[0].image_ref == saved["shots"][0]["image_ref"]


def test_shot_image_failure_then_retry_preserves_other_images_and_continuity(tmp_path, monkeypatch):
    failing = True

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            if failing:
                raise ValueError("service indisponible")
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _shot_project()
    project.shots[0].image_ref = "manual.png"
    project.shots[0].image_validated = True
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        url = "/api/projects/auto-images/shots/s2/generate-image"
        failed = client.post(url)
        assert failed.status_code == 502, failed.text
        assert app.state.repo.get_project(project.id).shots[1].image_ref is None
        assert client.get("/api/assets").json() == []
        failing = False
        saved = client.post(url).json()
        assert Path(saved["shots"][1]["image_ref"]).is_file()
        assert saved["shots"][1]["continue_from_previous"] is True
        assert saved["shots"][0] == project.shots[0].model_dump(mode="json")
        assert len(client.get("/api/assets").json()) == 1


def test_shot_generation_keeps_existing_or_concurrently_replaced_image(tmp_path, monkeypatch):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _shot_project()
    app.state.repo.save_project(project)
    manual = tmp_path / "manual-replacement.png"
    manual.write_bytes(_png())

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            current = app.state.repo.get_project(project.id)
            current.shots[0].image_ref = str(manual)
            current.title = "Titre modifie pendant la generation"
            app.state.repo.save_project(current)
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    with TestClient(app) as client:
        url = "/api/projects/auto-images/shots/s1/generate-image"
        saved = client.post(url).json()
        assert saved["shots"][0]["image_ref"] == str(manual)
        assert saved["title"] == "Titre modifie pendant la generation"
        saved = client.post(url).json()
        assert saved["shots"][0]["image_ref"] == str(manual)
        assert len(client.get("/api/assets").json()) == 1


def test_shot_generation_requires_initial_state_without_calling_llm_or_modal(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Aucun appel externe ne doit avoir lieu ici")

    monkeypatch.setattr("api.app.httpx.Client", forbidden)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"),
                     llm_transport=forbidden, modal_runner=forbidden)
    project = _shot_project()
    project.shots[0].initial_state = ""
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        response = client.post("/api/projects/auto-images/shots/s1/generate-image")
        assert response.status_code == 422, response.text
        assert "s1" in response.json()["detail"]
        assert client.post("/api/projects/auto-images/shots/unknown/generate-image").status_code == 404


def test_decor_is_generated_from_name_and_description_without_a_manual_prompt(tmp_path, monkeypatch):
    prompts = []

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            prompts.append(kwargs["json"]["prompt"])
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        response = client.post("/api/assets/generate-decor", json={
            "name": "Cantine", "description": "Prison dining hall, steel tables, pale green walls",
        })
        assert response.status_code == 200, response.text
        asset = response.json()
        assert asset["kind"] == "decor" and asset["name"] == "Cantine"
        assert asset["description"] == "Prison dining hall, steel tables, pale green walls"
        assert Path(asset["file_path"]).is_file()
        assert "9:16" in prompts[0] and "steel tables" in prompts[0]
        assert "No characters" in prompts[0]


def test_shot_decor_is_saved_and_reused_in_the_automatic_frame_prompt(tmp_path, monkeypatch):
    prompts = []

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            prompts.append(kwargs["json"]["prompt"])
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    file = tmp_path / "decor.png"
    file.write_bytes(_png())
    cantine = app.state.repo.add_asset(kind="decor", name="Cantine", description="Steel tables and green walls",
                                     file_path=file, width=17, height=29)
    app.state.repo.add_asset(kind="decor", name="Cellule", description="Bunk beds and barred window",
                             file_path=file, width=17, height=29)
    project = _shot_project().model_dump(mode="json")
    project["shots"][0]["decor_asset_id"] = cantine["id"]
    with TestClient(app) as client:
        saved = client.put("/api/projects/auto-images", json=project)
        assert saved.status_code == 200, saved.text
        response = client.post("/api/projects/auto-images/shots/s1/generate-image")
        assert response.status_code == 200, response.text
        assert response.json()["shots"][0]["decor_asset_id"] == cantine["id"]
    assert "Cantine" in prompts[0] and "Steel tables and green walls" in prompts[0]
    assert "Bunk beds" not in prompts[0]
    assert "opens the safe" not in prompts[0]


def test_shot_rejects_a_character_as_decor_before_generating(tmp_path, monkeypatch):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    file = tmp_path / "character.png"
    file.write_bytes(_png())
    asset = app.state.repo.add_asset(kind="character", name="Zeste", description="a lemon",
                                    file_path=file, width=17, height=29)
    project = _shot_project().model_dump(mode="json")
    project["shots"][0]["decor_asset_id"] = asset["id"]
    with TestClient(app) as client:
        saved = client.put("/api/projects/auto-images", json=project)
        assert saved.status_code == 200, saved.text
        response = client.post("/api/projects/auto-images/shots/s1/generate-image")
        assert response.status_code == 422, response.text
        assert "s1" in response.json()["detail"]
        assert len(client.get("/api/assets").json()) == 1


def _project_with_decors():
    payload = _shot_project().model_dump(mode="json")
    payload["decors"] = [{"id": "cantine", "name": "Cantine", "description": "Steel tables and pale green walls"}]
    for shot in payload["shots"]:
        shot["decor_id"] = "cantine"
    return Project.model_validate(payload)


def test_reference_images_are_persisted_once_and_reused_for_each_plan(tmp_path, monkeypatch):
    prompts = []

    def forbidden(*args, **kwargs):
        pytest.fail("Ni DeepSeek ni Modal ne doivent etre appeles pour les images")

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            prompts.append(kwargs["json"]["prompt"])
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"), llm_transport=forbidden, modal_runner=forbidden)
    project = _project_with_decors()
    manual = tmp_path / "manual-character.png"
    manual.write_bytes(_png())
    project.characters[1].image_ref = str(manual)
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        for endpoint in ["characters/zeste", "characters/absent", "decors/cantine"] * 2:
            response = client.post(f"/api/projects/{project.id}/{endpoint}/generate-image")
            assert response.status_code == 200, response.text
        references = response.json()
        assert references["characters"][1]["image_ref"] == str(manual)
        assert Path(references["characters"][0]["image_ref"]).is_file()
        assert references["decors"][0]["asset_id"]
        assert len(client.get("/api/assets").json()) == 2
        for shot in project.shots:
            response = client.post(f"/api/projects/{project.id}/shots/{shot.id}/generate-image")
            assert response.status_code == 200, response.text
        assert all(shot["image_ref"] for shot in response.json()["shots"])
        assert len(client.get("/api/assets").json()) == 4
    assert "Yellow lemon detective" in prompts[0]
    assert "No characters" in prompts[1]
    assert all("Steel tables and pale green walls" in prompt for prompt in prompts[2:])
    assert create_app(settings=app.state.settings).state.repo.get_project(project.id).decors[0].asset_id


def test_decor_failure_can_be_retried_and_manually_replaced(tmp_path, monkeypatch):
    failing = True

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            if failing:
                raise ValueError("indisponible")
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _project_with_decors()
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        base = f"/api/projects/{project.id}/decors/cantine"
        assert client.post(base + "/generate-image").status_code == 502
        assert app.state.repo.get_project(project.id).decors[0].asset_id is None
        failing = False
        generated = client.post(base + "/generate-image")
        assert generated.status_code == 200, generated.text
        uploaded = client.post("/api/assets/upload", data={"kind": "decor", "name": "Cantine manuelle"},
                               files={"file": ("manual.png", _png(), "image/png")}).json()
        saved = client.post(base + "/assign-asset", json={"asset_id": uploaded["id"]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["decors"][0]["asset_id"] == uploaded["id"]
        assert client.post(base + "/generate-image").json()["decors"][0]["asset_id"] == uploaded["id"]
        assert len(client.get("/api/assets").json()) == 2


def test_replace_full_scenario_validates_before_saving_and_preserves_other_projects(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Le remplacement ne doit appeler aucun fournisseur")

    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"), llm_transport=forbidden, modal_runner=forbidden)
    original = _project_with_decors()
    original.shots[0].image_ref = "manual.png"
    original.shots[0].image_validated = True
    app.state.repo.save_project(original)
    other = original.model_copy(deep=True)
    other.id = "other"
    app.state.repo.save_project(other)
    with TestClient(app) as client:
        url = f"/api/projects/{original.id}/replace-scenario"
        copied = original.model_dump(mode="json")
        response = client.post(url, json=copied)
        assert response.status_code == 200, response.text
        assert response.json() == copied
        broken = {**copied, "shots": [{**copied["shots"][0], "decor_id": "unknown"}]}
        assert client.post(url, json=broken).status_code == 422
        assert client.post(url, json={"title": "Incomplete"}).status_code == 422
        assert app.state.repo.get_project(original.id) == original
        edited = {**copied, "id": other.id, "title": "Version corrigee"}
        edited["shots"] = [{**copied["shots"][0], "action": "Zeste closes the safe.",
                            "ltx_prompt": "obsolete", "rendered_video_ref": "old.mp4", "human_validated": True}]
        response = client.post(url, json=edited)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["id"] == original.id and saved["title"] == "Version corrigee"
        assert saved["shots"][0]["image_ref"] == "manual.png"
        assert saved["shots"][0]["image_validated"] is True  # L'etat initial n'a pas change.
        assert saved["shots"][0]["rendered_video_ref"] is None
        assert saved["shots"][0]["human_validated"] is False
        assert saved["shots"][0]["ltx_prompt"] == ""
        assert app.state.repo.get_project(other.id) == other


def test_decor_catalogue_is_optional_for_old_projects_but_references_are_validated():
    assert _shot_project().decors == []
    payload = _shot_project().model_dump(mode="json")
    payload["shots"][0]["decor_id"] = "unknown"
    with pytest.raises(ValueError, match="decor"):
        Project.model_validate(payload)


@pytest.mark.parametrize('status', [401, 422, 429, 503, 504])
def test_meta_errors_keep_their_status_without_raw_urls_or_sensitive_details(tmp_path, monkeypatch, status):
    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            return httpx.Response(status, request=httpx.Request('POST', url), json={'detail': 'SYNTHETIC_SECRET_MUST_NOT_ESCAPE'})

    monkeypatch.setattr('api.app.httpx.Client', Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / 'data'))
    with TestClient(app) as client:
        response = client.post('/api/assets/generate', json={'prompt': 'A lemon'})
    assert response.status_code == status
    assert 'SYNTHETIC_SECRET' not in response.text
    assert 'developer.mozilla' not in response.text
    assert '/image' not in response.text


def test_image_prompt_preview_and_edit_preserve_story_and_existing_media(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("La preparation d'un prompt image reste locale")

    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"), llm_transport=forbidden, modal_runner=forbidden)
    project = _project_with_decors()
    project.shots[0].image_ref = "manual.png"
    project.shots[0].image_validated = True
    project.shots[0].rendered_video_ref = "approved.mp4"
    project.shots[0].human_validated = True
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        url = f"/api/projects/{project.id}/shots/s1/image-prompt"
        preview = client.get(url)
        assert preview.status_code == 200, preview.text
        assert "Zeste stands beside a closed safe" in preview.json()["prompt"]
        assert "Steel tables" in preview.json()["prompt"]
        assert client.get(url.replace("s1/", "unknown/")).status_code == 404
        prompt = "Vertical 9:16 still, t=0. Yellow lemon in a beige coat beside a closed safe."
        saved = client.put(url, json={"prompt": prompt}).json()
        assert saved["shots"][0]["image_prompt"] == prompt
        assert client.get(url).json()["prompt"] == prompt
        saved["shots"][0]["image_prompt"] = ""
        assert saved == project.model_dump(mode="json")
    assert create_app(settings=app.state.settings).state.repo.get_project(project.id).shots[0].image_prompt == prompt


def test_image_prompt_survives_refusal_and_is_reused_without_narrative_context(tmp_path, monkeypatch):
    sent = []

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            sent.append(kwargs["json"]["prompt"])
            if len(sent) == 1:
                return httpx.Response(422, request=httpx.Request("POST", url))
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _shot_project()
    project.shots[1].image_ref = "other-manual.png"
    app.state.repo.save_project(project)
    prompt = "Vertical 9:16 still, t=0. A yellow lemon in a beige coat waits beside a closed safe. No text."
    with TestClient(app) as client:
        base = f"/api/projects/{project.id}/shots/s1"
        assert client.put(base + "/image-prompt", json={"prompt": prompt}).status_code == 200
        assert sent == []
        assert client.post(base + "/generate-image").status_code == 422
        assert app.state.repo.get_project(project.id).shots[0].image_prompt == prompt
        saved = client.post(base + "/generate-image").json()
        assert saved["shots"][0]["image_prompt"] == prompt
        assert saved["shots"][0]["action"] == project.shots[0].action
        assert saved["shots"][0]["initial_state"] == project.shots[0].initial_state
        assert saved["shots"][0]["dialogue"] == project.shots[0].model_dump()["dialogue"]
        assert saved["shots"][1] == project.shots[1].model_dump(mode="json")
    assert sent == [prompt, prompt]


def test_image_prompt_length_and_reset_do_not_damage_old_projects(tmp_path):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _shot_project()
    app.state.repo.save_project(project)
    with TestClient(app) as client:
        url = f"/api/projects/{project.id}/shots/s1/image-prompt"
        assert client.put(url, json={"prompt": "x" * 4001}).status_code == 422
        assert app.state.repo.get_project(project.id) == project
        assert client.put(url, json={"prompt": "x" * 4000}).status_code == 200
        assert len(client.get(url).json()["prompt"]) == 4000
        assert client.put(url, json={"prompt": "  "}).status_code == 200
        assert app.state.repo.get_project(project.id) == project
        assert "Zeste stands beside a closed safe" in client.get(url).json()["prompt"]


def test_prompt_changed_during_generation_does_not_assign_obsolete_frame(tmp_path, monkeypatch):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _shot_project()
    app.state.repo.save_project(project)

    class Service(_MetaAIService):
        def post(self, url, **kwargs):
            current = app.state.repo.get_project(project.id)
            current.shots[0].image_prompt = "A revised initial frame."
            app.state.repo.save_project(current)
            return super().post(url, **kwargs)

    monkeypatch.setattr("api.app.httpx.Client", Service)
    with TestClient(app) as client:
        saved = client.post(f"/api/projects/{project.id}/shots/s1/generate-image").json()
        assert saved["shots"][0]["image_ref"] is None
        assert saved["shots"][0]["image_prompt"] == "A revised initial frame."
        assert len(client.get("/api/assets").json()) == 1


def test_replacing_only_image_prompt_keeps_existing_video_validation(tmp_path):
    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"))
    project = _shot_project()
    project.shots[0].image_ref = "manual.png"
    project.shots[0].image_validated = True
    project.shots[0].rendered_video_ref = "approved.mp4"
    project.shots[0].human_validated = True
    app.state.repo.save_project(project)
    edited = project.model_dump(mode="json")
    edited["shots"][0]["image_prompt"] = "A calmer still before the gesture."
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{project.id}/replace-scenario", json=edited)
        assert response.status_code == 200, response.text
        assert response.json() == edited
