import json

from calliope.queue.manager import queue_manager
from calliope.studio import service
from tests.test_studio_midroll import persisted_episode, story


def test_retake_keeps_unaffected_takes_and_rebuilds_only_the_continuous_chain(client, monkeypatch, tmp_path):
    from tests.test_studio import video_bytes

    plan = story()
    plan['scenes'][0]['shots'][1]['continue_from_previous'] = True
    plan['scenes'][0]['shots'][2]['continue_from_previous'] = True
    project = persisted_episode(client, monkeypatch, plan)
    board = service.board(project)
    shots = board['scenes'][0]['shots']
    content = video_bytes(tmp_path, audio=True)
    original = []
    for shot in shots:
        take = service.store_upload(shot['id'], content, 'video')
        service.select_take(take['id'])
        service.approve_shot(shot['id'], True)
        original.append(take['id'])
    old_job = board['production']['job_id']
    queue_manager.mark_done(old_job, [])
    with service.database() as conn:
        conn.execute("UPDATE studio_productions SET status='done',stage='done' WHERE project_id=?", (project,))
        conn.commit()
    url = f"/api/studio/projects/{project}/shots/{shots[1]['id']}/retake"
    first = client.post(url, json={'request_id':'targeted-retake'})
    assert first.status_code == 200, first.text
    result = first.json()
    prepared = result['scenes'][0]['shots']
    assert prepared[0]['selected_video_id'] == original[0]
    assert prepared[1]['selected_video_id'] is None
    assert prepared[2]['selected_video_id'] is None
    assert prepared[3]['selected_video_id'] == original[3]
    assert all(len(s['takes']) == 1 for s in prepared)
    new_job = result['production']['job_id']
    assert new_job != old_job
    assert result['production']['status'] == 'pending'
    second = client.post(url, json={'request_id':'targeted-retake'})
    assert second.status_code == 200
    assert second.json()['production']['job_id'] == new_job
    assert json.loads(queue_manager.get_job(new_job)['payload_json'])['studio_production_id'] == project
