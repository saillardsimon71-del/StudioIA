from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image


@pytest.fixture
def worker(monkeypatch):
    import asyncio
    import importlib

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    policy = asyncio.get_event_loop_policy()
    try:
        return importlib.import_module("deploy.modal_h3")
    finally:
        asyncio.set_event_loop_policy(policy)


def request(**kwargs):
    from calliope.engines.modal_h3 import build_request

    return build_request(
        **{
            "prompt": "Un voilier traverse la baie, bruit des vagues.",
            "duration_sec": 5,
            "aspect_ratio": "16:9",
            "seed": 42,
            **kwargs,
        }
    )


def test_duration_rounds_up_to_supported_grid():
    payload = request(duration_sec=6)
    assert payload["frames"] == 158
    assert payload["duration_sec"] == pytest.approx(158 / 24)
    assert payload["requested_duration_sec"] == 6
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prompt": " "},
        {"duration_sec": 0},
        {"duration_sec": float("nan")},
        {"duration_sec": float("inf")},
        {"duration_sec": 16},
        {"seed": -1},
        {"seed": True},
        {"seed": 2**64},
        {"aspect_ratio": "4:3"},
        {"audio_mode": "dubbed"},
    ],
)
def test_invalid_requests_fail_before_submission(kwargs):
    with pytest.raises(ValueError):
        request(**kwargs)


@pytest.mark.parametrize(
    "aspect,width,height",
    [
        ("16:9", 832, 480),
        ("9:16", 480, 832),
        ("1:1", 640, 640),
    ],
)
def test_text_to_video_has_no_dangling_image_reference(aspect, width, height):
    from calliope.engines.h3_graph import build_h3_graph

    graph = build_h3_graph(request(aspect_ratio=aspect), output_prefix="studio/test")
    assert not any(node["class_type"] == "LoadImage" for node in graph.values())
    condition = next(
        n["inputs"] for n in graph.values() if n["class_type"] == "MiniMaxH3ImageToVideo"
    )
    assert "first_frame" not in condition
    assert (condition["width"], condition["height"], condition["length"]) == (width, height, 124)
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph
    video = next(n["inputs"] for n in graph.values() if n["class_type"] == "CreateVideo")
    assert graph[video["audio"][0]]["class_type"] == "VAEDecodeAudio"


def test_image_to_video_transports_validated_png(tmp_path):
    from calliope.engines.h3_graph import build_h3_graph

    image = tmp_path / "référence photo.jpg"
    Image.new("RGB", (64, 64), "blue").save(image)
    payload = request(image_path=image)
    assert base64.b64decode(payload["image_base64"]).startswith(b"\x89PNG")
    assert payload["continuity_mode"] == "image"
    graph = build_h3_graph(payload, output_prefix="studio/test", uploaded_image="reference.png")
    condition = graph["104"]["inputs"]
    assert graph[condition["first_frame"][0]]["inputs"]["image"] == "reference.png"


def test_rejects_non_image_input(tmp_path):
    path = tmp_path / "invalid.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="image"):
        request(image_path=path)


