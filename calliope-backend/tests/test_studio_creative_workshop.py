import copy
import json

import pytest

from calliope.studio import series_planner
from tests.test_studio_story_preparation import creative_story, technical_plan


def candidates():
    return [
        {'id': 'A', 'title': 'Le coffre éternue', 'scene': 'Le gardien prétend être seul. Son coffre éternue et réclame une augmentation.'},
        {'id': 'B', 'title': 'La rançon du gardien', 'scene': 'Le gardien se fait passer pour le contenu du coffre. La cliente veut un remboursement : elle avait commandé quelqu\'un de compétent.'},
        {'id': 'C', 'title': 'Le coffre adopte', 'scene': 'Le coffre offre au gardien un contrat d\'adoption. Le gardien signe, puis découvre que son argent de poche dépend de la caisse.'},
    ]


def selected_story(candidate_id='B'):
    return creative_story() | {'editorial_choice': {
        'candidate_id': candidate_id,
        'reason': 'La cliente transforme le piège du gardien en humiliation compréhensible.',
        'revision': 'Rendre la demande de remboursement plus orale et montrer immédiatement le gardien dans le coffre.',
    }}


@pytest.mark.asyncio
async def test_selected_scene_reaches_realization_without_the_rejected_candidates(monkeypatch):
    requests = []
    roles = []
    replies = [{'candidates': candidates()}, selected_story(), technical_plan()]

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        roles.append(options.get('role'))
        return copy.deepcopy(replies[len(requests) - 1])

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    plan = await series_planner.plan_episode(
        {'entities': [], 'prompt_contract_version': 2, 'target_seconds': 32.5, 'language': 'fr'},
        'Une scène absurde au coffre.', '',
    )
    assert plan['creative_story'] == creative_story()
    assert roles == ['story', 'script', 'planner']
    assert plan['creative_workshop']['candidates'] == candidates()
    assert plan['creative_workshop']['selection']['candidate_id'] == 'B'
    realization = json.loads(requests[-1][1]['content'])
    assert realization['creative_story'] == creative_story()
    assert 'creative_candidates' not in realization['series']
    assert 'creative_workshop' not in realization['creative_story']
    spoken = [turn['text'] for scene in plan['scenes'] for shot in scene['shots']
              if shot.get('purpose') != 'midroll' for turn in shot['dialogue_turns']]
    assert spoken == ['Line 0.', 'Line 1.', 'Line 2.', 'Line 3.', 'Line 4.', 'Line 5.']


@pytest.mark.asyncio
async def test_editor_cannot_claim_to_select_an_unknown_candidate(monkeypatch):
    replies = iter([{'candidates': candidates()}, selected_story('unknown'), selected_story('C')])

    async def generate(messages, **options):
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    story, workshop = await series_planner._develop_creative_story({'entities': []}, 'Une scène.', '')
    assert workshop['selection']['candidate_id'] == 'C'
    assert story == creative_story()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_id', [[], {}])
async def test_malformed_editorial_choice_can_be_repaired(monkeypatch, invalid_id):
    replies = iter([{'candidates': candidates()}, selected_story(invalid_id), selected_story('C')])

    async def generate(messages, **options):
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    _, workshop = await series_planner._develop_creative_story({'entities': []}, 'Une scène.', '')
    assert workshop['selection']['candidate_id'] == 'C'


@pytest.mark.asyncio
async def test_incomplete_candidate_pool_is_repaired_before_selection(monkeypatch):
    replies = iter([{'candidates': [candidates()[0]]}, {'candidates': candidates()}, selected_story()])

    async def generate(messages, **options):
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    _, workshop = await series_planner._develop_creative_story({'entities': []}, 'Une scène.', '')
    assert len(workshop['candidates']) == 3
    assert workshop['selection']['candidate_id'] == 'B'


@pytest.mark.asyncio
async def test_workshop_keeps_the_user_brief_and_series_canon_in_both_writing_stages(monkeypatch):
    context = {'entities': [], 'bible': {'tone': 'Drame réaliste', 'past_events': 'Le gardien a perdu son emploi.'},
               'concept': 'Un drame familial sans surnaturel.',
               'directing_profile': {'shot_prompt_budget': 3600},
               'display_values': {'clock': 12}, '_planner_trace': {'project_id': 4, 'job_id': 5}}
    original = copy.deepcopy(context)
    brief = 'Commencer par cette réplique exacte : Je n\'ai plus de maison.'
    objective = 'Fin complète, aucune suite prévue.'
    requests = []
    replies = iter([{'candidates': candidates()}, selected_story()])

    async def generate(messages, **options):
        requests.append(json.loads(messages[1]['content']))
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    await series_planner._develop_creative_story(context, brief, objective)
    for request in requests:
        assert request['idea'] == brief
        assert request['objective'] == objective
        assert request['series']['bible']['tone'] == 'Drame réaliste'
        assert request['series']['bible']['past_events'] == 'Le gardien a perdu son emploi.'
        assert not {'directing_profile', '_planner_trace'} & request['series'].keys()
        assert request['series']['display_values'] == {'clock': 12}
    assert requests[1]['creative_candidates'] == candidates()
    assert context == original


@pytest.mark.asyncio
async def test_candidates_with_duplicate_ids_are_not_ambiguous_editorial_choices(monkeypatch):
    pool = candidates()
    pool[1]['id'] = 'A'
    replies = iter([{'candidates': pool}, {'candidates': candidates()}, selected_story()])

    async def generate(messages, **options):
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    _, workshop = await series_planner._develop_creative_story({'entities': []}, 'Une scène.', '')
    assert [candidate['id'] for candidate in workshop['candidates']] == ['A', 'B', 'C']


@pytest.mark.asyncio
async def test_writer_receives_canon_but_no_reference_generation_history(monkeypatch):
    context = {'entities': [{'key': 'keeper', 'kind': 'character', 'name': 'Le gardien',
        'description': 'Il a peur de perdre son emploi.',
        'identity': {'visual_description': 'Un robot rouge.', 'variant_of': '',
                     'attached_value': 12, 'label_key': 'age', 'reference_sha256': 'old-image-hash'},
        'id': 42, 'series_id': 9, 'reference_path': 'old.png',
        'reference_request': {'prompt': 'Un ancien mariage, sans rapport avec cet épisode.'},
        'url': '/api/file?path=old.png'}],
        'bible': {'past_events': 'Le robot a sauvé sa soeur.'}, 'history': [{'summary': 'Il a une dette.'}]}
    original = copy.deepcopy(context)
    requests = []
    replies = iter([{'candidates': candidates()}, selected_story()])

    async def generate(messages, **options):
        requests.append(json.loads(messages[1]['content']))
        return copy.deepcopy(next(replies))

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    await series_planner._develop_creative_story(context, 'Une enquête.', '')
    for request in requests:
        entity = request['series']['entities'][0]
        assert entity == {'key': 'keeper', 'kind': 'character', 'name': 'Le gardien',
            'description': 'Il a peur de perdre son emploi.',
            'identity': {'visual_description': 'Un robot rouge.', 'variant_of': '',
                         'attached_value': 12, 'label_key': 'age'}}
        assert request['series']['history'] == context['history']
        assert request['series']['bible'] == context['bible']
        assert 'ancien mariage' not in json.dumps(request, ensure_ascii=False)
    assert context == original
