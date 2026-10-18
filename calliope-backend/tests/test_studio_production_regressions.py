import asyncio
import copy
import json

import pytest

from calliope.config import settings
from calliope.queue.manager import queue_manager
from calliope.studio import pacing, production, series_planner, service, transcription
from tests.test_studio_series import episode, planned_episode, series


@pytest.fixture
def repeated_line():
    expected = "WESH KIKI !! T'AS VENDU LA GRAVITÉ POUR DES CHIPS ?"
    words = [
        (' Wesh', 0.0, 0.36, 0.41), (' Kiki,', 0.36, 0.64, 0.61),
        (' t', 0.98, 1.02, 0.72), ("'as", 1.02, 1.1, 0.88),
        (' vendu', 1.1, 1.32, 0.97), (' la', 1.32, 1.5, 0.99),
        (' gravité', 1.5, 1.82, 0.98), (' pour', 1.82, 2.06, 0.99),
        (' des', 2.06, 2.18, 0.99), (' chips', 2.18, 2.46, 0.84),
        (' ?', 2.46, 2.74, 0.40),
    ]
    second = [
        (' Wesh', 2.74, 2.98, 0.97), (' Kiki,', 2.98, 3.32, 0.99),
        (' t', 3.54, 3.6, 0.99), ("'as", 3.6, 3.64, 0.99),
        (' vendu', 3.64, 3.88, 0.99), (' la', 3.88, 4.02, 0.99),
        (' gravité', 4.02, 4.38, 0.99), (' pour', 4.38, 4.6, 0.99),
        (' des', 4.6, 4.7, 0.99), (' chips', 4.7, 4.96, 0.99),
        (' ?', 4.96, 5.16, 0.77),
    ]
    segments = [
        {'words': [{'word': text, 'start': start, 'end': end, 'probability': probability}
                   for text, start, end, probability in items]}
        for items in [words, second]
    ]
    return expected, {
        'status': 'ok',
        'text': "Wesh Kiki, t'as vendu la gravité pour des chips ? " * 2,
        'segments': segments,
        'speech_intervals': [[0.0, 2.74], [2.74, 5.16]],
    }


def test_repeated_phrase_uses_first_occurrence_and_a_safe_cut(repeated_line):
    expected, transcript = repeated_line
    verdict = transcription.assess_transcript(expected, transcript)
    assert verdict['status'] == 'ok'
    assert verdict['voice'] == [[0.0, 2.46]]
    assert verdict['trim_end_limit_sec'] == 2.74
    assert verdict['repeated_occurrences'] == 2


def test_intended_repeated_exchange_is_not_removed(repeated_line):
    expected, transcript = repeated_line
    verdict = transcription.assess_transcript(expected + ' ' + expected, transcript)
    assert verdict['status'] == 'ok'
    assert 'trim_end_limit_sec' not in verdict


def test_repeated_phrase_without_word_timing_requires_retry(repeated_line):
    expected, transcript = repeated_line
    transcript['segments'] = []
    assert transcription.assess_transcript(expected, transcript)['status'] == 'extra'


def test_extra_invented_words_are_not_discarded_as_a_repeat(repeated_line):
    expected, transcript = repeated_line
    transcript['text'] += 'I rule the entire city and you must obey me now.'
    assert 'trim_end_limit_sec' not in transcription.assess_transcript(expected, transcript)


def test_assessment_preserves_first_occurrence_and_trim_caps_before_repeat(
    client, monkeypatch, tmp_path, repeated_line
):
    expected, transcript = repeated_line
    path = tmp_path / 'cached-take.mp4'
    path.write_bytes(b'cached media fixture')
    stat = path.stat()
    take = {'id': 314, 'output_path': str(path), 'metadata': {
        'duration_sec': 5.166667, 'has_audio': True,
        'transcription_cache': {
            'signature': [stat.st_size, stat.st_mtime_ns, settings.studio_asr_checkpoint, 'fr'],
            'transcript': transcript,
            'activity': {'intervals': [[0, 5.166667]], 'max_db': -0.9},
        },
    }}
    shot = {'id': 123, 'duration_sec': 3.4, 'dialogue': expected, 'audio_mode': 'native',
            'selected_video_id': take['id'], 'takes': [take]}
    monkeypatch.setattr(service, 'shot_context', lambda *args: {
        'language': 'fr', 'shot': {'dialogue_turns': [{'speaker': 'benny', 'text': expected}]},
    })
    monkeypatch.setattr(service, 'render_event', lambda *args, **kwargs: None)
    verdict = asyncio.run(production.assess_take(shot))
    assert verdict['voice'] == [[0, 2.46]]
    assert take['metadata']['dialogue_qc']['trim_end_limit_sec'] == 2.74
    cuts = []
    monkeypatch.setattr(service, 'save_trim', lambda conn, identifier, start, end:
                        cuts.append((start, end)) or True)
    production.trim_take(shot, verdict['voice'], verdict['trim_end_limit_sec'])
    assert cuts == [(0, pacing.floor_grid(2.74))]
    assert 2.46 <= cuts[0][1] < 2.74


