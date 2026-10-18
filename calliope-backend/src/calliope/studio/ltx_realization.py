import copy
from itertools import groupby


def action_sequence(shot):
    actions = [shot.get('action', '')]
    actions += [turn.get(field, '') for turn in shot.get('dialogue_turns', [])
                for field in ('action', 'reaction')]
    actions.append(shot.get('reaction', ''))
    return list(dict.fromkeys(value.strip() for value in actions if value and value.strip()))


def prepare_ltx_plan(plan):
    plan = copy.deepcopy(plan)
    audio = plan.get('audio') or {}
    positions, old_index, new_index = {}, 0, 0
    for scene in plan['scenes']:
        prepared = []
        for shot in scene['shots']:
            positions[old_index] = [(new_index, 0.0)]
            turns = shot.get('dialogue_turns') or []
            groups = [list(group) for _, group in groupby(enumerate(turns), lambda pair: pair[1]['speaker'])]
            split = len(groups) > 1 and shot.get('purpose') not in ('midroll', 'outro')
            if plan.get('post_dub') or shot.get('post_dub'):
                # Le post-doublage porte les voix : on garde le plan long,
                # sans couper à chaque changement de locuteur.
                split = False
            if split and (
                shot.get('screen_text')
                or (
                    'state_changes' not in shot
                    and shot.get('start_state') != shot.get('end_state')
                )
            ):
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
            positions[old_index] = []
            elapsed = 0.0
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
                scoped = ' '.join(action_sequence(part))
                part.update(description=scoped or part['dialogue'], prompt=scoped)
                positions[old_index].append((new_index, elapsed))
                elapsed += part['duration_sec']
                prepared.append(part)
                new_index += 1
            old_index += 1
        scene['shots'] = prepared
    for field in ('sfx', 'holds'):
        for cue in audio.get(field, []):
            parts = positions.get(cue['shot_index'])
            if parts:
                at_sec = cue.get('at_sec', 0)
                index, start = next(
                    (part for part in reversed(parts) if part[1] <= at_sec), parts[0]
                )
                cue['shot_index'] = index
                cue['at_sec'] = round(at_sec - start, 6)
    return plan
