import copy
import io
import json
import zipfile

import pytest

from calliope.studio import service
from tests.test_studio import png_bytes
from tests.test_studio_midroll import persisted_episode


def contract():
    return {
        'summary': 'Le gardien répond à la radio.',
        'casting': ['keeper'], 'location': 'tower',
        'first_frame': 'Keeper left of frame in the tower, looking right at the radio.',
        'positions': {'keeper': 'left, facing right'},
        'eyelines': {'keeper': 'towards the radio on the right'},
        'framing': 'medium shot at eye level', 'camera': 'locked camera',
        'action': 'The keeper answers the radio.',
        'initial_state': {'keeper': 'standing left, radio off'},
        'final_state': {'keeper': 'standing left, radio on'},
        'chronology': [{'at_sec': 0, 'action': 'Keeper turns on the radio.'}],
        'dialogue': [{'speaker': 'keeper', 'text': 'Line 0.', 'start_sec': 1, 'end_sec': 3}],
        'sound': 'quiet room tone', 'duration_sec': 4,
        'references': ['keeper', 'tower'],
        'continuity': ['Keep yellow coat and the radio on the right.'],
        'qc': ['Check exact speech, radio position and no stray text.'],
    }


@pytest.fixture
def prepared(client, monkeypatch):
    from calliope.studio import director
    project = persisted_episode(client, monkeypatch)
    shots = service.board(project)['scenes'][0]['shots']
    contracts = []
    for shot in shots:
        value = contract()
        value['duration_sec'] = shot['duration_sec']
        value['dialogue'] = [{'speaker': (shot.get('dialogue_turns') or [{}])[0].get('speaker', 'keeper'),
                              'text': shot['dialogue'], 'start_sec': 0, 'end_sec': shot['duration_sec']}]
        contracts.append(value)
    director.initialize(project, contracts)
    return project, shots


def state(client, project):
    result = client.get(f'/api/studio/projects/{project}/director')
    assert result.status_code == 200, result.text
    return result.json()


def approve(client, project):
    current = state(client, project)
    result = client.post(f'/api/studio/projects/{project}/director/approve',
                         json={'expected_hash': current['storyboard_hash']})
    assert result.status_code == 200, result.text
    return result.json()


def test_contract_rejects_unknown_speaker_timing_and_blank(client, prepared):
    from calliope.studio.director import validate_contract
    project, shots = prepared
    with service.database() as conn:
        context = service.shot_context(conn, shots[0]['id'])
    for change in ({'casting': ['stranger']}, {'duration_sec': float('nan')},
                   {'camera': ' '}, {'dialogue': [{'speaker': 'stranger', 'text': 'Hi',
                                                  'start_sec': 0, 'end_sec': 1}]}):
        with pytest.raises(ValueError):
            validate_contract(contract() | change, context['entities'])


def test_resume_rejects_cancelled_job_without_leaving_false_pending_state(client, prepared, monkeypatch):
    from calliope.studio import director, series

    project, _ = prepared
    with service.database() as conn:
        job_id = conn.execute('SELECT job_id FROM studio_productions WHERE project_id=?',
                              (project,)).fetchone()[0]
        conn.execute("UPDATE jobs SET status='cancelled',error='Manual diagnostic' WHERE id=?", (job_id,))
        conn.execute("UPDATE studio_productions SET status='paused',stage='review' WHERE project_id=?",
                     (project,))
        conn.commit()
    monkeypatch.setattr(series, 'require_services', lambda **kwargs: None)
    monkeypatch.setattr(director, 'preflight', lambda project_id: {'ok': True})
    result = client.post(f'/api/studio/projects/{project}/production/resume')
    assert result.status_code == 409
    assert service.board(project)['production']['status'] == 'paused'
    with service.database() as conn:
        assert conn.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 'cancelled'
        assert not conn.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='pending'",
                                (project,)).fetchone()


def test_storyboard_review_and_images_are_required_before_gpu(client, prepared):
    project, shots = prepared
    current = state(client, project)
    assert current['approved'] is False
    result = client.get(f'/api/studio/projects/{project}/director/preflight').json()
    assert not result['ok']
    assert any('storyboard' in error.lower() for error in result['errors'])
    assert client.get(f'/api/studio/projects/{project}/director/package').status_code == 409
    approve(client, project)
    result = client.get(f'/api/studio/projects/{project}/director/preflight').json()
    assert not result['ok']
    assert any('image' in error.lower() for error in result['errors'])
    generated = client.post(f"/api/studio/shots/{shots[0]['id']}/generate", json={
        'kind': 'image', 'n_takes': 1, 'request_id': 'forbidden-image'})
    assert generated.status_code == 422


