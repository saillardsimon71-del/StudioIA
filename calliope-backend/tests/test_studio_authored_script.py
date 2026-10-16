import copy
import json

import pytest

from calliope.studio import series_planner
from tests.test_studio_ltx_realization import conversation


ENTITIES = [{'key': 'keeper', 'kind': 'character', 'name': 'Le gardien'},
            {'key': 'visitor', 'kind': 'character', 'name': 'La visiteuse'}]
SCRIPT = '''Titre : Le coffre.

Conserver les événements et les répliques suivants, mot pour mot.

Le gardien garde le coffre fermé et pointe sa serrure.
LE GARDIEN : "Tu as ouvert le coffre ?"

La visiteuse lève les mains. Le coffre reste fermé.
LA VISITEUSE : "Non, je te jure !"

Le gardien ouvre le coffre en la regardant.
LE GARDIEN : "Je viens de te voir."

Le coffre se referme. Fin.

RÉALISATION

Ne pas prononcer ces consignes. Garder les voix existantes.
'''


@pytest.mark.asyncio
async def test_authored_actions_and_words_bypass_creative_rewriting(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('A supplied locked script must not be rewritten or consume a creative call.')

    monkeypatch.setattr(series_planner, 'generate_structured', forbidden)
    story, workshop = await series_planner._develop_creative_story(
        {'entities': ENTITIES}, SCRIPT, '')
    assert story['source_locked'] is True
    assert [b['action'] for b in story['beats']] == [
        'Le gardien garde le coffre fermé et pointe sa serrure.',
        'La visiteuse lève les mains. Le coffre reste fermé.',
        'Le gardien ouvre le coffre en la regardant.']
    assert [b['dialogue_turns'][0]['speaker'] for b in story['beats']] == [
        'keeper', 'visitor', 'keeper']
    assert story['beats'][-1]['consequence'] == 'Le coffre se referme. Fin.'
    assert 'consignes' not in json.dumps(story, ensure_ascii=False)
    assert workshop['source'] == 'operator_script'


def test_locked_story_rejects_a_take_merging_distinct_authored_beats():
    from calliope.studio.authored_script import authored_story

    story = authored_story(SCRIPT, ENTITIES)
    plan = conversation()
    plan['scenes'][0]['shots'][0]['source_beat_index'] = 0
    with pytest.raises(ValueError, match='source_beat_index'):
        series_planner._validate_story_realization(story, plan)
    source = plan['scenes'][0]['shots'][0]
    plan['scenes'][0]['shots'] = []
    for i, turn in enumerate(source['dialogue_turns']):
        plan['scenes'][0]['shots'].append(source | {
            'source_beat_index': i, 'dialogue_turns': [copy.deepcopy(turn)]})
    series_planner._validate_story_realization(story, plan)


def test_free_ideas_and_example_quotes_are_not_treated_as_locked_scripts():
    from calliope.studio.authored_script import authored_story

    assert authored_story('Une dispute. Exemple : "Tu as ouvert le coffre ?"', ENTITIES) is None
    assert authored_story('LE GARDIEN : "Tu as ouvert le coffre ?"', ENTITIES) is None


def test_director_does_not_accept_a_short_story_as_ready():
    from tests.test_studio_story_preparation import technical_plan

    with pytest.raises(ValueError, match='trop courte'):
        series_planner.normalize_plan(technical_plan(), {
            'entities': [], 'target_seconds': 60, 'studio_director': True})


def test_authored_markdown_labels_and_french_quotes_keep_exact_speech():
    from calliope.studio.authored_script import authored_story

    text = SCRIPT.replace('LE GARDIEN : "Tu as ouvert le coffre ?"',
                          '**LE GARDIEN :** \u00ab Tu as ouvert le coffre ? \u00bb')
    assert authored_story(text, ENTITIES)['beats'][0]['dialogue_turns'] == [
        {'speaker': 'keeper', 'text': 'Tu as ouvert le coffre ?'}]


@pytest.mark.asyncio
async def test_locked_story_is_checked_before_any_state_compilation(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(messages)
        if len(requests) == 1:
            return conversation()
        assert messages[0]['content'].startswith('REPAIR VALUES ONLY')
        raise RuntimeError('Stop offline before any additional model call.')

    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    with pytest.raises(RuntimeError, match='Stop offline'):
        await series_planner.plan_episode(
            {'entities': ENTITIES, 'language': 'fr', 'prompt_contract_version': 2}, SCRIPT, '')
    assert len(requests) == 2
    fields = json.loads(requests[1][-1]['content'])['repair_fields']
    assert fields[0]['path'][-1] == 'source_beat_index'
