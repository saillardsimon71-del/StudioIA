import asyncio
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, Mock

@pytest.fixture
def worker(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    policy = asyncio.get_event_loop_policy()
    try:
        return importlib.import_module('deploy.modal_h3')
    finally:
        asyncio.set_event_loop_policy(policy)


def packet_hashes(path, stream):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', stream, '-show_packets',
         '-show_data_hash', 'sha256', '-show_entries', 'packet=data_hash',
         '-of', 'json', str(path)], capture_output=True, check=True,
    )
    return [packet['data_hash'] for packet in json.loads(result.stdout)['packets']]


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_compatible_native_video_preserves_compressed_video_and_audio(worker, tmp_path):
    assert hasattr(worker, '_prepare_video'), 'Compatible ComfyUI outputs should avoid another lossy encode'
    source, target = tmp_path / 'source.mp4', tmp_path / 'target.mp4'
    subprocess.run(
        ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'color=c=green:s=64x96:r=24:d=0.5', '-f', 'lavfi', '-i',
         'sine=frequency=440:duration=0.5', '-c:v', 'libx264', '-pix_fmt',
         'yuv420p', '-c:a', 'aac', '-shortest', str(source)], check=True,
    )
    method = worker._prepare_video(source, target, 'native')
    assert method == 'stream_copy'
    assert packet_hashes(source, 'v:0') == packet_hashes(target, 'v:0')
    assert packet_hashes(source, 'a:0') == packet_hashes(target, 'a:0')
    info = worker._probe_video(target)
    assert (info['width'], info['height'], info['frames']) == (64, 96, 12)
    assert info['has_audio']


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
def test_incompatible_video_is_converted_and_silent_mode_drops_audio(worker, tmp_path):
    assert hasattr(worker, '_prepare_video'), 'A compatible-output fast path must retain a format fallback'
    source, target = tmp_path / 'source.mkv', tmp_path / 'target.mp4'
    subprocess.run(
        ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
         'color=c=blue:s=64x96:r=24:d=0.5', '-f', 'lavfi', '-i',
         'sine=frequency=440:duration=0.5', '-c:v', 'ffv1', '-c:a',
         'pcm_s16le', '-shortest', str(source)], check=True,
    )
    assert worker._prepare_video(source, target, 'silent') == 'transcode'
    info = worker._probe_video(target)
    assert info['video_codec'] == 'h264'
    assert info['pixel_format'] == 'yuv420p'
    assert not info['has_audio']
    assert info['frames'] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_batch_retains_loaded_models_until_last_take_and_releases_on_failure(monkeypatch, failure):
    from calliope.studio import production
    from calliope.studio import video_preparation

    events = []

    async def idle(seconds, **kwargs):
        events.append(seconds)

    async def render(job, shot, stop):
        events.append(shot['id'])
        if failure:
            raise ValueError('render failed')

    monkeypatch.setattr(production.service, 'video_idle_window', idle, raising=False)
    monkeypatch.setattr(production, 'prepare_local_audio', AsyncMock(), raising=False)
    monkeypatch.setattr(production, 'checkpoint', AsyncMock())
    monkeypatch.setattr(production, 'render_shot', render)
    monkeypatch.setattr(video_preparation, 'prepare_video_batch', AsyncMock(return_value={
        'engine':object(), 'modal_app':'test-app', 'modal_function':'render_clip',
    }))
    shots = [{'id': 'first', 'continue_from_previous': False},
             {'id': 'last', 'continue_from_previous': True}]
    if failure:
        with pytest.raises(ValueError, match='render failed'):
            await production.render_videos({'id': 42}, shots, lambda: False)
        assert events == [60, 'first', 2]
    else:
        await production.render_videos({'id': 42}, shots, lambda: False)
        assert events == [60, 'first', 'last', 2]


@pytest.mark.asyncio
async def test_gpu_control_failure_does_not_fail_an_existing_render(client, monkeypatch):
    from calliope.studio import service
    from calliope.engines.modal_h3 import ModalH3Engine

    monkeypatch.setattr(service.settings, 'dry_run', False)
    monkeypatch.setattr(service.settings, 'studio_video_profile', 'ltx')
    control = Mock(side_effect=RuntimeError('control unavailable'))
    monkeypatch.setattr(ModalH3Engine, 'set_idle_window', control, raising=False)
    await service.video_idle_window(60)
    control.assert_called_once_with(60)