def test_package_import_and_review_preserve_takes(client, prepared):
    project, shots = prepared
    approved = approve(client, project)
    result = client.get(f'/api/studio/projects/{project}/director/package')
    assert result.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(result.content))
    manifest = json.loads(archive.read('manifest.json'))
    assert len(manifest['shots']) == len(shots)
    assert manifest['storyboard_hash'] == approved['storyboard_hash']
    assert 'README.txt' in archive.namelist()
    entry = manifest['shots'][0]
    uploaded = client.post(f'/api/studio/projects/{project}/director/images',
        data={'expected_hash': approved['storyboard_hash']},
        files=[('files', (entry['filename'], png_bytes(), 'image/png'))])
    assert uploaded.status_code == 200, uploaded.text
    current = state(client, project)
    assert current['shots'][0]['image']['approved'] is False
    take = current['shots'][0]['image']['take_id']
    reviewed = client.post(f'/api/studio/projects/{project}/director/images/{take}/review',
        json={'expected_hash': current['storyboard_hash'], 'approved': True})
    assert reviewed.status_code == 200, reviewed.text
    assert state(client, project)['shots'][0]['image']['approved'] is True
    assert service.board(project)['scenes'][0]['shots'][0]['takes'][0]['id'] == take


def test_invalid_batch_is_atomic_and_old_package_rejected(client, prepared):
    project, shots = prepared
    current = approve(client, project)
    filename = current['shots'][0]['filename']
    for files in ([('files', (filename, png_bytes(), 'image/png')),
                   ('files', ('unknown.png', png_bytes(), 'image/png'))],
                  [('files', (filename, png_bytes(), 'image/png')),
                   ('files', (filename, b'broken', 'image/png'))]):
        result = client.post(f'/api/studio/projects/{project}/director/images',
            data={'expected_hash': current['storyboard_hash']}, files=files)
        assert result.status_code == 422, result.text
        assert not service.board(project)['scenes'][0]['shots'][0]['takes']
    result = client.post(f'/api/studio/projects/{project}/director/images',
        data={'expected_hash': 'outdated'}, files=[('files', (filename, png_bytes(), 'image/png'))])
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_revision_keeps_unrequested_shots_and_rejects_llm_extra_ids(client, prepared, monkeypatch):
    from calliope.studio import director
    project, shots = prepared
    before = state(client, project)
    target = before['shots'][0]
    value = copy.deepcopy(target['contract'])
    value['camera'] = 'slow push in'
    async def generated(*args, **kwargs):
        return {'shots': [{'id': target['id'], 'contract': value}]}
    monkeypatch.setattr(director, 'generate_structured', generated)
    result = await director.revise(project, before['storyboard_hash'], [target['id']], 'Rapproche la camera.')
    assert result['shots'][0]['contract']['camera'] == 'slow push in'
    assert result['shots'][1:] == before['shots'][1:]
    with pytest.raises(service.HTTPException) as error:
        await director.revise(project, before['storyboard_hash'], [target['id']], 'stale')
    assert error.value.status_code == 409


def test_direct_edits_invalidate_storyboard_review(client, prepared):
    project, shots = prepared
    approve(client, project)
    service.update_shot(project, shots[0]['id'], {'prompt': 'Changed action.'})
    assert state(client, project)['approved'] is False


@pytest.mark.asyncio
async def test_unverified_or_bad_take_never_auto_approves(client, prepared, monkeypatch):
    from calliope.studio import production
    project, shots = prepared
    shot = shots[0] | {'approved': False}
    monkeypatch.setattr(production, 'load_shot', lambda _: shot)
    monkeypatch.setattr(production, 'reusable_video', lambda _: {'id': 1})
    async def assess(_):
        return {'status': 'extra', 'reason': 'Invented speech'}
    monkeypatch.setattr(production, 'assess_take', assess)
    with pytest.raises(production.ProductionPausedError):
        await production.render_shot({'id': 1, 'project_id': project}, shot, lambda: False)
    assert service.board(project)['scenes'][0]['shots'][0]['approved'] is False


def test_preflight_has_no_remote_engine_calls(client, prepared, monkeypatch):
    from calliope.engines.modal_h3 import ModalH3Engine
    project, _ = prepared
    def forbidden(*args, **kwargs):
        pytest.fail('Preflight must not contact Modal')
    monkeypatch.setattr(ModalH3Engine, 'check_available', forbidden)
    result = client.get(f'/api/studio/projects/{project}/director/preflight')
    assert result.status_code == 200
    assert result.json()['cost']['estimated_eur'] is None


