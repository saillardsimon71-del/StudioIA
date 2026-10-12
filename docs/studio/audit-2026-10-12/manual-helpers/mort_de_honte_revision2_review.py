import json
import re
from pathlib import Path

import httpx

root = Path(r'AUDIT_WORKSPACE')
state_path = root / 'work/mort-de-honte-revision2-launch.json'
state = json.loads(state_path.read_text(encoding='utf-8'))
brief = json.loads((root / 'work/mort-de-honte-revision2-brief.json').read_text(encoding='utf-8'))
client = httpx.Client(base_url='http://127.0.0.1:8247/api/studio', timeout=60)

def api(method, path, data=None):
    response = client.request(method, path, json=data)
    response.raise_for_status()
    return response.json()

project_id = state['project_id']
board = api('GET', f'/projects/{project_id}')
assert board['production']['status'] == 'paused', board['production']
assert board['production']['stage'] == 'review', board['production']
shots = [shot for scene in board['scenes'] for shot in scene['shots']]
expected = [(turn['speaker'], turn['text']) for beat in brief['expected_story']['beats'] for turn in beat['dialogue_turns']]
actual = [(turn['speaker'], turn['text']) for shot in shots if shot.get('purpose') != 'midroll' for turn in shot.get('dialogue_turns', [])]
assert actual == expected, {'expected': expected, 'actual': actual}
midrolls = [shot for shot in shots if shot.get('purpose') == 'midroll']
assert len(midrolls) == 1
assert [turn['speaker'] for turn in midrolls[0]['dialogue_turns']] == ['clara']
assert midrolls[0]['screen_text'] == []
assert [(turn['speaker'], turn['text']) for turn in shots[0]['dialogue_turns']] == expected[:2]
assert not shots[0]['continue_from_previous']
document = (root / 'outputs/MORT_DE_HONTE_REVISION_2.md').read_text(encoding='utf-8')
image_prompt = re.search(r'## Prompt d\x27image.*?```text\n(.*?)\n```', document, re.S).group(1)
opening = shots[0]
api('PATCH', f"/projects/{project_id}/shots/{opening['id']}", {
    'image_prompt': image_prompt,
    'blocking': "Initial composition: Enzo sits inside the metal shopping cart basket, hips and folded legs within its mesh sides, head and torso above the rim. Clara stands behind its handle on the left, both hands on the handle. Jade stands to the right facing Clara and Enzo. All three faces and the cart wheels are visible, with the deep salon behind them.",
    'camera': 'Keep a shared eye-level three-quarter medium-wide composition through this opening exchange.',
})
preview = api('GET', f"/projects/{project_id}/shots/{opening['id']}/prompt-preview")
assert 'INSIDE the deep wire basket' in preview['image_prompt']
board = api('GET', f'/projects/{project_id}')
(root / 'work/mort-de-honte-revision2-prepared-board.json').write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding='utf-8')
(root / 'work/mort-de-honte-revision2-opening-preview.json').write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding='utf-8')
state.update(status='script_reviewed', opening_shot_id=opening['id'], checked_story_turns=len(expected), midroll_shot_id=midrolls[0]['id'])
if 'opening_image_take_id' not in state:
    result = api('POST', f"/shots/{opening['id']}/generate", {'kind': 'image', 'request_id': 'mort-de-honte-r2-opening-image-20261012', 'n_takes': 1})
    state.update(status='opening_image_requested', opening_image_take_id=result['takes'][0]['id'], opening_image_job_id=result['jobs'][0]['id'])
state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(state | {'shots': [{'id': shot['id'], 'seconds': shot['duration_sec'], 'description': shot['description'], 'purpose': shot.get('purpose'), 'dialogue_turns': shot['dialogue_turns']} for shot in shots]}, ensure_ascii=False))
