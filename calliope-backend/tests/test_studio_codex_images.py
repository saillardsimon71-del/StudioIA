import asyncio
import json
import sys
import time
from types import SimpleNamespace

import pytest

import calliope.config as config_module
from calliope.config import settings
from calliope.studio import service
from calliope.studio import codex_image
from tests.test_studio import new_board, png_bytes


@pytest.mark.asyncio
async def test_codex_images_use_subscription_without_an_image_api_key(monkeypatch):
    called = []

    async def generate(request, *, check_cancel=None):
        called.append(request)
        return png_bytes(), {"provider": "codex"}

    monkeypatch.setitem(sys.modules, "calliope.studio.codex_image", SimpleNamespace(generate=generate))
    monkeypatch.setattr(service, "settings", SimpleNamespace(
        **(settings.model_dump() | {"studio_image_provider": "codex", "studio_image_api_key": None})
    ))
    request = {"image_provider": "codex", "image_base_url": settings.studio_image_base_url,
               "image_model": "codex-imagegen", "aspect_ratio": "9:16", "prompt": "A deep hotel lobby."}
    content, metadata = await service._generate_image(request)
    assert content == png_bytes() and metadata["provider"] == "codex"
    assert called == [request]


def test_codex_provider_can_be_saved_without_changing_text_service(client, monkeypatch):
    monkeypatch.setattr(settings, "studio_image_provider", "api")
    before = client.get("/api/studio/config").json()
    response = client.patch("/api/studio/config", json={"image_provider": "codex"})
    assert response.status_code == 200
    config = response.json()
    assert config["image_provider"] == "codex"
    assert (config["llm_base_url"], config["llm_model"]) == (before["llm_base_url"], before["llm_model"])
    assert "image_api_key" not in config


def test_codex_image_job_freezes_provider_and_keeps_the_original_prompt(client, monkeypatch):
    monkeypatch.setattr(settings, "studio_image_provider", "codex")
    _, board = new_board(client)
    shot = board["scenes"][0]["shots"][0]
    response = client.post(f"/api/studio/shots/{shot['id']}/generate", json={
        "kind": "image", "request_id": "codex-image-test", "n_takes": 1,
    })
    assert response.status_code == 200, response.text
    take = response.json()["takes"][0]
    with service.database() as conn:
        request = json.loads(conn.execute(
            "SELECT request_json FROM studio_takes WHERE id=?", (take["id"],)
        ).fetchone()[0])
    assert request["image_provider"] == "codex"
    assert request["image_model"] == "codex-imagegen"
    assert shot["image_prompt"] in request["prompt"]
    assert take["engine"] == "Codex imagegen"


def test_api_key_login_is_not_accepted_as_a_subscription(monkeypatch):
    monkeypatch.setattr(codex_image, "_executable", lambda: "codex")
    monkeypatch.setattr(codex_image.subprocess, "run", lambda *args, **kw: SimpleNamespace(
        returncode=0, stdout="", stderr="Logged in using an API key",
    ))
    assert not codex_image.check_available()["ok"]


def test_codex_image_choice_survives_restarting_the_studio(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config_module, "CONFIG_FILE", path)
    isolated = config_module.Settings(studio_image_provider="codex")
    isolated.save_config_file()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["studio_image_provider"] == "codex"
    assert config_module.Settings(**stored).studio_image_provider == "codex"


@pytest.fixture
def native_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-use")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(codex_image, "_executable", lambda: "codex")
    monkeypatch.setattr(codex_image, "check_available", lambda: {"ok": True})
    image = tmp_path / "codex" / "generated_images" / "native.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png_bytes())
    seen = {}
    process = SimpleNamespace(poll=lambda: 0)

    def launch(command, **kwargs):
        seen.update(command=command, env=kwargs["env"], prompt=kwargs["stdin"].read().decode())
        result_path = command[command.index("--output-last-message") + 1]
        with open(result_path, "w", encoding="utf-8") as result:
            json.dump({"status": "done", "image_path": str(image), "error": ""}, result)
        return process

    monkeypatch.setattr(codex_image.subprocess, "Popen", launch)
    monkeypatch.setattr(codex_image, "_stop", lambda current: seen.update(stopped=current))
    return image, process, seen


@pytest.mark.asyncio
async def test_native_generator_uses_references_and_returns_tool_image(native_runner):
    image, _, seen = native_runner
    content, metadata = await codex_image.generate({
        "prompt": "Only Rico sits on the left.", "aspect_ratio": "9:16",
        "reference_paths": ["rico.png", "apartment.png"],
    })
    assert content == image.read_bytes() and metadata["provider"] == "codex"
    assert "Only Rico sits on the left." in seen["prompt"]
    assert seen["command"][-5:] == ["--image", "rico.png", "--image", "apartment.png", "-"]
    assert "--ignore-user-config" in seen["command"]
    assert seen["command"][seen["command"].index("--sandbox") + 1] == "read-only"
    assert "OPENAI_API_KEY" not in seen["env"] and "CODEX_API_KEY" not in seen["env"]


@pytest.mark.asyncio
async def test_cancel_stops_only_the_native_image_process(native_runner):
    _, process, seen = native_runner
    process.poll = lambda: None
    calls = 0

    async def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await codex_image.generate({"prompt": "A hotel.", "aspect_ratio": "9:16"}, check_cancel=cancel)
    assert seen["stopped"] is process


@pytest.mark.asyncio
async def test_native_timeout_stops_the_worker_without_resubmitting(native_runner, monkeypatch):
    _, process, seen = native_runner
    process.poll = lambda: None
    clock = iter([0, 601])
    monkeypatch.setattr(codex_image, "time", SimpleNamespace(time=time.time, monotonic=lambda: next(clock)))
    with pytest.raises(ValueError, match="10 minutes"):
        await codex_image.generate({"prompt": "A hotel.", "aspect_ratio": "9:16"})
    assert seen["stopped"] is process


def test_returned_image_must_belong_to_native_generator(native_runner, tmp_path):
    outside = tmp_path / "unrelated.png"
    outside.write_bytes(png_bytes())
    with pytest.raises(ValueError, match="générateur"):
        codex_image._generated_image({"status": "done", "image_path": str(outside)}, time.time())
    image = native_runner[0]
    with pytest.raises(ValueError, match="ancien"):
        codex_image._generated_image({"status": "done", "image_path": str(image)}, time.time() + 60)


@pytest.mark.asyncio
async def test_changing_provider_does_not_silently_retry_a_frozen_api_job(monkeypatch):
    monkeypatch.setattr(settings, "studio_image_provider", "codex")
    with pytest.raises(ValueError, match="générateur.*changé"):
        await service._generate_image({"image_model": "agnes-2.5-flash", "reference_paths": []})
