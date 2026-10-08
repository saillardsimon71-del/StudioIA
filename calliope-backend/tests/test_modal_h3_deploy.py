from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    policy = asyncio.get_event_loop_policy()
    try:
        module = importlib.import_module("deploy.modal_h3")
    finally:
        asyncio.set_event_loop_policy(policy)
    monkeypatch.setattr(module, "_COMFY_PROCESS", None, raising=False)
    monkeypatch.setattr(module, "_COMFY_LOG", None, raising=False)
    return module


def test_comfy_reuses_process_and_restarts_after_crash(worker, monkeypatch):
    first = SimpleNamespace(poll=lambda: None)
    second = SimpleNamespace(poll=lambda: None)
    starts = iter([(first, Path("first.log")), (second, Path("second.log"))])
    monkeypatch.setattr(worker, "_start_comfyui", lambda: next(starts))
    monkeypatch.setattr(worker, "_http_json", lambda *args, **kwargs: {})
    assert worker._ensure_comfyui()[0] is first
    assert worker._ensure_comfyui()[0] is first
    first.poll = lambda: 1
    assert worker._ensure_comfyui()[0] is second


def test_unhealthy_live_process_is_stopped_before_restart(worker, monkeypatch):
    old = SimpleNamespace(poll=lambda: None)
    new = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(worker, "_COMFY_PROCESS", old)
    monkeypatch.setattr(worker, "_COMFY_LOG", Path("old.log"))
    monkeypatch.setattr(worker, "_http_json", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    stopped = []
    monkeypatch.setattr(worker, "_stop_process", stopped.append)
    monkeypatch.setattr(worker, "_start_comfyui", lambda: (new, Path("new.log")))
    assert worker._ensure_comfyui()[0] is new
    assert stopped == [old]


@pytest.mark.parametrize("event_kind", ["progress", "progress_state"])
def test_sampler_phases_are_observed_and_telemetry_redacts_inputs(worker, monkeypatch, event_kind):
    writes = []
    monkeypatch.setattr(worker, "progress_store", SimpleNamespace(put=SimpleNamespace(aio=AsyncMock(side_effect=lambda key, val: writes.append(json.loads(json.dumps(val)))))))
    progress = worker._Progress("take-42")
    progress.snapshot["comfy_prompt_id"] = "comfy-42"
    progress.comfy_event({"type": "execution_start", "data": {"prompt_id": "other"}})
    assert not progress.snapshot["events"]
    progress.comfy_event({"type": "execution_start", "data": {"prompt_id": "comfy-42", "prompt": "secret dialogue"}})
    progress.comfy_event({"type": "executing", "data": {"prompt_id": "comfy-42", "node": "13"}})
    assert progress.snapshot["phase"] == "LOADING_MODEL"
    progress.comfy_event({"type": "executing", "data": {"prompt_id": "comfy-42", "node": "14"}})
    data = {"prompt_id": "comfy-42", "node": "14", "value": 2, "max": 8}
    if event_kind == "progress_state":
        data = {"prompt_id": "comfy-42", "nodes": {"14": {"node_id": "14", "value": 2, "max": 8}}}
    progress.comfy_event({"type": event_kind, "data": data})
    assert progress.snapshot["phase"] == "GENERATING"
    assert progress.snapshot["sampling"] == {"node": "14", "value": 2, "max": 8}
    progress.comfy_event({"type": "progress", "data": {"prompt_id": "comfy-42", "node": "10", "value": 1, "max": 3}})
    assert progress.snapshot["sampling"]["max"] == 8
    assert "secret" not in json.dumps(writes)
    assert all(event["at"].endswith("+00:00") for event in progress.snapshot["events"])


def test_telemetry_failure_does_not_break_render_phase(worker, monkeypatch):
    monkeypatch.setattr(worker, "progress_store", SimpleNamespace(put=SimpleNamespace(aio=AsyncMock(side_effect=RuntimeError("secret")))))
    progress = worker._Progress("take-42")
    progress.emit("FAILED", "render failed")
    assert progress.snapshot["phase"] == "FAILED"


def test_history_success_waits_for_delayed_readable_artifact(worker, monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "COMFYUI_DIR", tmp_path)
    output = tmp_path / "output" / "studio" / "clip.mp4"
    output.parent.mkdir(parents=True)
    monkeypatch.setattr(worker, "_http_json", lambda *a, **k: {"p": {"status": {"completed": True}, "outputs": {}}})
    sleeps = []
    def delayed_file(seconds):
        sleeps.append(seconds)
        output.write_bytes(b"mp4")
    monkeypatch.setattr(worker.time, "sleep", delayed_file)
    process = SimpleNamespace(poll=lambda: None)
    assert worker._wait_for_video("p", "studio/clip", process, tmp_path / "log") == output
    assert sleeps


@pytest.mark.parametrize("source_in_output", [True, False])
def test_run_clip_reuses_live_comfy_then_resets_on_failure(worker, monkeypatch, tmp_path, source_in_output):
    from calliope.engines.modal_h3 import build_request

    payload = build_request(prompt="Private dialogue", duration_sec=5, aspect_ratio="16:9", seed=42, trace_id="take-1")
    process = SimpleNamespace(poll=lambda: None)
    starts, stopped, snapshots = [], [], []
    def start():
        starts.append(True)
        return process, tmp_path / "log"
    def http(route, body=None, **kwargs):
        return {"prompt_id": body["prompt_id"]} if body else {}
    monkeypatch.setattr(worker, "_start_comfyui", start)
    monkeypatch.setattr(worker, "_stop_process", stopped.append)
    monkeypatch.setattr(worker, "_http_json", http)
    monkeypatch.setattr(worker, "progress_store", SimpleNamespace(put=SimpleNamespace(aio=AsyncMock(side_effect=lambda key, val: snapshots.append(json.loads(json.dumps(val)))))))
    monkeypatch.setattr(worker, "_ComfyEvents", lambda client: SimpleNamespace(start=lambda: None, close=lambda: None, drain=lambda p: None))
    monkeypatch.setattr(worker, "COMFYUI_DIR", tmp_path)
    source = tmp_path / "output" / "studio" / "source.mp4" if source_in_output else tmp_path / "external.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    def wait(*args):
        source.write_bytes(b"source")
        return source
    monkeypatch.setattr(worker, "_wait_for_video", wait)
    monkeypatch.setattr(worker.subprocess, "run", lambda command, **kwargs: Path(command[-1]).write_bytes(b"encoded MP4"))
    monkeypatch.setattr(worker, "_probe_video", lambda path: {"width": 832, "height": 480, "frames": 124, "has_audio": True})
    assert worker._run_clip(payload)["metadata"]["seed"] == 42
    assert source.exists() is not source_in_output
    assert worker._run_clip({**payload, "trace_id": "take-2"})["video_base64"]
    assert source.exists() is not source_in_output
    assert len(starts) == 1
    assert stopped == []
    assert [e["phase"] for e in snapshots[-1]["events"]] == ["CONTAINER_STARTED", "LOADING_MODEL", "ARTIFACT_READY"]
    monkeypatch.setattr(worker, "_wait_for_video", lambda *args: (_ for _ in ()).throw(RuntimeError("Comfy crash")))
    with pytest.raises(RuntimeError, match="crash"):
        worker._run_clip({**payload, "trace_id": "take-3"})
    assert worker._COMFY_PROCESS is None
    assert worker._COMFY_LOG is None
    assert stopped == [process]
    assert snapshots[-1]["phase"] == "FAILED"
    assert "Private dialogue" not in json.dumps(snapshots)


def test_history_success_without_file_has_bounded_wait(worker, monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "COMFYUI_DIR", tmp_path)
    monkeypatch.setattr(worker, "_http_json", lambda *a, **k: {"p": {"status": {"completed": True}}})
    ticks = iter([0, 0, 0, 0, 61, 61])
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="60 s"):
        worker._wait_for_video("p", "studio/clip", SimpleNamespace(poll=lambda: None), tmp_path / "log")