def test_local_asr_warms_without_transcribing_or_using_a_gpu(tmp_path, monkeypatch):
    from calliope.studio import transcription
    import sys
    from types import SimpleNamespace

    checkpoint = tmp_path / 'asr.pt'
    checkpoint.write_bytes(b'local checkpoint')
    model = object()
    load = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, 'whisper', SimpleNamespace(load_model=load))
    monkeypatch.setattr(transcription, '_model', None)
    monkeypatch.setattr(transcription, '_checkpoint', None)
    transcription.warm_transcriber(checkpoint)
    transcription.warm_transcriber(checkpoint)
    load.assert_called_once_with(str(checkpoint.resolve()), device='cpu')


@pytest.mark.asyncio
async def test_serial_batch_does_not_render_the_independent_cta_before_its_continuations(monkeypatch):
    from calliope.studio import production

    monkeypatch.setattr(production.settings, 'studio_video_concurrency', 1)
    monkeypatch.setattr(production, 'checkpoint', AsyncMock())
    monkeypatch.setattr(production, 'prepare_local_audio', AsyncMock())
    monkeypatch.setattr(production.service, 'video_idle_window', AsyncMock())
    order = []

    async def render(job, shot, stop):
        await asyncio.sleep(0.01)
        order.append((shot['id'], job['video_batch_last']))

    monkeypatch.setattr(production, 'render_shot', render)
    shots = [{'id':1,'continue_from_previous':False},
             {'id':2,'continue_from_previous':True},
             {'id':3,'continue_from_previous':False,'purpose':'midroll'}]
    await production.render_videos({'id':42}, shots, lambda:False)
    assert order == [(1,False),(2,False),(3,True)]


@pytest.mark.asyncio
async def test_release_keeps_gpu_warm_for_another_queued_take(client, monkeypatch):
    from calliope.studio import service
    from calliope.queue.manager import queue_manager
    from calliope.engines.modal_h3 import ModalH3Engine
    from tests.test_studio import new_board

    monkeypatch.setattr(queue_manager, 'paused', True)
    monkeypatch.setattr(service.settings, 'studio_video_profile', 'ltx')
    _, board = new_board(client)
    shot_id = board['scenes'][0]['shots'][0]['id']
    queued = service.enqueue_takes(shot_id, {'kind':'video', 'request_id':'cost-queue', 'n_takes':2})
    monkeypatch.setattr(service.settings, 'dry_run', False)
    control = Mock()
    monkeypatch.setattr(ModalH3Engine, 'set_idle_window', control)
    await service.video_idle_window(2, job_id=queued['jobs'][0]['id'])
    control.assert_not_called()


@pytest.mark.asyncio
async def test_standalone_result_releases_gpu_before_local_media_validation(client, monkeypatch, tmp_path):
    import base64
    from calliope.studio import service, export
    from calliope.queue.manager import queue_manager
    from calliope.engines import modal_h3
    from tests.test_studio import new_board, video_bytes

    monkeypatch.setattr(queue_manager, 'paused', True)
    _, board = new_board(client)
    queued = service.enqueue_takes(board['scenes'][0]['shots'][0]['id'],
                                   {'kind':'video','request_id':'cost-single','n_takes':1})
    job = queue_manager.get_job(queued['jobs'][0]['id'])
    content = base64.b64encode(video_bytes(tmp_path, audio=True)).decode()
    events = []

    class Engine:
        def __init__(self, *args):
            pass

        def spawn(self, payload):
            return 'fc-cost-test'

        def poll(self, call_id):
            events.append('result')
            return {'video_base64':content,'metadata':{}}

        def progress(self, trace_id):
            pytest.fail('A ready result should not wait for telemetry')

    async def idle(seconds, **kwargs):
        events.append(seconds)

    inspect = export.inspect_media

    def check(path):
        assert events[:3] == [60, 'result', 2]
        return inspect(path)

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', Engine)
    monkeypatch.setattr(service, 'video_idle_window', idle)
    monkeypatch.setattr(export, 'inspect_media', check)
    await service.run_studio_job(job, {'studio_take_id':queued['takes'][0]['id']}, lambda:False)