def test_listener_only_closeup_preserves_ordered_two_speaker_exchange():
    plan = planned_episode({})
    plan['scenes'][0]['shots'] = plan['scenes'][0]['shots'][:1]
    plan['entities'].append({'key': 'visitor', 'kind': 'character', 'name': 'Visitor',
                             'description': 'A blue coat.'})
    shot = plan['scenes'][0]['shots'][0]
    shot.pop('speaker')
    shot.pop('dialogue')
    shot.update(character_keys=['keeper'], dialogue_turns=[
        {'speaker': 'keeper', 'text': 'Did you sell it?'},
        {'speaker': 'visitor', 'text': 'It was a good deal.'},
    ], duration_sec=6.2)
    result = series_planner.normalize_plan(plan, {'entities': []})
    normalized = result['scenes'][0]['shots'][0]
    assert len(result['scenes'][0]['shots']) == 1
    assert [turn['speaker'] for turn in normalized['dialogue_turns']] == ['keeper', 'visitor']
    assert normalized['character_keys'] == ['keeper', 'visitor']
    assert normalized['duration_sec'] == 6.2


def test_listener_is_included_in_still_and_character_references():
    plan = planned_episode({})
    plan['entities'].append({'key': 'visitor', 'kind': 'character', 'name': 'Visitor',
                             'description': 'A blue coat.'})
    shot = plan['scenes'][0]['shots'][0]
    shot.update(character_keys=['keeper'], listener_keys=['visitor'])
    normalized = series_planner.normalize_plan(plan, {'entities': []})['scenes'][0]['shots'][0]
    assert normalized['character_keys'] == ['keeper', 'visitor']
    assert 'Visitor' in normalized['image_prompt']
    assert 'Visitor' in normalized['prompt']


def test_pause_prevents_the_next_shot_from_overwriting_paused_state(client, monkeypatch):
    from calliope.studio import video_preparation
    from unittest.mock import AsyncMock

    monkeypatch.setattr(video_preparation, 'prepare_video_batch', AsyncMock(return_value=None))
    previous = queue_manager.paused
    queue_manager.paused = True
    monkeypatch.setattr(settings, 'dry_run', False)
    monkeypatch.setattr(settings, 'llm_base_url', 'http://localhost:9001/v1')
    monkeypatch.setattr(settings, 'studio_image_base_url', 'http://localhost:9002/v1')
    monkeypatch.setattr(settings, 'studio_video_concurrency', 1)
    try:
        created = episode(client, series(client)['id']).json()
        plan = planned_episode({})
        plan['scenes'][0]['shots'].append(copy.deepcopy(plan['scenes'][0]['shots'][0]))
        production.save_episode_plan(created['project_id'], plan)
        board = service.board(created['project_id'])
        shots = board['scenes'][0]['shots']
        attempted = []

        async def render(job, shot, should_stop):
            attempted.append(shot['id'])
            if shot['id'] == shots[0]['id']:
                with service.database() as conn:
                    conn.execute("UPDATE studio_productions SET status='paused',message=? "
                                 'WHERE project_id=?', ('Review the real video.', created['project_id']))
                    conn.commit()
                raise production.ProductionPausedError()

        monkeypatch.setattr(production, 'render_shot', render)
        job = queue_manager.get_job(created['job_id'])
        with pytest.raises(production.ProductionPausedError):
            asyncio.run(production.render_videos(job, shots, lambda: False))
        assert attempted == [shots[0]['id']]
        state = service.board(created['project_id'])['production']
        assert state['status'] == 'paused'
        assert state['message'] == 'Review the real video.'
    finally:
        queue_manager.paused = previous


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unknown", "missing", "extra", "inaudible", "ok"])
@pytest.mark.parametrize("approved", [False, True])
async def test_dialogue_assessment_never_rerenders_and_only_approves_ok_takes(
    client, monkeypatch, status, approved
):
    previous = queue_manager.paused
    queue_manager.paused = True
    monkeypatch.setattr(settings, 'dry_run', False)
    monkeypatch.setattr(settings, 'llm_base_url', 'http://localhost:9001/v1')
    monkeypatch.setattr(settings, 'studio_image_base_url', 'http://localhost:9002/v1')
    try:
        created = episode(client, series(client)['id']).json()
        production.save_episode_plan(created['project_id'], planned_episode({}))
        shot = service.board(created['project_id'])['scenes'][0]['shots'][0]
        shot.update(approved=approved, selected_video_id=242,
                    takes=[{'id': 242, 'engine': 'import'}])
        decisions = []
        monkeypatch.setattr(production, 'load_shot', lambda identifier: shot)
        monkeypatch.setattr(queue_manager, 'is_cancelled', lambda identifier: False)
        monkeypatch.setattr(production, 'trim_take', lambda *args: None)
        monkeypatch.setattr(service, 'approve_shot', lambda *args: decisions.append(args))

        async def assess(value):
            return {'status': status, 'reason': 'Transcription incertaine ou aucune parole reconnue.',
                    'voice': None}

        async def generate(*args, **kwargs):
            pytest.fail('Une transcription incertaine ne doit pas déclencher de rendu GPU.')

        monkeypatch.setattr(production, 'assess_take', assess)
        monkeypatch.setattr(production, 'generate_take', generate)
        job = queue_manager.get_job(created['job_id'])
        await production.render_shot(job, shot, lambda: False)
        # Un QC non concluant ne relance pas de rendu et ne vaut jamais
        # approbation : la prise reste pour la revue humaine.
        expected = [(shot['id'], True)] if status == 'ok' else []
        assert decisions == expected
    finally:
        queue_manager.paused = previous