def test_progress_cancels_blocked_async_dict_put(worker, monkeypatch):
    state = {"entered": False, "cancelled": False, "calls": 0}
    async def blocked(key, value):
        state["entered"] = True
        state["calls"] += 1
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True
    monkeypatch.setattr(worker, "progress_store", SimpleNamespace(put=SimpleNamespace(aio=blocked)))
    monkeypatch.setattr(worker, "H3_PROGRESS_TIMEOUT_SEC", 0.01, raising=False)
    progress = worker._Progress("take-42")
    progress.emit("FAILED", "render failed")
    assert progress.snapshot["phase"] == "FAILED"
    assert state == {"entered": True, "cancelled": True, "calls": 1}
    progress.emit("ARTIFACT_READY", "readable")
    assert not progress.available
    assert state["calls"] == 1


def test_run_clip_enters_render_after_blocked_telemetry_is_cancelled(worker, monkeypatch):
    from calliope.engines.modal_h3 import build_request

    state = {"cancelled": False}
    async def blocked(key, value):
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True
    def render(payload, progress, started):
        assert state["cancelled"]
        return {"video_base64": "done", "metadata": {}}
    monkeypatch.setattr(worker, "progress_store", SimpleNamespace(put=SimpleNamespace(aio=blocked)))
    monkeypatch.setattr(worker, "H3_PROGRESS_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(worker, "_render_clip", render)
    payload = build_request(prompt="dialogue", duration_sec=5, aspect_ratio="16:9", seed=42, trace_id="take-42")
    assert worker._run_clip(payload)["video_base64"] == "done"


@pytest.mark.parametrize("source_in_output", [True, False])
@pytest.mark.parametrize("stage", ["encode", "probe", "dimensions"])
def test_failed_postprocessing_removes_only_owned_source(worker, monkeypatch, tmp_path, source_in_output, stage):
    from calliope.engines.modal_h3 import build_request

    payload = build_request(prompt="dialogue", duration_sec=5, aspect_ratio="16:9", seed=42)
    monkeypatch.setattr(worker, "COMFYUI_DIR", tmp_path)
    source = tmp_path / "output" / "studio" / "source.mp4" if source_in_output else tmp_path / "external.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    monkeypatch.setattr(worker, "_ensure_comfyui", lambda: (SimpleNamespace(poll=lambda: None), tmp_path / "log"))
    monkeypatch.setattr(worker, "_reset_comfyui", lambda: None)
    monkeypatch.setattr(worker, "_http_json", lambda route, body: {"prompt_id": body["prompt_id"]})
    monkeypatch.setattr(worker, "_ComfyEvents", lambda client: SimpleNamespace(start=lambda: None, close=lambda: None, drain=lambda p: None))
    monkeypatch.setattr(worker, "_wait_for_video", lambda *args: source)
    def encode(command, **kwargs):
        if stage == "encode":
            raise RuntimeError("encode failed")
        Path(command[-1]).write_bytes(b"encoded")
    def probe(path):
        if stage == "probe":
            raise RuntimeError("probe failed")
        return {"width": 1, "height": 1, "frames": 1}
    monkeypatch.setattr(worker.subprocess, "run", encode)
    monkeypatch.setattr(worker, "_probe_video", probe)
    with pytest.raises(RuntimeError):
        worker._run_clip(payload)
    assert source.exists() is not source_in_output
