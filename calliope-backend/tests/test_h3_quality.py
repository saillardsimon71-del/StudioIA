import base64

import pytest
from PIL import Image

from calliope.config import settings
from calliope.engines.h3_graph import build_h3_graph, validate_request
from calliope.engines.modal_h3 import build_request


@pytest.mark.parametrize("aspect,size", [("9:16", (768, 1344)), ("16:9", (1344, 768)), ("1:1", (992, 992))])
def test_quality_uses_image_trained_base_at_native_resolution(tmp_path, aspect, size):
    image = tmp_path / "first.png"
    Image.new("RGB", (64, 64), "green").save(image)
    payload = build_request(prompt="A cat speaks French.", duration_sec=5.8, aspect_ratio=aspect,
                            seed=42, image_path=image, video_profile="quality")
    assert (payload["width"], payload["height"]) == size
    assert payload["steps"] == 25
    assert base64.b64decode(payload["image_base64"]) == image.read_bytes()
    graph = build_h3_graph(payload, output_prefix="studio/quality", uploaded_image="first.png")
    assert graph["6"]["inputs"]["unet_name"] == "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    assert graph["104"]["inputs"]["first_frame"] == ["1", 0]
    assert not any(n["class_type"] in {"BlockSparseAttention", "ModelAttentionBackend", "MiniMaxH3SigmaShift", "LoraLoaderModelOnly"}
                   for n in graph.values())
    assert graph["9"]["inputs"]["model"] == graph["16"]["inputs"]["model"] == ["6", 0]
    assert graph["9"]["inputs"]["steps"] == 25
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


def test_old_fast_requests_keep_their_trained_schedule():
    payload = build_request(prompt="A cat speaks.", duration_sec=5, aspect_ratio="9:16", seed=42)
    payload.pop("video_profile", None)
    validate_request(payload)
    graph = build_h3_graph(payload, output_prefix="studio/legacy")
    assert graph["6"]["inputs"]["unet_name"] == "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"
    assert payload["steps"] == 8
    assert (payload["width"], payload["height"]) == (480, 832)
    assert graph["143"]["inputs"]["shift_video"] == 10
    assert graph["127"]["class_type"] == "BlockSparseAttention"


def test_profiles_cannot_accidentally_mix_models_and_schedules():
    payload = build_request(prompt="A cat speaks.", duration_sec=5, aspect_ratio="9:16", seed=42,
                            video_profile="quality")
    with pytest.raises(ValueError):
        validate_request({**payload, "steps": 8})
    with pytest.raises(ValueError):
        validate_request({**payload, "video_profile": "fast"})
    with pytest.raises(ValueError):
        build_request(prompt="A cat speaks.", duration_sec=5, aspect_ratio="9:16", seed=42,
                      video_profile="imaginary")


def test_video_profile_is_selectable_and_persisted_by_studio(client, monkeypatch):
    monkeypatch.setattr(settings, "studio_video_profile", "fast", raising=False)
    response = client.patch("/api/studio/config", json={"video_profile": "quality"})
    assert response.status_code == 200, response.text
    assert response.json()["video_profile"] == "quality"
    assert client.get("/api/studio/config").json()["video_profile"] == "quality"
    response = client.patch("/api/studio/config", json={"video_profile": "invented"})
    assert response.status_code == 422
    assert client.get("/api/studio/config").json()["video_profile"] == "quality"


def test_queued_take_freezes_the_video_profile(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import service
    monkeypatch.setattr(queue_manager, "paused", True)
    monkeypatch.setattr(settings, "studio_video_profile", "quality", raising=False)
    project_id = client.post("/api/studio/projects", json={"title": "Quality test", "idea": "A cat speaks."}).json()["id"]
    response = client.post(f"/api/studio/projects/{project_id}/plan", json={"text": "INT. ROOM - DAY\nA cat speaks.", "mode": "script", "use_llm": False})
    assert response.status_code == 200, response.text
    board = response.json()
    shot_id = board["scenes"][0]["shots"][0]["id"]
    response = client.post(f"/api/studio/shots/{shot_id}/generate", json={"kind": "video", "request_id": "quality-test"})
    assert response.status_code == 200, response.text
    import json
    with service.database() as conn:
        row = conn.execute("SELECT request_json FROM studio_takes WHERE clip_id=?", (shot_id,)).fetchone()
    monkeypatch.setattr(settings, "studio_video_profile", "fast")
    assert json.loads(row[0])["video_profile"] == "quality"
