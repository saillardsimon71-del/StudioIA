import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from calliope.studio import production, service
from calliope.engines import modal_h3
from calliope.queue.manager import queue_manager
from tests.test_studio import new_board, png_bytes


def image_shots(client, monkeypatch):
    monkeypatch.setattr(queue_manager, 'paused', True)
    project_id, board = new_board(client)
    shots = [s for scene in board['scenes'] for s in scene['shots']]
    for shot in shots:
        take = service.store_upload(shot['id'], png_bytes(), 'image')
        service.select_take(take['id'])
    monkeypatch.setattr(service.settings, 'dry_run', False)
    monkeypatch.setattr(production, 'prepare_local_audio', AsyncMock())
    monkeypatch.setattr(production, 'checkpoint', AsyncMock())
    return project_id, [production.load_shot(s['id']) for s in shots]


@pytest.mark.asyncio
async def test_invalid_later_image_fails_before_any_gpu_render(client, monkeypatch):
    project_id, shots = image_shots(client, monkeypatch)
    last = shots[-1]
    with service.database() as conn:
        path = conn.execute('SELECT output_path FROM studio_takes WHERE id=?',
                            (last['selected_image_id'],)).fetchone()[0]
    Path(path).write_bytes(b'not an image')
    started = []

    async def render(*args):
        started.append('gpu')

    monkeypatch.setattr(production, 'render_shot', render)
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    with pytest.raises(ValueError, match='image'):
        await production.render_videos({'id':42, 'project_id':project_id}, shots, lambda:False)
    assert started == []


@pytest.mark.asyncio
async def test_all_static_inputs_and_account_lookup_finish_before_first_render(client, monkeypatch):
    project_id, shots = image_shots(client, monkeypatch)
    events = []

    class Engine:
        def __init__(self, *args):
            pass

        def check_available(self):
            events.append('deployment')
            return {'ok':True}

        def identity(self):
            events.append('account')
            return {'workspace':'test-workspace'}

    original = modal_h3._image_png

    def encode(path):
        events.append('image')
        return original(path)

    async def render(job, shot, stop):
        batch = job.get('video_preparation')
        assert batch, 'Static video preparation is missing'
        assert len(batch['shots']) == len(shots)
        assert batch['identity'] == {'workspace':'test-workspace'}
        for prepared in batch['shots'].values():
            assert prepared['payload']['frames'] > 0
            assert base64.b64decode(prepared['payload']['image_base64']).startswith(b'\x89PNG')
        events.append('gpu')

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', Engine)
    monkeypatch.setattr(modal_h3, '_image_png', encode)
    monkeypatch.setattr(production, 'render_shot', render)
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    await production.render_videos({'id':42, 'project_id':project_id}, shots, lambda:False)
    assert events.count('account') == 1
    assert events.count('image') == len(shots)
    assert max(i for i,e in enumerate(events) if e != 'gpu') < events.index('gpu')


@pytest.mark.asyncio
async def test_pending_continuation_is_prepared_without_requiring_a_previous_video(client, monkeypatch):
    project_id, shots = image_shots(client, monkeypatch)
    with service.database() as conn:
        conn.execute('UPDATE studio_shots SET continue_from_previous=1 WHERE clip_id=?',
                     (shots[-1]['id'],))
        conn.commit()
    from calliope.studio import video_preparation

    class Engine:
        def __init__(self, *args):
            pass

        def check_available(self):
            return {'ok':True}

        def identity(self):
            return {'workspace':'test-workspace'}

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', Engine)
    batch = await video_preparation.prepare_video_batch(shots)
    continuation = batch['shots'][shots[-1]['id']]
    assert not continuation['payload'].get('image_base64')
    assert continuation['inputs']['image_path'] is None
    assert continuation['payload']['prompt']


@pytest.mark.asyncio
async def test_cancellation_during_preparation_starts_no_gpu(client, monkeypatch):
    project_id, shots = image_shots(client, monkeypatch)
    started = []

    async def render(*args):
        started.append('gpu')

    monkeypatch.setattr(production, 'render_shot', render)
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await production.render_videos({'id':42, 'project_id':project_id}, shots, lambda:True)
    assert started == []