def ready_images(client, project):
    current = approve(client, project)
    response = client.post(f'/api/studio/projects/{project}/director/images',
        data={'expected_hash': current['storyboard_hash']},
        files=[('files', (s['filename'], png_bytes(), 'image/png')) for s in current['shots']])
    assert response.status_code == 200, response.text
    for entry in response.json()['shots']:
        result = client.post(f"/api/studio/projects/{project}/director/images/{entry['image']['take_id']}/review",
            json={'expected_hash': current['storyboard_hash'], 'approved': True})
        assert result.status_code == 200, result.text
    result = client.patch(f'/api/studio/projects/{project}/director/budget', json={
        'max_new_takes': 40, 'max_video_seconds': 180., 'max_cost_eur': 2.,
        'estimated_eur_per_video_second': 0.01})
    assert result.status_code == 200, result.text
    return state(client, project)


def test_preflight_budget_and_modified_image_block_secondary_generation(client, prepared):
    project, shots = prepared
    current = ready_images(client, project)
    check = client.get(f'/api/studio/projects/{project}/director/preflight').json()
    assert check['ok'], check
    assert check['cost']['new_takes'] == len(shots)
    assert check['cost']['estimated_eur'] > 0
    image = next(t for t in service.board(project)['scenes'][0]['shots'][0]['takes'] if t['kind'] == 'image')
    with service.database() as conn:
        path = conn.execute('SELECT output_path FROM studio_takes WHERE id=?', (image['id'],)).fetchone()[0]
    from pathlib import Path
    Path(path).write_bytes(png_bytes() + b'changed')
    result = client.post(f"/api/studio/shots/{shots[0]['id']}/generate", json={
        'kind': 'video', 'n_takes': 1, 'request_id': 'changed-image'})
    assert result.status_code == 422
    assert not state(client, project)['shots'][0]['image']['approved']


def test_cost_reservations_block_multiple_takes_and_are_idempotent(client, prepared):
    project, shots = prepared
    ready_images(client, project)
    result = client.patch(f'/api/studio/projects/{project}/director/budget', json={
        'max_new_takes': 1, 'max_video_seconds': 180., 'max_cost_eur': 2.,
        'estimated_eur_per_video_second': 0.01})
    assert result.status_code == 200
    payload = {'kind': 'video', 'n_takes': 2, 'request_id': 'two-takes'}
    result = client.post(f"/api/studio/shots/{shots[0]['id']}/generate", json=payload)
    assert result.status_code == 422, result.text
    with service.database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM studio_director_reservations').fetchone()[0] == 0
    payload.update(n_takes=1, request_id='one-take')
    first = client.post(f"/api/studio/shots/{shots[0]['id']}/generate", json=payload)
    assert first.status_code == 200, first.text
    again = client.post(f"/api/studio/shots/{shots[0]['id']}/generate", json=payload)
    assert again.status_code == 200, again.text
    assert first.json()['takes'][0]['id'] == again.json()['takes'][0]['id']
    payload['request_id'] = 'new-charge'
    assert client.post(f"/api/studio/shots/{shots[1]['id']}/generate", json=payload).status_code == 422


@pytest.mark.asyncio
async def test_plan_to_qc_and_targeted_retake_without_gpu(client, prepared, monkeypatch, tmp_path):
    from calliope.studio import director, production, video_preparation
    from calliope.queue.manager import queue_manager
    from tests.test_studio import video_bytes
    project, shots = prepared
    current = ready_images(client, project)
    content = video_bytes(tmp_path, audio=True)
    calls = []
    async def local_video(job, shot, kind, should_stop, attempt=0):
        assert kind == 'video'
        assert job['video_batch_last'] is True, 'Release the GPU before waiting for human QC'
        calls.append(shot['id'])
        take = service.store_upload(shot['id'], content, 'video')
        return service.select_take(take['id'])
    async def no_engine(*args):
        return None
    async def unknown(_):
        return {'status': 'unknown', 'reason': 'ASR unavailable'}
    monkeypatch.setattr(production, 'generate_take', local_video)
    monkeypatch.setattr(production, 'assess_take', unknown)
    monkeypatch.setattr(production, 'prepare_local_audio', no_engine)
    monkeypatch.setattr(video_preparation, 'prepare_video_batch', no_engine)
    old_job = service.board(project)['production']['job_id']
    job = queue_manager.get_job(old_job)
    payload = json.loads(job['payload_json'])
    payload.pop('prepare_only', None)
    assert await production.run_production(job, payload, lambda: False) == []
    assert calls == [shots[0]['id']], 'Only one take should be rendered before human QC'
    board = service.board(project)
    take_id = board['scenes'][0]['shots'][0]['selected_video_id']
    assert board['scenes'][0]['shots'][0]['approved'] is False
    check = await director.verify_take(project, take_id)
    assert check['audio_label'] == 'non vérifié'
    reviewed = client.post(f'/api/studio/projects/{project}/director/takes/{take_id}/review', json={
        'expected_hash': current['storyboard_hash'], 'approved': True,
        'visual_ok': True, 'continuity_ok': True, 'speakers_ok': True, 'audio_ok': True})
    assert reviewed.status_code == 200, reviewed.text
    assert service.board(project)['scenes'][0]['shots'][0]['approved']
    queue_manager.mark_done(old_job, [])
    retried = client.post(f"/api/studio/projects/{project}/shots/{shots[1]['id']}/retake",
                         json={'request_id': 'lifecycle-retake'})
    assert retried.status_code == 200, retried.text
    assert retried.json()['scenes'][0]['shots'][0]['selected_video_id'] == take_id


