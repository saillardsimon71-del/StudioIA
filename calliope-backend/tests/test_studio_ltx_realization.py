import copy

import pytest

from calliope.config import settings
from calliope.studio import series_planner
from tests.test_studio_native_contract import native_plan
from calliope.studio.ltx_realization import prepare_ltx_plan


def conversation():
    plan = native_plan()
    plan['entities'].append({'key':'visitor', 'kind':'character', 'name':'Visitor',
                             'description':'A visitor in a blue coat.'})
    plan['render_canon']['characters']['visitor'] = {
        'name':'Visitor', 'appearance':'A visitor in a blue coat.', 'voice':'Young clear tenor.'}
    first = plan['scenes'][0]['shots'][0]
    first.update(character_keys=['keeper','visitor'], listener_keys=[],
                 dialogue_turns=[
                     {'speaker':'keeper','text':'Tu as ouvert le coffre ?', 'action':'Keeper points at the case.'},
                     {'speaker':'visitor','text':"Non, je te jure !", 'action':'Visitor opens the case.'},
                     {'speaker':'keeper','text':'Je viens de te voir.', 'reaction':'Keeper shakes his head.'}],
                 dialogue='', speaker='', duration_sec=7.2, continue_from_previous=False,
                 camera='The camera stays fixed.', framing='Wide shared composition.',
                 action='Keeper leans forward.', reaction='Visitor closes the case.',
                 state_changes=[
                     {'key':'case','from':'Closed on the desk.','to':'Open on the desk.',
                      'action_ref':'dialogue_turns.1.action'},
                     {'key':'case','from':'Open on the desk.','to':'Closed on the desk.',
                      'action_ref':'reaction'}])
    first.pop('end_state', None)
    plan['scenes'][0]['shots'] = [first]
    return plan


def test_ltx_prepares_single_voice_continuations_without_rewriting_the_story(monkeypatch):
    monkeypatch.setattr(settings, 'studio_video_profile', 'ltx')
    source = conversation()
    before = copy.deepcopy(source)
    plan = prepare_ltx_plan(series_planner.normalize_plan(source, {'entities':[]}))
    shots = plan['scenes'][0]['shots']
    assert [s['speaker'] for s in shots] == ['keeper','visitor','keeper']
    assert [s['dialogue'] for s in shots] == [t['text'] for t in before['scenes'][0]['shots'][0]['dialogue_turns']]
    assert [s['continue_from_previous'] for s in shots] == [False,True,True]
    assert all(s['character_keys'] == ['keeper','visitor'] for s in shots)
    assert all(s['framing'] == 'Wide shared composition.' for s in shots)
    assert shots[0]['action'] == 'Keeper leans forward.'
    assert shots[1]['action'] == shots[2]['action'] == ''
    assert shots[0]['reaction'] == shots[1]['reaction'] == ''
    assert shots[2]['reaction'] == 'Visitor closes the case.'
    assert shots[0]['end_state']['case'] == 'Closed on the desk.'
    assert shots[1]['start_state']['case'] == 'Closed on the desk.'
    assert shots[1]['end_state']['case'] == 'Open on the desk.'
    assert shots[2]['start_state']['case'] == 'Open on the desk.'
    assert shots[2]['end_state']['case'] == 'Closed on the desk.'
    series_planner._resolve_native_states(plan)
    series_planner._validate_native_contract(plan, plan['entities'])
    assert series_planner.normalize_plan(copy.deepcopy(plan), {'entities':[]}) == plan


def test_h3_conversation_remains_a_multi_speaker_take(monkeypatch):
    monkeypatch.setattr(settings, 'studio_video_profile', 'fast')
    plan = series_planner.normalize_plan(conversation(), {'entities':[]})
    assert len(plan['scenes'][0]['shots']) == 1
    assert len(plan['scenes'][0]['shots'][0]['dialogue_turns']) == 3


def test_ltx_keeps_authored_timed_overlays_and_requests_review_instead_of_moving_them(monkeypatch):
    monkeypatch.setattr(settings, 'studio_video_profile', 'ltx')
    source = conversation()
    source['scenes'][0]['shots'][0]['screen_text'] = [
        {'key':'label','text':'OUVERT','x':0.5,'y':0.8,'event':'reveal'}]
    plan = prepare_ltx_plan(series_planner.normalize_plan(source, {'entities':[]}))
    assert len(plan['scenes'][0]['shots']) == 1
    assert 'LTX' in plan['editorial_review']['risks']


@pytest.mark.asyncio
async def test_normal_planner_applies_ltx_realization_before_saving(monkeypatch):
    monkeypatch.setattr(settings, 'studio_video_profile', 'ltx')
    source = conversation()
    story = {'title':'Le coffre', 'narrative_outline':source['narrative_outline'],
             'beats':[{'action':'Une dispute.', 'dialogue_turns':[
                 {key:turn[key] for key in ('speaker','text')}
                 for turn in source['scenes'][0]['shots'][0]['dialogue_turns']]}]}

    async def develop(*args, **kwargs):
        return story, {}

    async def generate(messages, **kwargs):
        assert 'LTX REALIZATION' in messages[0]['content']
        return copy.deepcopy(source)

    monkeypatch.setattr(series_planner, '_develop_creative_story', develop)
    monkeypatch.setattr(series_planner, 'generate_structured', generate)
    plan = await series_planner.plan_episode(
        {'entities':[], 'prompt_contract_version':2, 'language':'fr',
         'directing_profile':{'cta_placement':'end'}}, 'Une dispute devant un coffre.', '')
    shots = plan['scenes'][0]['shots']
    assert [s['speaker'] for s in shots[:-1]] == ['keeper','visitor','keeper']
    assert shots[-1]['purpose'] == 'midroll'
    assert plan['creative_story'] == story


def test_new_series_uses_end_cta_without_rewriting_existing_series(client):
    item = client.post('/api/studio/series', json={
        'title':'Test', 'concept':'Conversation.'}).json()
    assert item['directing_profile']['cta_placement'] == 'end'