@pytest.mark.asyncio
async def test_prepared_images_are_reused_with_each_takes_actual_seed(client, monkeypatch, tmp_path):
    from calliope.studio.video_preparation import prepare_video_batch
    from tests.test_studio import video_bytes

    _, shots = image_shots(client, monkeypatch)
    monkeypatch.setattr(service.settings, 'studio_video_concurrency', 1)
    content = base64.b64encode(video_bytes(tmp_path, audio=True)).decode()
    received = []
    lookups = []

    class Engine:
        def __init__(self, *args):
            pass

        def check_available(self):
            return {'ok':True}

        def identity(self):
            lookups.append('identity')
            return {'workspace':'test-workspace'}

        def spawn(self, payload):
            received.append(payload)
            return f'fc-prepared-{len(received)}'

        def poll(self, call_id):
            return {'video_base64':content, 'metadata':{}}

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', Engine)
    batch = await prepare_video_batch(shots)

    def late_preparation(*args, **kwargs):
        pytest.fail('Static image encoding was repeated during the GPU batch')

    monkeypatch.setattr(modal_h3, '_image_png', late_preparation)
    monkeypatch.setattr(modal_h3, 'build_request', late_preparation)
    for index, shot in enumerate(shots):
        queued = service.enqueue_takes(shot['id'], {'kind':'video', 'request_id':f'prepared-{index}',
                                      'n_takes':1, 'seed':101+index, 'parent_job_id':42})
        job = queue_manager.get_job(queued['jobs'][0]['id']) | {'video_preparation':batch}
        await service.run_studio_job(job, {'studio_take_id':queued['takes'][0]['id'],
                                          'parent_job_id':42}, lambda:False)
    assert [r['seed'] for r in received] == [101, 102]
    assert all(r.get('image_base64') and r.get('trace_id') for r in received)
    assert len({r['trace_id'] for r in received}) == 2
    assert lookups == ['identity']


@pytest.mark.asyncio
async def test_progress_lookup_does_not_hold_back_a_ready_result(client, monkeypatch, tmp_path):
    import threading
    from tests.test_studio import video_bytes

    _, board = new_board(client)
    monkeypatch.setattr(queue_manager, 'paused', True)
    queued = service.enqueue_takes(board['scenes'][0]['shots'][0]['id'],
                                  {'kind':'video','request_id':'slow-progress','n_takes':1})
    job = queue_manager.get_job(queued['jobs'][0]['id'])
    content = base64.b64encode(video_bytes(tmp_path, audio=True)).decode()
    released = threading.Event()
    events = []
    polls = []

    class Engine:
        def __init__(self, *args):
            pass

        def spawn(self, payload):
            return 'fc-slow-progress'

        def poll(self, call_id):
            polls.append(call_id)
            if len(polls) == 1:
                return None
            events.append('result')
            released.set()
            return {'video_base64':content, 'metadata':{}}

        def progress(self, trace_id):
            released.wait(0.3)
            events.append('telemetry')
            return None

    sleep = asyncio.sleep

    async def fast_poll(seconds):
        await sleep(0.01)

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', Engine)
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    monkeypatch.setattr(service.asyncio, 'sleep', fast_poll)
    await service.run_studio_job(job, {'studio_take_id':queued['takes'][0]['id']}, lambda:False)
    assert events[0] == 'result'


def test_prepared_continuation_uses_the_current_trim_and_keeps_the_template(monkeypatch):
    from calliope.studio.video_preparation import prepared_request

    inputs = {'prompt':'A cat listens.', 'duration_sec':3, 'aspect_ratio':'9:16',
              'audio_mode':'native', 'video_profile':'ltx', 'image_path':None}
    prepared = {'inputs':inputs, 'payload':modal_h3.build_request(
        **inputs, seed=0,
    ), 'source_sha256':None}
    extracted = []

    def frame(path, end):
        extracted.append((path, end))
        return png_bytes()

    monkeypatch.setattr(modal_h3, '_last_frame_png', frame)
    request = inputs | {'previous_video_path':'previous.mp4', 'previous_video_time':1.25,
                        'seed':123, 'trace_id':'continuation-trace'}
    payload = prepared_request(request, prepared)
    assert extracted == [(Path('previous.mp4'), 1.25)]
    assert payload['continuity_mode'] == 'last_frame'
    assert payload['seed'] == 123 and payload['trace_id'] == 'continuation-trace'
    assert base64.b64decode(payload['image_base64']) == png_bytes()
    assert prepared['payload']['seed'] == 0
    assert 'image_base64' not in prepared['payload']
    with pytest.raises(ValueError, match='changé'):
        prepared_request(request | {'prompt':'Different dialogue.'}, prepared)


@pytest.mark.asyncio
async def test_idle_control_reuses_the_render_engine_without_new_discovery(monkeypatch):
    from types import SimpleNamespace

    applied = []
    engine = SimpleNamespace(set_idle_window=applied.append)
    monkeypatch.setattr(service.settings, 'dry_run', False)
    monkeypatch.setattr(service.settings, 'studio_video_profile', 'ltx')

    def unnecessary_discovery(*args):
        pytest.fail('The prepared engine should be reused for GPU control')

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', unnecessary_discovery)
    assert await service.video_idle_window(60, engine=engine) is True
    assert applied == [60]


@pytest.mark.asyncio
async def test_independent_render_overlaps_the_previous_local_audio_check(monkeypatch):
    monkeypatch.setattr(service.settings, 'dry_run', True)
    monkeypatch.setattr(service.settings, 'studio_video_concurrency', 1)
    monkeypatch.setattr(production, 'checkpoint', AsyncMock())
    monkeypatch.setattr(production, 'prepare_local_audio', AsyncMock())
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    next_render = asyncio.Event()
    events = []

    async def render(job, shot, stop):
        events.append(f'gpu-{shot["id"]}')
        if job.get('video_ready'):
            job['video_ready']()
        if shot['id'] == 1:
            await asyncio.wait_for(next_render.wait(), timeout=0.5)
            events.append('audio-1-done')
        else:
            next_render.set()

    monkeypatch.setattr(production, 'render_shot', render)
    shots = [{'id':1,'continue_from_previous':False}, {'id':2,'continue_from_previous':False}]
    await production.render_videos({'id':42}, shots, lambda:False)
    assert events.index('gpu-2') < events.index('audio-1-done')