def test_continuation_uses_actual_last_frame_only(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg absent")
    video = tmp_path / "vidéo précédente.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=24:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    payload = request(previous_video_path=video)
    assert payload["continuity_mode"] == "last_frame"
    with Image.open(io.BytesIO(base64.b64decode(payload["image_base64"]))) as image:
        red, green, blue = image.getpixel((32, 32))
        assert blue > 240 and red < 10 and green < 10
    assert not any("video" in key or "audio_base64" in key for key in payload)


def test_continuation_rejects_missing_video(tmp_path):
    with pytest.raises(ValueError, match="vidéo"):
        request(previous_video_path=tmp_path / "missing.mp4")


def test_reference_and_continuation_cannot_be_combined(tmp_path):
    with pytest.raises(ValueError):
        request(image_path=tmp_path / "image.png", previous_video_path=tmp_path / "video.mp4")


def test_worker_metadata_describes_actual_video_and_audio(tmp_path, worker):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg absent")
    video = tmp_path / "audio réel.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    metadata = worker._probe_video(video)
    assert metadata["width"] == metadata["height"] == 64
    assert metadata["fps"] == 24
    assert metadata["frames"] == 24
    assert metadata["duration_sec"] == pytest.approx(1)
    assert metadata["has_audio"] is True


def test_worker_model_links_never_write_volume(tmp_path, monkeypatch, worker):
    root = tmp_path / "models-volume"
    comfy = tmp_path / "ComfyUI"
    locations = [
        ("diffusion_models", worker.H3_MODEL_FILES["unet"]),
        ("text_encoders", worker.H3_MODEL_FILES["clip"]),
        ("vae", worker.H3_MODEL_FILES["video_vae"]),
        ("vae", worker.H3_MODEL_FILES["audio_vae"]),
    ]
    for category, filename in locations:
        path = root / category / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing model")
    monkeypatch.setattr(worker, "MODELS_ROOT", root)
    monkeypatch.setattr(worker, "COMFYUI_DIR", comfy)
    try:
        (tmp_path / "symlink-check").symlink_to(root, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows ne permet pas les symlinks; worker déployé sous Linux")
        raise
    worker._mount_volume_models()
    worker._mount_volume_models()
    for category, filename in locations:
        assert (comfy / "models" / category / filename).resolve() == root / category / filename
        assert (root / category / filename).read_bytes() == b"existing model"


def test_silent_video_does_not_attach_native_audio():
    from calliope.engines.h3_graph import build_h3_graph

    graph = build_h3_graph(request(audio_mode="silent"), output_prefix="studio/test")
    assert "audio" not in graph["91"]["inputs"]
    assert not any(n["class_type"] == "VAEDecodeAudio" for n in graph.values())


def test_worker_rejects_tampered_payload():
    from calliope.engines.h3_graph import validate_request

    payload = request()
    with pytest.raises(ValueError):
        validate_request({**payload, "workflow": {}})
    with pytest.raises(ValueError):
        validate_request({**payload, "frames": 125})
    with pytest.raises(ValueError):
        validate_request({**payload, "duration_sec": 5})
    with pytest.raises(ValueError):
        validate_request({**payload, "width": 1344})


class FakeCall:
    object_id = "fc-existing"

    def __init__(self):
        self.results = []
        self.timeouts = []
        self.cancelled = False

    def get(self, timeout):
        self.timeouts.append(timeout)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def cancel(self, terminate_containers=False):
        self.cancelled = terminate_containers


def fake_modal(monkeypatch, call, *, hydrate_error=None):
    state = {"spawn_count": 0, "hydrate_count": 0, "call_ids": [], "payload": None}

    class Function:
        @staticmethod
        def from_name(app_name, function_name):
            assert (app_name, function_name) == ("studio-ia-h3", "render_clip")
            return Function()

        def hydrate(self):
            state["hydrate_count"] += 1
            if hydrate_error:
                raise hydrate_error

        def spawn(self, payload):
            state["spawn_count"] += 1
            state["payload"] = payload
            return call

    class FunctionCall:
        @staticmethod
        def from_id(call_id):
            state["call_ids"].append(call_id)
            return call

    module = SimpleNamespace(
        Function=Function,
        FunctionCall=FunctionCall,
        exception=SimpleNamespace(TimeoutError=TimeoutError),
    )
    monkeypatch.setitem(sys.modules, "modal", module)
    return state


def test_preflight_only_resolves_deployment_metadata(monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    state = fake_modal(monkeypatch, FakeCall())
    result = ModalH3Engine("studio-ia-h3", "render_clip").check_available()
    assert result["ok"] is True
    assert state["hydrate_count"] == 1
    assert state["spawn_count"] == 0


def test_preflight_error_never_exposes_credentials(monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    fake_modal(monkeypatch, FakeCall(), hydrate_error=RuntimeError("token secret-123"))
    result = ModalH3Engine("studio-ia-h3", "render_clip").check_available()
    assert result["ok"] is False
    assert "secret-123" not in json.dumps(result)


def test_poll_resumes_original_call_and_keeps_completed_result(monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    call = FakeCall()
    call.results = [TimeoutError(), {"video_base64": "bXA0", "metadata": {"engine": "FastH3"}}]
    state = fake_modal(monkeypatch, call)
    engine = ModalH3Engine("studio-ia-h3", "render_clip")
    assert engine.poll("fc-existing") is None
    result = engine.poll("fc-existing")
    assert result["video_base64"] == "bXA0"
    assert engine.poll("fc-existing") == result
    assert call.timeouts == [0, 0]
    assert state["spawn_count"] == 0
    assert set(state["call_ids"]) == {"fc-existing"}


def test_spawn_and_cancel_use_function_call_identity(monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    call = FakeCall()
    state = fake_modal(monkeypatch, call)
    engine = ModalH3Engine("studio-ia-h3", "render_clip")
    payload = request()
    call_id = engine.spawn(payload)
    assert call_id == "fc-existing"
    assert state["payload"] == payload
    engine.cancel(call_id)
    assert call.cancelled is True
    assert state["spawn_count"] == 1


def test_remote_execution_timeout_is_not_a_pending_poll(monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine, _load_modal

    modal = _load_modal()
    remote_error = modal.exception.FunctionTimeoutError("GPU deadline exceeded")
    call = FakeCall()
    call.results = [remote_error]
    fake_modal(monkeypatch, call)
    with pytest.raises(type(remote_error), match="deadline"):
        ModalH3Engine("studio-ia-h3", "render_clip").poll("fc-existing")


def test_modal_import_keeps_windows_subprocesses_available():
    code = """
import asyncio, sys
from calliope.engines.modal_h3 import _load_modal
policy = asyncio.get_event_loop_policy()
_load_modal()
assert asyncio.get_event_loop_policy() is policy
async def run():
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-c', 'print(42)', stdout=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    assert process.returncode == 0 and stdout.strip() == b'42'
asyncio.run(run())
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, timeout=30)


def test_trace_id_is_optional_and_cannot_inject_workflow():
    from calliope.engines.h3_graph import validate_request

    assert "trace_id" not in request()
    payload = request(trace_id="take-42")
    assert payload["trace_id"] == "take-42"
    validate_request(payload)
    for value in ("", "a" * 129, "secret\ntext", 42):
        with pytest.raises(ValueError):
            validate_request({**payload, "trace_id": value})
    with pytest.raises(ValueError):
        validate_request({**payload, "workflow": {}})


def test_progress_retrieves_named_dict_without_submission(monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine

    snapshot = {"trace_id": "take-42", "phase": "LOADING_MODEL", "events": []}
    modal = SimpleNamespace(Dict=SimpleNamespace(from_name=lambda name: store))
    store = SimpleNamespace(get=SimpleNamespace(aio=AsyncMock(side_effect=lambda key: snapshot if key == "take-42" else None)))
    monkeypatch.setitem(sys.modules, "modal", modal)
    engine = ModalH3Engine("studio-ia-h3", "render_clip")
    assert engine.progress("take-42") == snapshot
    assert engine.progress("missing") is None
    store.get.aio = AsyncMock(side_effect=RuntimeError("secret"))
    assert engine.progress("take-42") is None


def test_progress_cancels_blocked_async_dict_get(monkeypatch):
    import asyncio
    from calliope.engines import modal_h3

    state = {"entered": False, "cancelled": False}
    async def blocked(key):
        state["entered"] = True
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True
    store = SimpleNamespace(get=SimpleNamespace(aio=blocked))
    module = SimpleNamespace(Dict=SimpleNamespace(from_name=lambda name: store))
    monkeypatch.setitem(sys.modules, "modal", module)
    monkeypatch.setattr(modal_h3, "H3_PROGRESS_TIMEOUT_SEC", 0.01, raising=False)
    assert modal_h3.ModalH3Engine("studio-ia-h3", "render_clip").progress("take-1") is None
    assert state == {"entered": True, "cancelled": True}
