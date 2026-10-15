import copy
from itertools import groupby


def prepare_ltx_plan(plan):
    plan = copy.deepcopy(plan)
    audio = plan.get('audio') or {}
    timed = {cue['shot_index'] for field in ('sfx', 'holds') for cue in audio.get(field, [])}
    positions, old_index, new_index = {}, 0, 0
    for scene in plan['scenes']:
        prepared = []
        for shot in scene['shots']:
            positions[old_index] = new_index
            turns = shot.get('dialogue_turns') or []
            groups = [list(group) for _, group in groupby(enumerate(turns), lambda pair: pair[1]['speaker'])]
            split = len(groups) > 1 and shot.get('purpose') not in ('midroll', 'outro')
            if split and (shot.get('screen_text') or old_index in timed or (
                    'state_changes' not in shot and shot.get('start_state') != shot.get('end_state'))):
                review = plan.setdefault('editorial_review', {})
                warning = 'LTX : échange à plusieurs voix avec effets synchronisés à vérifier dans les prises.'
                if warning not in review.get('risks', ''):
                    review['risks'] = (review.get('risks', '') + ' ' + warning).strip()
                split = False
            if not split:
                prepared.append(shot)
                new_index += 1
                old_index += 1
                continue
            actions = [('action', shot.get('action'))]
            actions += [(f'dialogue_turns.{i}.{field}', turn.get(field))
                        for i, turn in enumerate(turns) for field in ('action', 'reaction')]
            actions.append(('reaction', shot.get('reaction')))
            changes, cursor = [], 0
            for change in shot.get('state_changes', []):
                cursor = next(i for i, (ref, action) in enumerate(actions)
                              if i >= cursor and action and (
                                  ref == change['action_ref'] if 'action_ref' in change
                                  else action == change.get('action')))
                changes.append(change | {'action_ref': actions[cursor][0]})
            weights = [max(1, sum(len(t['text'].split()) for _, t in group)) for group in groups]
            state = copy.deepcopy(shot.get('start_state') or {})
            for group_index, indexed in enumerate(groups):
                offset = indexed[0][0]
                group = [turn for _, turn in indexed]
                part = copy.deepcopy(shot)
                part.update(dialogue_turns=group, speaker=group[0]['speaker'],
                            dialogue='\n'.join(t['text'] for t in group),
                            duration_sec=max(1.0, round(shot['duration_sec'] * weights[group_index] / sum(weights), 1)),
                            listener_keys=[key for key in shot['character_keys'] if key != group[0]['speaker']],
                            action=shot.get('action', '') if group_index == 0 else '',
                            reaction=shot.get('reaction', '') if group_index == len(groups) - 1 else '',
                            start_state=copy.deepcopy(state), state_changes=[])
                if group_index:
                    part.update(continue_from_previous=True, cut_reason='',
                                camera='Continue the established camera and framing smoothly, without a cut.')
                for change in changes:
                    ref = change['action_ref']
                    if ref.startswith('dialogue_turns.'):
                        _, turn_index, field = ref.split('.')
                        if not offset <= int(turn_index) < offset + len(group):
                            continue
                        ref = f'dialogue_turns.{int(turn_index) - offset}.{field}'
                    elif (ref == 'action' and group_index != 0) or (
                            ref == 'reaction' and group_index != len(groups) - 1):
                        continue
                    part['state_changes'].append(change | {'action_ref': ref})
                    state[change['key']] = change['to']
                part['end_state'] = copy.deepcopy(state)
                prepared.append(part)
                new_index += 1
            old_index += 1
        scene['shots'] = prepared
    for field in ('sfx', 'holds'):
        for cue in audio.get(field, []):
            cue['shot_index'] = positions.get(cue['shot_index'], cue['shot_index'])
    return plan
