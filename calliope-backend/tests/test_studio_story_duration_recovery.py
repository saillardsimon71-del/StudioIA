import copy
import json

import pytest

from calliope.studio import series_planner
from tests.test_studio_native_contract import realization_plan


def screenplay(plan):
    return {'title': 'Le coffre', 'narrative_outline': plan['narrative_outline'],
            'beats': [{'action': 'Le gardien réagit.', 'dialogue_turns': [
                {'speaker': s['speaker'], 'text': s['dialogue']}]} for s in plan['scenes'][0]['shots']]}


@pytest.mark.asyncio
async def test_duration_repair_that_adds_speech_returns_to_the_writer(monkeypatch):
    original = realization_plan()
    source = screenplay(original)
    expanded = copy.deepcopy(original)
    for index, shot in enumerate(expanded['scenes'][0]['shots']):
        shot.update(duration_sec=10 if index == 5 else 11)
    expanded['scenes'][0]['shots'][0]['dialogue'] = 'The writer adds a new opening.'
    revised = screenplay(expanded)
    revisions, requests = [], []

    async def develop(*args):
        return copy.deepcopy(source), {}

    async def write(context, idea, objective, trace=None, **options):
        revisions.append(options)
        assert options['previous_story'] == source
        assert '65' in options['revision_note']
        return copy.deepcopy(revised)

    async def generate(messages, **options):
        requests.append((copy.deepcopy(messages), options))
        if len(requests) == 1:
            return copy.deepcopy(original)
        if len(requests) == 2:
            invalid = copy.deepcopy(expanded['scenes'][0]['shots'])
            invalid[0]['dialogue'] = original['scenes'][0]['shots'][0]['dialogue']
            extra = copy.deepcopy(invalid[-1])
            extra.update(dialogue='An unauthorized technical addition.', duration_sec=1)
            invalid.append(extra)
            return {'values': {'field_1': invalid}}
        assert options['required_keys'] == ('prompt_contract_version', 'scenes')
        assert json.loads(messages[-1]['content'])['creative_story'] == revised
        return copy.deepcopy(expanded)

    monkeypatch.setattr(series_planner, '_develop_creative_story', develop)
    monkeypatch.setattr(series_planner, '_write_creative_story', write)
    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    plan = await series_planner.plan_episode(
        {'entities': [], 'language': 'fr', 'prompt_contract_version': 2,
         'studio_director': True, 'target_seconds': 65, 'directing_profile': {'cta_placement': 'none'}},
        'Préparatifs dans plusieurs lieux.', '',
    )
    assert len(revisions) == 1 and len(requests) == 3
    assert plan['creative_story'] == revised
    assert sum(s['duration_sec'] for c in plan['scenes'] for s in c['shots']) == 65
    assert all('unauthorized' not in s['dialogue'] for c in plan['scenes'] for s in c['shots'])


@pytest.mark.asyncio
async def test_writer_receives_its_previous_story_and_duration_feedback(monkeypatch):
    story = screenplay(realization_plan())
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        return copy.deepcopy(story)

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    await series_planner._write_creative_story(
        {'entities': [], 'language': 'fr', 'target_seconds': 65}, 'Préparatifs dans les cellules.', '',
        previous_story=story, revision_note='Seulement 44.1 secondes pour une cible de 65.',
    )
    request = json.loads(requests[0][-1]['content'])
    assert request['previous_story'] == story
    assert '44.1' in request['revision_note']
    assert request['idea'] == 'Préparatifs dans les cellules.'


def test_duration_at_the_ten_percent_boundary_accepts_decimal_rounding():
    plan = realization_plan()
    durations = [3.2, 1.3, 1.6, 1.9, 3.7, 3.3, 6.5, 6.0, 2.1, 2.8, 1.6, 2.5, 6.0, 3.8, 1.9, 1.3, 9.0]
    template = plan['scenes'][0]['shots'][0]
    plan['scenes'][0]['shots'] = [copy.deepcopy(template) | {'duration_sec': d} for d in durations]
    normalized = series_planner.normalize_plan(
        plan, {'entities': [], 'target_seconds': 65, 'studio_director': True}
    )
    assert sum(s['duration_sec'] for s in normalized['scenes'][0]['shots']) == pytest.approx(58.5)
