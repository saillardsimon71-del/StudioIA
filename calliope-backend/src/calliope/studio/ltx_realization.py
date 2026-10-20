import copy
import re
import unicodedata
from itertools import groupby


def action_sequence(shot):
    actions = [shot.get('action', '')]
    actions += [turn.get(field, '') for turn in shot.get('dialogue_turns', [])
                for field in ('action', 'reaction')]
    actions.append(shot.get('reaction', ''))
    return list(dict.fromkeys(value.strip() for value in actions if value and value.strip()))


def split_sentences(value):
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+', str(value or ''))
            if part.strip()]


def fold_text(value):
    folded = unicodedata.normalize('NFKD', str(value or ''))
    folded = ''.join(char for char in folded if not unicodedata.combining(char))
    return folded.casefold()


_STOPWORDS = frozenset("""
a an and are as at be been being but by for from has have he her his in into is it its
of on or she that the their then they this to was were with without
à au aux ce cette ces dans de des du elle en et il ils la le les leur ma mais me mon
ne nos notre on ou par pas plus pour qu que qui sa se ses son sur ta te tes to tu un
une vos votre vous vers y déjà encore puis alors comme si donc car quand
""".split())

# Verbes d'attention/découverte : deux phrases qui font regarder/découvrir le
# même objet au même personnage décrivent le même événement, même reformulées.
_ATTENTION_VERBS = frozenset("""
look looks looking look_at watch watches watching notice notices noticing
discover discovers discovering see sees seeing spot spots spotting
observe observes observing glance glances glancing stare stares staring
peek peeks peeking check checks checking examine examines examining
realise realizes realising realize recognizing recognise recognizes
regarde regardent regarder remarque remarquent remarquer decouvre
decouvrent decouvrir apercoit apercoivent apercevoir observe observent
observer fixe fixent fixer voit voient voir
""".split())


def _stem(word):
    for suffix in ('ing', 'ees', 'es', 'ed', 's'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def content_words(sentence):
    """Mots porteurs de sens, sans accents, au singulier approximatif."""
    words = re.findall(r'[a-z]+', fold_text(sentence))
    return {_stem(word) for word in words
            if word not in _STOPWORDS and len(word) >= 3 and word not in _ATTENTION_VERBS}


def _attention_verbs(sentence):
    words = re.findall(r'[a-z]+', fold_text(sentence))
    return {_stem(word) for word in words if word in _ATTENTION_VERBS}


def _actors(sentence):
    """Noms propres (personnages) mentionnés dans la phrase, accents repliés.

    Détecte les jetons commençant par une majuscule (y compris en début de
    phrase, comme le faisait la version ASCII) ; les mots-outils sont écartés.
    """
    actors = set()
    for token in re.findall(r'\S+', str(sentence or '')):
        clean = token.strip(".,;:!?\"'()«»—…")
        if clean[:1].isupper():
            folded = fold_text(clean)
            if folded and folded not in _STOPWORDS:
                actors.add(folded)
    return actors


def repeated_pair(first, second):
    """Décide si deux descriptions visuelles racontent le même événement.

    Deux règles déterministes, sans LLM, volontairement conservatrices
    (mieux vaut laisser passer un doublon douteux pour la revue humaine que
    rejeter à tort deux événements distincts) :
    - quasi-identité : les mots porteurs de sens se recouvrent (Jaccard >= 0.6) ;
    - même acteur + même OBJET + verbe d'attention des deux côtés : la
      « découverte du compteur » décrite avant puis après une réplique.
    Retourne None ou une raison courte.
    """
    # Des chiffres différents ne sont jamais une répétition : la montée du
    # compteur (000 puis 001) est une progression diégétique, pas un doublon.
    if re.findall(r'\d+', fold_text(first)) != re.findall(r'\d+', fold_text(second)):
        return None
    left, right = content_words(first), content_words(second)
    if not left or not right:
        return None
    overlap = left & right
    if len(overlap) / len(left | right) >= 0.6:
        return 'description presque identique'
    actors = _actors(first) & _actors(second)
    if actors and _attention_verbs(first) and _attention_verbs(second):
        names = {fold_text(actor) for actor in actors}
        objects = (left - names) & (right - names)
        if objects:
            return 'même personnage attentif au même objet'
    return None


def find_repeated_events(actions):
    """Signale les événements décrits plusieurs fois dans une prise.

    ``actions`` est la liste des textes visuels d'une prise (chronologie,
    action, gestes par réplique). Retourne la liste des paires
    ``(index_a, index_b, raison)`` dans l'ordre de détection. La conservation
    littérale de chaque texte ne garantit pas l'unicité de l'événement :
    un même geste répété à l'écran casse la continuité du pilote.
    """
    sentences = [(index, sentence)
                 for index, action in enumerate(actions)
                 for sentence in split_sentences(action)]
    repeats = []
    for position, (index_a, first) in enumerate(sentences):
        for index_b, second in sentences[position + 1:]:
            reason = repeated_pair(first, second)
            if reason:
                pair = (index_a, index_b, reason)
                if pair not in repeats:
                    repeats.append(pair)
    return repeats


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
