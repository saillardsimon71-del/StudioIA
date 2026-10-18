import copy
import json

import pytest

from calliope.studio import director, series_planner, service
from tests.test_studio_director import contract, prepared, state
from tests.test_studio_midroll import persisted_episode
from tests.test_studio_native_contract import native_plan


@pytest.mark.asyncio
async def test_preparation_repairs_compressed_dialogue_before_saving(client, monkeypatch):
    project = persisted_episode(client, monkeypatch)
    calls = []

    async def generate(messages, **options):
        source = json.loads(messages[-1]['content'])
        calls.append(source)
        requirements = source['requirements']
        value = contract()
        value['duration_sec'] = requirements['duration_sec']
        value['dialogue'] = [line | {'start_sec': 0., 'end_sec': value['duration_sec']}
                             for line in requirements['dialogue']]
        if len(calls) == 1:
            value['dialogue'][0]['end_sec'] = .1
        return {'shots': [value]}

    monkeypatch.setattr(director, 'generate_structured', generate)
    result = await director.prepare(project)
    assert 'parole trop rapide' in calls[1].get('validation_error', '')
    assert len(calls) == len(result['shots']) + 1
    with service.database() as conn:
        director.validate_live(conn, project, result)


@pytest.mark.asyncio
async def test_revision_saves_a_valid_selected_shot_despite_other_bad_timings(client, prepared):
    project, _ = prepared
    before = state(client, project)
    target, unrelated = before['shots'][:2]
    broken = copy.deepcopy(unrelated['contract'])
    broken['dialogue'][0]['end_sec'] = .1
    with service.database() as conn:
        conn.execute('UPDATE studio_director_shots SET contract_json=? WHERE clip_id=?',
                     (json.dumps(broken), unrelated['id']))
        director.apply_contract(conn, unrelated['id'], broken)
        conn.commit()
    before = state(client, project)
    value = copy.deepcopy(target['contract'])
    value['dialogue'][0]['text'] = 'Une autre réponse.'
    result = await director.revise(project, before['storyboard_hash'], [target['id']], '',
                                   contracts=[{'id': target['id'], 'contract': value}])
    assert result['shots'][0]['contract'] == value
    assert result['shots'][1:] == before['shots'][1:]
    with pytest.raises(service.HTTPException, match='parole trop rapide'):
        director.approve(project, result['storyboard_hash'])


@pytest.mark.asyncio
async def test_generated_revision_retries_bad_timing_without_changing_words(client, prepared, monkeypatch):
    project, _ = prepared
    before = state(client, project)
    target = before['shots'][0]
    calls = []

    async def generate(messages, **options):
        calls.append(copy.deepcopy(messages))
        value = copy.deepcopy(target['contract'])
        if len(calls) == 1:
            value['dialogue'][0]['end_sec'] = .1
        elif len(calls) == 2:
            value['dialogue'][0]['text'] = 'Changed words.'
        return {'shots': [{'id': target['id'], 'contract': value}]}

    monkeypatch.setattr(director, 'generate_structured', generate)
    result = await director.revise(project, before['storyboard_hash'], [target['id']],
                                   'Corrige le minutage, garde les paroles.')
    assert len(calls) == 3
    assert 'parole trop rapide' in calls[1][-1]['content']
    assert result['shots'][0]['contract']['dialogue'] == target['contract']['dialogue']


def test_native_planner_reports_all_shots_without_enough_speech_time():
    plan = native_plan()
    for shot in plan['scenes'][0]['shots'][:2]:
        shot['duration_sec'] = 1.
        shot['dialogue'] = 'Cette réplique ne peut pas tenir dans une seconde.'
        shot['dialogue_turns'] = [{'speaker': 'keeper', 'text': shot['dialogue']}]
    with pytest.raises(series_planner._NativeValidationError, match='parole trop rapide') as failed:
        series_planner.normalize_plan(plan, {'entities': []})
    assert ['scenes', 0, 'shots', 0, 'duration_sec'] in failed.value.paths
    assert ['scenes', 0, 'shots', 1, 'duration_sec'] in failed.value.paths
