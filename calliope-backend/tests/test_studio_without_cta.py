import copy
import json

import pytest

from calliope.studio import production, series_planner, service
from tests.test_studio_midroll import persisted_episode, story

CONTEXT = {"entities": [], "language": "fr", "directing_profile": {"cta_placement": "none"}}


def test_new_series_has_no_cta(client):
    response = client.post('/api/studio/series', json={"title": "Prison", "concept": "Une confrontation."})
    assert response.status_code == 200, response.text
    assert response.json()["directing_profile"]["cta_placement"] == "none"
    changed = client.patch(f"/api/studio/series/{response.json()['id']}",
                           json={'directing_profile': {'preset': 'fast_dialogue_social_fiction'}})
    assert changed.status_code == 200, changed.text
    assert changed.json()['directing_profile']['cta_placement'] == 'none'


def test_no_cta_reserves_no_time_and_preserves_story_and_sound_cues():
    plan = story()
    expected = copy.deepcopy(plan)
    assert series_planner._midroll_spec(CONTEXT)["duration_sec"] == 0
    assert series_planner.insert_midroll(plan, CONTEXT) == expected


def test_no_cta_removes_promotion_and_restores_sound_cue_positions():
    plan = story()
    expected = copy.deepcopy(plan)
    legacy = series_planner.insert_midroll(plan, CONTEXT | {"directing_profile": {"cta_placement": "middle"}})
    assert any(s.get('purpose') == 'midroll' for c in legacy['scenes'] for s in c['shots'])
    assert series_planner.insert_midroll(legacy, CONTEXT) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True])
async def test_65_seconds_are_entirely_given_to_the_screenwriter(monkeypatch, native):
    from tests.test_studio_native_contract import native_plan

    planned = native_plan() if native else story()
    for index, shot in enumerate(planned['scenes'][0]['shots']):
        shot['duration_sec'] = 10 if index == 5 else 11
        shot['image_prompt'] = 'Keeper in the tower.'
        if native:
            shot['state_changes'] = []
    requests = []

    async def generate(messages, **options):
        requests.append(messages)
        return copy.deepcopy(planned)

    async def develop(context, *args):
        assert context['target_seconds'] == 65
        return {'title': 'Le coffre', 'narrative_outline': planned['narrative_outline'],
                'beats': [{'action': 'Le gardien réagit.',
                           'dialogue_turns': [{'speaker': s['speaker'], 'text': s['dialogue']}]}
                          for s in planned['scenes'][0]['shots']]}, {}

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    monkeypatch.setattr(series_planner, '_develop_creative_story', develop)
    context = CONTEXT | {'target_seconds': 65}
    if native:
        context['prompt_contract_version'] = 2
    result = await series_planner.plan_episode(context, 'Une dispute.', '')
    request = json.loads(requests[0][-1]['content'])
    assert request['target_seconds'] == request['series']['target_seconds'] == 65
    assert 'no promotional' in request['ending'].lower()
    shots = [s for c in result['scenes'] for s in c['shots']]
    assert sum(s['duration_sec'] for s in shots) == 65
    assert all(s.get('purpose') not in ('midroll', 'outro') for s in shots)


@pytest.mark.asyncio
async def test_resume_and_export_do_not_require_a_cta(client, monkeypatch):
    project = persisted_episode(client, monkeypatch, story())
    with service.database() as conn:
        context = json.loads(conn.execute('SELECT context_json FROM studio_episodes WHERE project_id=?',
                                          (project,)).fetchone()[0])
        context['directing_profile']['cta_placement'] = 'none'
        conn.execute('UPDATE studio_episodes SET context_json=? WHERE project_id=?',
                     (json.dumps(context), project))
        conn.commit()
    before = service.board(project)
    production.ensure_midroll(project)
    after = service.board(project)
    assert after['project']['revision'] == before['project']['revision']
    assert all(s.get('purpose') not in ('midroll', 'outro') for c in after['scenes'] for s in c['shots'])
    with pytest.raises(service.HTTPException) as missing:
        await service.enqueue_export(project, 'no-cta')
    assert 'incitation' not in missing.value.detail.lower()
