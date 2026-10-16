import re


def authored_story(text, entities):
    if not re.search(r'mot pour mot|r[ée]pliques? exact|word for word|verbatim', text, re.I):
        return None
    matches = list(re.finditer(
        r'(?m)^[ \t]*(?:\*\*)?([^\n:"\u00ab]{1,80}?)[ \t]*:[ \t]*(?:\*\*)?'
        r'[ \t]*["\u00ab]([^\n]+?)["\u00bb][ \t]*(?:\*\*)?[ \t]*$', text))
    if not matches:
        return None
    from calliope.studio.series_planner import canonical_key

    characters = {e['key'] for e in entities if e.get('kind', 'character') == 'character'}
    beats, end = [], 0
    for match in matches:
        speaker = canonical_key(match[1], entities, 'character')
        if speaker not in characters:
            raise ValueError(f'Scénario fourni : locuteur inconnu {match[1]!r}.')
        paragraphs = re.split(r'\n\s*\n', text[end:match.start()].strip())
        action = re.sub(r'(?m)^\s*#{1,6}\s+.*\n?', '', paragraphs[-1]).strip()
        if not action:
            raise ValueError('Scénario fourni : une action doit précéder chaque réplique.')
        beats.append({'action': action,
                      'dialogue_turns': [{'speaker': speaker, 'text': match[2].strip()}]})
        end = match.end()
    tail = re.split(
        r'(?im)^\s*(?:#{1,6}\s*)?'
        r'(?:réalisation|consignes[^\n]*|instructions[^\n]*|règles)\s*:?[ \t]*$',
        text[end:], maxsplit=1)[0].strip()
    if tail:
        beats[-1]['consequence'] = tail
    title = re.search(r'(?im)^\s*(?:#{1,6}\s*)?Titre\s*:\s*(.+)$', text)
    opening, ending = beats[0]['action'], tail or beats[-1]['action']
    return {
        'source_locked': True, 'title': title[1].strip() if title else 'Scénario fourni',
        'beats': beats, 'summary': opening, 'events': [b['action'] for b in beats],
        'ongoing_plot': '', 'narrative_outline': {
            'opening': opening, 'question': opening,
            'progression': '\n'.join(b['action'] for b in beats),
            'payoff': ending, 'ending': ending,
        },
    }