def test_bad_audio_cannot_be_approved_through_old_endpoint(client, prepared, tmp_path):
    from tests.test_studio import video_bytes
    project, shots = prepared
    ready_images(client, project)
    take = service.store_upload(shots[0]['id'], video_bytes(tmp_path, audio=True), 'video')
    service.select_take(take['id'])
    with service.database() as conn:
        row = conn.execute('SELECT metadata_json FROM studio_takes WHERE id=?', (take['id'],)).fetchone()
        metadata = json.loads(row[0]) | {'dialogue_qc': {'status': 'extra', 'reason': 'Parasitic words'}}
        conn.execute('UPDATE studio_takes SET metadata_json=? WHERE id=?', (json.dumps(metadata), take['id']))
        conn.commit()
    with pytest.raises(service.HTTPException) as error:
        service.approve_shot(shots[0]['id'], True)
    assert error.value.status_code == 422


def test_secret_is_removed_from_director_payload(monkeypatch):
    from calliope.config import settings
    from calliope.studio.director import scrub
    monkeypatch.setattr(settings, 'llm_api_key', 'test-secret-never-send')
    assert 'test-secret-never-send' not in json.dumps(scrub({'brief': 'test-secret-never-send'}))


@pytest.mark.asyncio
async def test_real_image_adapter_is_disabled_without_calling_provider(monkeypatch):
    from calliope.config import settings
    monkeypatch.setattr(settings, 'dry_run', False)
    with pytest.raises(ValueError, match='manuelles'):
        await service._generate_image({'image_provider': 'codex'})


def test_derived_text_anchor_does_not_invalidate_storyboard(client, prepared):
    project, shots = prepared
    before = approve(client, project)
    with service.database() as conn:
        context = service.shot_context(conn, shots[0]['id'])['shot']
        context['screen_text'] = [{'key': 'score', 'anchor': 'character', 'anchor_binding': {'take_id': 1}}]
        conn.execute('UPDATE studio_shot_context SET context_json=? WHERE clip_id=?',
                     (json.dumps(context), shots[0]['id']))
        conn.commit()
    baseline = state(client, project)['storyboard_hash']
    with service.database() as conn:
        context['screen_text'][0]['anchor_binding'] = {'take_id': 2, 'frame_index': 12}
        conn.execute('UPDATE studio_shot_context SET context_json=? WHERE clip_id=?',
                     (json.dumps(context), shots[0]['id']))
        conn.commit()
    assert state(client, project)['storyboard_hash'] == baseline


def test_preflight_rechecks_old_video_approval_after_contract_change(client, prepared, tmp_path):
    from calliope.studio import director
    from tests.test_studio import video_bytes
    project, shots = prepared
    ready_images(client, project)
    take = service.store_upload(shots[0]['id'], video_bytes(tmp_path, audio=True), 'video')
    service.select_take(take['id'])
    with service.database() as conn:
        conn.execute('UPDATE studio_shots SET approved=1 WHERE clip_id=?', (shots[0]['id'],))
        conn.commit()
    check = director.preflight(project)
    assert not check['ok']
    assert any('QC' in error for error in check['errors'])


def test_replacing_first_frame_discards_old_video_trims(client, prepared):
    project, shots = prepared
    current = ready_images(client, project)
    with service.database() as conn:
        conn.execute('UPDATE studio_shots SET trim_start_sec=1,trim_end_sec=2 WHERE clip_id=?', (shots[0]['id'],))
        conn.commit()
    response = client.post(f'/api/studio/projects/{project}/director/images',
        data={'expected_hash': current['storyboard_hash']},
        files=[('files', (current['shots'][0]['filename'], png_bytes(), 'image/png'))])
    assert response.status_code == 200
    shot = service.board(project)['scenes'][0]['shots'][0]
    assert shot['trim_start_sec'] is None and shot['trim_end_sec'] is None


def test_missing_or_raised_cost_cannot_bypass_queued_job_guard(client, prepared):
    from calliope.studio import director
    project, shots = prepared
    ready_images(client, project)
    queued = service.enqueue_takes(shots[0]['id'], {'kind': 'video', 'n_takes': 1, 'request_id': 'budget-guard'})
    with service.database() as conn:
        row = conn.execute('SELECT budget_json FROM studio_director_projects WHERE project_id=?', (project,)).fetchone()
        budget = json.loads(row[0])
        for rate in (None, 10.):
            conn.execute('UPDATE studio_director_projects SET budget_json=? WHERE project_id=?',
                         (json.dumps(budget | {'estimated_eur_per_video_second': rate}), project))
            with pytest.raises(service.HTTPException):
                director.guard_budget(conn, project)