@pytest.mark.asyncio
async def test_continuation_waits_for_the_previous_audio_trim_even_after_gpu_is_ready(monkeypatch):
    monkeypatch.setattr(service.settings, 'dry_run', True)
    monkeypatch.setattr(service.settings, 'studio_video_concurrency', 1)
    monkeypatch.setattr(production, 'checkpoint', AsyncMock())
    monkeypatch.setattr(production, 'prepare_local_audio', AsyncMock())
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    trimmed = False

    async def render(job, shot, stop):
        nonlocal trimmed
        if shot['id'] == 2:
            assert trimmed
        if job.get('video_ready'):
            job['video_ready']()
        if shot['id'] == 1:
            await asyncio.sleep(0.01)
            trimmed = True

    monkeypatch.setattr(production, 'render_shot', render)
    await production.render_videos({'id':42}, [{'id':1,'continue_from_previous':False},
                                   {'id':2,'continue_from_previous':True}], lambda:False)


@pytest.mark.asyncio
async def test_late_audio_failure_collects_the_paid_sibling_and_stops_later_renders(monkeypatch):
    monkeypatch.setattr(service.settings, 'dry_run', True)
    monkeypatch.setattr(service.settings, 'studio_video_concurrency', 1)
    monkeypatch.setattr(production, 'checkpoint', AsyncMock())
    monkeypatch.setattr(production, 'prepare_local_audio', AsyncMock())
    monkeypatch.setattr(service, 'video_idle_window', AsyncMock())
    next_render = asyncio.Event()
    completed = []

    async def render(job, shot, stop):
        if shot['id'] == 1:
            job['video_ready']()
            await next_render.wait()
            raise ValueError('audio check failed')
        assert shot['id'] == 2
        next_render.set()
        await asyncio.sleep(0.01)
        completed.append(shot['id'])

    monkeypatch.setattr(production, 'render_shot', render)
    with pytest.raises(ValueError, match='audio check failed'):
        await asyncio.wait_for(production.render_videos({'id':42}, [
            {'id':1,'continue_from_previous':False}, {'id':2,'continue_from_previous':False},
            {'id':3,'continue_from_previous':False},
        ], lambda:False), timeout=0.5)
    assert completed == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [True, False])
async def test_reused_videos_need_neither_source_images_nor_modal(client, monkeypatch, tmp_path, approved):
    from tests.test_studio import video_bytes

    project_id, shots = image_shots(client, monkeypatch)
    clip = video_bytes(tmp_path, audio=True)
    for shot in shots:
        take = service.store_upload(shot['id'], clip, 'video')
        service.select_take(take['id'])
        with service.database() as conn:
            if approved:
                conn.execute("UPDATE studio_takes SET engine='MiniMax H3' WHERE id=?", (take['id'],))
                conn.execute('UPDATE studio_shots SET approved=1 WHERE clip_id=?', (shot['id'],))
            path = conn.execute('SELECT output_path FROM studio_takes WHERE id=?',
                                (shot['selected_image_id'],)).fetchone()[0]
            conn.commit()
        Path(path).unlink()

    def unavailable(*args, **kwargs):
        pytest.fail('Completed videos must not require Modal or source images')

    monkeypatch.setattr(modal_h3, 'ModalH3Engine', unavailable)
    monkeypatch.setattr(modal_h3, 'build_request', unavailable)
    monkeypatch.setattr(production, 'assess_take', AsyncMock(return_value=None))
    await production.render_videos({'id':42, 'project_id':project_id}, shots, lambda:False)
    assert all(production.load_shot(shot['id'])['approved'] for shot in shots)


@pytest.mark.asyncio
async def test_render_releases_the_gpu_slot_before_its_real_local_assessment(client, monkeypatch, tmp_path):
    from tests.test_studio import video_bytes

    monkeypatch.setattr(queue_manager, 'paused', True)
    _, board = new_board(client)
    shot_id = board['scenes'][0]['shots'][0]['id']
    take = service.store_upload(shot_id, video_bytes(tmp_path, audio=True), 'video')
    shot = service.select_take(take['id'])
    events = []

    async def assess(current):
        assert events == ['gpu-free']
        assert current['selected_video_id'] == take['id']
        events.append('audio')
        return None

    monkeypatch.setattr(production, 'assess_take', assess)
    await production.render_shot({'id':42, 'video_ready':lambda:events.append('gpu-free')},
                                 shot, lambda:False)
    assert events == ['gpu-free', 'audio']
    assert production.load_shot(shot_id)['approved']
