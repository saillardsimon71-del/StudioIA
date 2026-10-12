import json
from pathlib import Path

import httpx

root = Path(r'AUDIT_WORKSPACE')
state_path = root / 'work/mort-de-honte-revision2-launch.json'
state = json.loads(state_path.read_text(encoding='utf-8-sig'))
client = httpx.Client(base_url='http://127.0.0.1:8247/api/studio', timeout=60)

def api(method, path, data=None):
    response = client.request(method, path, json=data)
    response.raise_for_status()
    return response.json()

def save():
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

project_id = state['project_id']
board = api('GET', f'/projects/{project_id}')
assert board['production']['status'] == 'paused', board['production']
for name, shot_id, filename in (
    ('verified_opening_image', state['opening_shot_id'], 'mort-de-honte-revision2-opening-manual-2.png'),
    ('verified_midroll_image', state['midroll_shot_id'], 'mort-de-honte-revision2-midroll-manual.png'),
):
    image = root / 'work' / filename
    assert image.is_file()
    if name + '_take_id' not in state:
        with image.open('rb') as stream:
            response = client.post(f'/shots/{shot_id}/upload', data={'kind': 'image'}, files={'file': (image.name, stream, 'image/png')})
        response.raise_for_status()
        state[name + '_take_id'] = response.json()['id']
        save()
    api('POST', f"/takes/{state[name + '_take_id']}/select")
board = api('GET', f'/projects/{project_id}')
shots = [shot for scene in board['scenes'] for shot in scene['shots']]
brief = json.loads((root / 'work/mort-de-honte-revision2-brief.json').read_text(encoding='utf-8'))
expected = [(turn['speaker'], turn['text']) for beat in brief['expected_story']['beats'] for turn in beat['dialogue_turns']]
actual = [(turn['speaker'], turn['text']) for shot in shots if shot.get('purpose') != 'midroll' for turn in shot['dialogue_turns']]
assert actual == expected
assert shots[0]['selected_image_id'] == state['verified_opening_image_take_id']
midroll = next(shot for shot in shots if shot.get('purpose') == 'midroll')
assert midroll['selected_image_id'] == state['verified_midroll_image_take_id']
assert midroll['screen_text'] == []
assert len(shots) == 7 and sum(shot['duration_sec'] for shot in shots) == 60
(root / 'work/mort-de-honte-revision2-ready-board.json').write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding='utf-8')
result = api('POST', f'/projects/{project_id}/production/resume')
state.update(status='render_started', production=result['production'], opening_visual_check='Three characters only; Enzo seated inside basket; women standing; cart wheels readable.', midroll_visual_check='Clara alone, same appearance as opening, pointing toward viewer; no phone overlay.')
save()
print(json.dumps(state, ensure_ascii=False))