@pytest.mark.asyncio
async def test_native_director_revision_can_give_a_silent_character_a_line(client, monkeypatch):
    from calliope.studio import director
    from tests.test_studio_native_contract import native_plan
    from calliope.studio.h3_prompt import compile_video_prompt
    plan = native_plan()
    plan['entities'].append({'key': 'visitor', 'kind': 'character', 'name': 'Visitor', 'description': 'A visitor.'})
    plan['render_canon']['characters']['visitor'] = {'name': 'Visitor', 'appearance': 'A blue coat.', 'voice': 'A quiet alto.'}
    project = persisted_episode(client, monkeypatch, plan)
    shots = service.board(project)['scenes'][0]['shots']
    contracts = []
    for shot in shots:
        value = contract()
        value['dialogue'][0]['text'] = shot['dialogue']
        value['initial_state']['case'] = value['final_state']['case'] = 'Closed on the desk.'
        contracts.append(value)
    director.initialize(project, contracts)
    current = director.state(project)
    value = copy.deepcopy(current['shots'][0]['contract'])
    value['casting'].append('visitor')
    value['references'].append('visitor')
    value['positions']['visitor'] = 'right of frame'
    value['eyelines']['visitor'] = 'towards keeper'
    value['initial_state']['visitor'] = value['final_state']['visitor'] = 'standing right'
    value['dialogue'] = [{'speaker': 'visitor', 'text': 'Please listen.', 'start_sec': 2., 'end_sec': 3.}]
    value['chronology'] = [{'at_sec': 2., 'action': 'The visitor raises a hand'}]
    await director.revise(project, current['storyboard_hash'], [shots[0]['id']], '',
        contracts=[{'id': shots[0]['id'], 'contract': value}])
    with service.database() as conn:
        context = service.shot_context(conn, shots[0]['id'])
        assert context['speaker_ids']['keeper'] == 'S1'
        assert context['speaker_ids']['visitor'] == 'S2'
        for profile in ('fast', 'ltx'):
            prompt = compile_video_prompt(service.public_shot(conn, shots[0]['id']), context, True, profile)
            assert 'Please listen.' in prompt
            assert 'At 2 seconds' in prompt
            assert 'between 2 and 3 seconds' in prompt


def test_director_episode_options_do_not_change_series_canon(client, monkeypatch):
    from calliope.studio import series
    from calliope.queue.manager import queue_manager
    monkeypatch.setattr(queue_manager, 'paused', True)
    monkeypatch.setattr(series, 'require_services', lambda **kwargs: None)
    item = client.post('/api/studio/series', json={'title': 'Options', 'concept': 'A tower.'}).json()
    payload = {'idea': 'A call.', 'request_id': 'options', 'studio_director': True,
               'prepare_only': False, 'target_seconds': 25, 'tone': 'Comique'}
    response = client.post(f"/api/studio/series/{item['id']}/episodes", json=payload)
    assert response.status_code == 200, response.text
    project = response.json()['project_id']
    with service.database() as conn:
        frozen = json.loads(conn.execute('SELECT context_json FROM studio_episodes WHERE project_id=?', (project,)).fetchone()[0])
    assert frozen['target_seconds'] == 25
    assert frozen['bible']['tone'] == 'Comique'
    assert frozen['prepare_only'] is True
    assert frozen['image_mode'] == 'manual_chatgpt'
    assert client.get(f"/api/studio/series/{item['id']}").json()['bible']['tone'] != 'Comique'
    assert client.post(f"/api/studio/series/{item['id']}/episodes", json=payload).json()['project_id'] == project
    assert client.post(f"/api/studio/series/{item['id']}/episodes", json=payload | {'tone': 'Triste'}).status_code == 409


@pytest.mark.asyncio
async def test_malformed_or_out_of_scope_revision_is_atomic(client, prepared, monkeypatch):
    from calliope.studio import director
    project, shots = prepared
    before = director.state(project)
    for answer in ({'shots': ['bad']}, {'shots': [{'id': shots[1]['id'], 'contract': contract()}]}):
        async def generated(*args, **kwargs):
            return answer
        monkeypatch.setattr(director, 'generate_structured', generated)
        with pytest.raises(service.HTTPException) as error:
            await director.revise(project, before['storyboard_hash'], [shots[0]['id']], 'Rapproche la camera.')
        assert error.value.status_code == 422
        assert director.state(project) == before


def test_package_contains_available_reference_images(client, prepared):
    project, _ = prepared
    board = service.board(project)
    series_id = board['episode']['series_id']
    entity = client.get(f'/api/studio/series/{series_id}').json()['entities'][0]
    result = client.post(f"/api/studio/series/{series_id}/entities/{entity['id']}/reference",
                         files={'file': ('reference.png', png_bytes(), 'image/png')})
    assert result.status_code == 200, result.text
    approve(client, project)
    archive = zipfile.ZipFile(io.BytesIO(client.get(f'/api/studio/projects/{project}/director/package').content))
    manifest = json.loads(archive.read('manifest.json'))
    refs = [ref for ref in manifest['shots'][0]['references'] if ref['available']]
    assert len(refs) == 1
    assert archive.read(refs[0]['file']).startswith(b'\x89PNG')


def test_migration_is_additive_and_repeatable(client, prepared):
    from calliope.config import settings
    from calliope.studio.schema import migrate_studio
    project, _ = prepared
    before = state(client, project)
    migrate_studio(settings.db_path)
    migrate_studio(settings.db_path)
    assert state(client, project) == before


@pytest.mark.asyncio
async def test_preparation_batches_contracts_and_resumes_validated_drafts(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    before = service.board(project)
    calls = []
    interrupted = False

    async def realize(messages, **kwargs):
        nonlocal interrupted
        source = json.loads(messages[-1]['content'])
        if len(source['shots']) != 1:
            raise ValueError('No JSON object found in LLM reply (len=54451)')
        shot = source['shots'][0]
        calls.append(shot['id'])
        if len(calls) == 2:
            interrupted = True
            raise RuntimeError('Reply truncated (finish_reason=length)')
        c = contract()
        c['duration_sec'] = shot['duration_sec']
        c['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                          'start_sec': 0., 'end_sec': shot['duration_sec']}]
        return {'shots': [c]}

    monkeypatch.setattr(director, 'generate_structured', realize)
    with pytest.raises(service.HTTPException) as failure:
        await director.prepare(project)
    assert interrupted
    assert 'finish_reason=length' in failure.value.detail
    assert not director.state(project)['enabled']
    assert service.board(project)['scenes'] == before['scenes']
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='failed',error='Old JSON failure' WHERE project_id=?", (project,))
        conn.commit()
    result = await director.prepare(project)
    ids = [s['id'] for scene in before['scenes'] for s in scene['shots']]
    assert calls == ids[:2] + ids[1:]
    assert result['enabled'] and not result['approved']
    assert [s['id'] for s in result['shots']] == ids
    for entry, shot in zip(result['shots'], [s for scene in before['scenes'] for s in scene['shots']]):
        assert entry['contract']['dialogue'][0]['text'] == shot['dialogue']
        assert entry['contract']['duration_sec'] == shot['duration_sec']
    board = service.board(project)
    assert board['production']['status'] == 'paused'
    assert board['production']['stage'] == 'review'
    assert board['production']['error'] is None
    assert all(not s['takes'] for scene in board['scenes'] for s in scene['shots'])


@pytest.mark.asyncio
async def test_preparation_rejects_changed_dialogue_before_applying_contracts(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    before = service.board(project)

    async def wrong_dialogue(*args, **kwargs):
        return {'shots': [contract() | {'dialogue': [
            {'speaker': 'keeper', 'text': 'Invented line.', 'start_sec': 0., 'end_sec': 3.},
        ]}]}

    monkeypatch.setattr(director, 'generate_structured', wrong_dialogue)
    with pytest.raises(service.HTTPException) as failure:
        await director.prepare(project)
    assert failure.value.status_code == 422
    assert not director.state(project)['enabled']
    assert service.board(project)['scenes'] == before['scenes']


@pytest.mark.asyncio
async def test_preparation_repairs_invalid_positions_for_only_the_affected_shot(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    before = service.board(project)
    shots = [s for scene in before['scenes'] for s in scene['shots']]
    requests = []

    async def realize(messages, **kwargs):
        source = json.loads(messages[-1]['content'])
        shot = source['shots'][0]
        requests.append(source)
        value = contract()
        value['duration_sec'] = shot['duration_sec']
        value['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                              'start_sec': 0., 'end_sec': shot['duration_sec']}]
        if len(requests) == 1:
            value['positions'] = {'radio': 'left, facing right'}
            value['eyelines'] = {}
        return {'shots': [value]}

    monkeypatch.setattr(director, 'generate_structured', realize)
    result = await director.prepare(project)
    assert [r['shots'][0]['id'] for r in requests] == [shots[0]['id']] + [s['id'] for s in shots]
    assert requests[1]['invalid_contract']['positions'] == {'radio': 'left, facing right'}
    assert 'positions' in requests[1]['validation_error']
    assert 'keeper' in requests[1]['validation_error']
    assert requests[1]['requirements']['casting'] == ['keeper']
    assert requests[1]['requirements']['dialogue'] == [{'speaker': 'keeper', 'text': shots[0]['dialogue']}]
    assert result['enabled'] and not result['approved']
    for entry in result['shots']:
        assert set(entry['contract']['positions']) == set(entry['contract']['casting'])
        assert set(entry['contract']['eyelines']) == set(entry['contract']['casting'])


@pytest.mark.asyncio
async def test_preparation_reuses_a_valid_unwrapped_provider_contract_without_regenerating(client, monkeypatch):
    from calliope.studio import director
    import jsonschema

    project = persisted_episode(client, monkeypatch)
    original = service.board(project)
    calls = []

    async def provider(messages, **options):
        source = json.loads(messages[-1]['content'])
        shot = source['shots'][0]
        schema = json.loads(messages[0]['content'].split('Schema: ', 1)[1])
        value = contract()
        value['duration_sec'] = shot['duration_sec']
        value['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                              'start_sec': 0., 'end_sec': shot['duration_sec']}]
        jsonschema.validate({'shots': [value]}, schema)
        assert 'shots' not in options.get('required_keys', ())
        assert options['retry_invalid'] is False
        calls.append(shot['id'])
        return value

    monkeypatch.setattr(director, 'generate_structured', provider)
    result = await director.prepare(project)
    expected_ids = [s['id'] for scene in original['scenes'] for s in scene['shots']]
    assert calls == expected_ids
    assert result['enabled'] and not result['approved']
    assert [s['id'] for s in result['shots']] == expected_ids


@pytest.mark.asyncio
async def test_preparation_never_accepts_an_unwrapped_fragment(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    calls = []

    async def fragment(*args, **kwargs):
        calls.append(1)
        return {'summary': 'An incomplete nested fragment.', 'casting': ['keeper']}

    monkeypatch.setattr(director, 'generate_structured', fragment)
    with pytest.raises(service.HTTPException):
        await director.prepare(project)
    assert len(calls) == 3
    assert not director.state(project)['enabled']


@pytest.mark.asyncio
async def test_preparation_bounds_semantic_repairs_and_never_invents_missing_positions(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    before = service.board(project)
    requests = []

    async def invalid(messages, **kwargs):
        source = json.loads(messages[-1]['content'])
        requests.append(source)
        value = contract()
        value['positions'] = {}
        return {'shots': [value]}

    monkeypatch.setattr(director, 'generate_structured', invalid)
    with pytest.raises(service.HTTPException) as failure:
        await director.prepare(project)
    assert failure.value.status_code == 422
    assert len(requests) == 3
    assert 'positions' in failure.value.detail
    assert not director.state(project)['enabled']
    assert service.board(project)['scenes'] == before['scenes']
    with service.database() as conn:
        assert not conn.execute('SELECT 1 FROM studio_director_drafts').fetchone()


@pytest.mark.asyncio
async def test_preparation_rejects_a_known_but_wrong_location(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    with service.database() as conn:
        series_id = conn.execute('SELECT series_id FROM studio_episodes WHERE project_id=?', (project,)).fetchone()[0]
        conn.execute("INSERT INTO studio_entities(series_id,entity_key,kind,name,description) "
                     "VALUES(?,'other-room','location','Other room','Another room.')", (series_id,))
        conn.commit()

    async def wrong_location(messages, **kwargs):
        shot = json.loads(messages[-1]['content'])['shots'][0]
        value = contract() | {'location': 'other-room', 'references': ['keeper', 'other-room']}
        value['duration_sec'] = shot['duration_sec']
        value['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                              'start_sec': 0., 'end_sec': shot['duration_sec']}]
        return {'shots': [value]}

    monkeypatch.setattr(director, 'generate_structured', wrong_location)
    with pytest.raises(service.HTTPException) as failure:
        await director.prepare(project)
    assert failure.value.status_code == 422
    assert 'location' in failure.value.detail
    assert not director.state(project)['enabled']


@pytest.mark.parametrize('change', ['shot', 'canon'])
@pytest.mark.asyncio
async def test_preparation_does_not_reuse_drafts_after_input_changes(client, monkeypatch, change):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    calls = []

    async def realize(messages, **kwargs):
        shot = json.loads(messages[-1]['content'])['shots'][0]
        calls.append(shot['id'])
        if len(calls) == 2:
            raise RuntimeError('Interrupted')
        value = contract()
        value['duration_sec'] = shot['duration_sec']
        value['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                              'start_sec': 0., 'end_sec': shot['duration_sec']}]
        return {'shots': [value]}

    monkeypatch.setattr(director, 'generate_structured', realize)
    with pytest.raises(service.HTTPException):
        await director.prepare(project)
    first = calls[0]
    if change == 'shot':
        service.update_shot(project, first, {'dialogue': 'A revised line.'})
    else:
        with service.database() as conn:
            conn.execute("UPDATE studio_entities SET description='A revised identity.' WHERE series_id="
                         "(SELECT series_id FROM studio_episodes WHERE project_id=?)", (project,))
            conn.commit()
    result = await director.prepare(project)
    assert calls.count(first) == 2
    assert result['enabled'] and not result['approved']
    if change == 'shot':
        assert result['shots'][0]['contract']['dialogue'][0]['text'] == 'A revised line.'


@pytest.mark.asyncio
async def test_preparation_does_not_overwrite_an_edit_made_during_the_request(client, monkeypatch):
    from calliope.studio import director

    project = persisted_episode(client, monkeypatch)
    first = service.board(project)['scenes'][0]['shots'][0]['id']

    async def realize(messages, **kwargs):
        shot = json.loads(messages[-1]['content'])['shots'][0]
        if shot['id'] == first:
            service.update_shot(project, first, {'dialogue': 'Concurrent edit.'})
        value = contract()
        value['duration_sec'] = shot['duration_sec']
        value['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                              'start_sec': 0., 'end_sec': shot['duration_sec']}]
        return {'shots': [value]}

    monkeypatch.setattr(director, 'generate_structured', realize)
    with pytest.raises(service.HTTPException) as failure:
        await director.prepare(project)
    assert failure.value.status_code == 409
    assert not director.state(project)['enabled']
    assert service.board(project)['scenes'][0]['shots'][0]['dialogue'] == 'Concurrent edit.'


@pytest.mark.asyncio
async def test_new_episode_writes_contracts_then_pauses_without_any_media(client, monkeypatch):
    from calliope.config import settings
    from calliope.queue.manager import queue_manager
    from calliope.studio import director, production, series
    from tests.test_studio_midroll import insert, story
    monkeypatch.setattr(queue_manager, 'paused', True)
    monkeypatch.setattr(settings, 'dry_run', False)
    monkeypatch.setattr(series, 'require_services', lambda **kwargs: None)
    item = client.post('/api/studio/series', json={'title': 'Director', 'concept': 'A call at a lighthouse.'}).json()
    created = client.post(f"/api/studio/series/{item['id']}/episodes", json={
        'idea': 'A warning.', 'request_id': 'director-new', 'studio_director': True}).json()
    plan = insert(story())
    async def write(*args, **kwargs):
        return plan
    async def realize(messages, **kwargs):
        source = json.loads(messages[-1]['content'])
        assert 'Studio Director' in messages[0]['content']
        result = []
        for shot in source['shots']:
            c = contract()
            c['duration_sec'] = shot['duration_sec']
            c['dialogue'] = [{'speaker': 'keeper', 'text': shot['dialogue'],
                              'start_sec': 0., 'end_sec': shot['duration_sec']}]
            result.append(c)
        return {'shots': result}
    monkeypatch.setattr(production, 'plan_episode', write)
    monkeypatch.setattr(director, 'generate_structured', realize)
    job = queue_manager.get_job(created['job_id'])
    assert await production.run_production(job, json.loads(job['payload_json']), lambda: False) == []
    current = director.state(created['project_id'])
    assert current['enabled'] and not current['approved']
    board = service.board(created['project_id'])
    assert board['production']['status'] == 'paused'
    assert board['production']['stage'] == 'review'
    assert all(not shot['takes'] for scene in board['scenes'] for shot in scene['shots'])


@pytest.mark.asyncio
async def test_revised_events_replace_obsolete_episode_memory(client, prepared):
    from calliope.studio import director
    project, shots = prepared
    before = director.state(project)
    value = copy.deepcopy(before['shots'][0]['contract'])
    value['summary'] = 'Le gardien éteint définitivement la radio.'
    value['action'] = 'The keeper switches the radio off permanently.'
    value['final_state']['keeper'] = 'standing left, radio off permanently'
    await director.revise(project, before['storyboard_hash'], [shots[0]['id']], '',
        contracts=[{'id': shots[0]['id'], 'contract': value}])
    with service.database() as conn:
        episode = conn.execute('SELECT summary,events_json,ongoing_plot FROM studio_episodes WHERE project_id=?', (project,)).fetchone()
    assert value['summary'] in episode['summary']
    assert value['summary'] in json.loads(episode['events_json'])
    assert 'The keeper hears a warning.' not in episode['summary']
    assert episode['ongoing_plot'] == ''


def test_legacy_structure_editor_cannot_create_uncontracted_director_shots(client, prepared):
    project, shots = prepared
    before = state(client, project)
    scene_id = shots[0]['scene_id']
    assert client.post(f'/api/studio/projects/{project}/scenes', json={'heading': 'New room'}).status_code == 422
    assert client.post(f'/api/studio/projects/{project}/scenes/{scene_id}/shots',
                       json={'description': 'New shot', 'prompt': 'A room.'}).status_code == 422
    assert client.delete(f"/api/studio/projects/{project}/shots/{shots[0]['id']}").status_code == 422
    assert state(client, project) == before
