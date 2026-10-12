import json
import re
import shutil
from pathlib import Path

import httpx

from calliope.studio import service

root = Path(r'AUDIT_WORKSPACE')
state_path = root / 'work/mort-de-honte-revision2-launch.json'
brief_path = root / 'work/mort-de-honte-revision2-brief.json'
client = httpx.Client(base_url='http://127.0.0.1:8247/api/studio', timeout=60)

def api(method, path, data=None):
    response = client.request(method, path, json=data)
    response.raise_for_status()
    return response.json()

def save(value):
    state_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

text = (root / 'outputs/MORT_DE_HONTE_REVISION_2.md').read_text(encoding='utf-8')
episode_text = text.split('## Épisode 1 - Livraison express\n', 1)[1].split('## Raccord avec', 1)[0]
blocks = re.split(r'^### ', episode_text, flags=re.M)[1:]
beats = []
for block in blocks:
    heading, body = block.split('\n', 1)
    if 'Incitation centrale' in heading:
        continue
    turns = [{'speaker': name.lower(), 'text': line} for name, line in re.findall(r'\*\*([A-Z]+) :\*\* "([^"\n]+)"', body)]
    action = re.sub(r'\*\*[A-Z]+ :\*\* "[^"\n]+"', '', body).strip()
    beats.append({'moment': heading, 'action': action, 'dialogue_turns': turns})
assert len(beats) == 5
assert sum(len(beat['dialogue_turns']) for beat in beats) == 22
story = {
    'title': 'Livraison express - Version 2',
    'beats': beats,
    'summary': "Clara livre Enzo assis dans un chariot à Jade. Jade croyait Clara enterrée : Enzo a inventé sa mort pour séduire Jade et lui faire payer des vacances. Les filles exigent un remboursement. Enzo essaie de partir puis de séduire Clara pour briser leur alliance. Elles le photographient et envoient l'annonce de son faux enterrement à sa mère, qui appelle immédiatement.",
    'events': ["Clara arrive avec Enzo réellement assis dans le panier du chariot ; Jade est debout.", "Jade révèle le faux décès de Clara et les vacances payées à Enzo.", "Jade réclame son argent ; Clara empêche Enzo de partir.", "Enzo essaie de séduire Clara ; Jade révèle la même manipulation avant son virement.", "Clara photographie Enzo et envoie la photo à sa mère. Le téléphone sonne ; Clara annonce l'appel sans décrocher."],
    'ongoing_plot': "Le lendemain, les deux filles organisent le faux enterrement d'Enzo. La mère découvre qu'elle aussi a payé pour l'enterrement inventé de Clara. L'appel final du premier épisode n'a pas été décroché à l'écran. Les fleurs du deuxième épisode appartiennent à son décor. L'épisode 2 termine l'arc, mais ne doit pas être préparé ni produit maintenant.",
}
idea = "Réaliser uniquement le scénario approuvé ci-dessous. Conserver exactement les répliques, leurs locuteurs et leur ordre ; préparer leur mise en scène sans réécrire l'histoire. Les horaires sont des repères, pas des pauses à remplir. La première prise est l'échange d'ouverture entre Clara et Jade, environ sept secondes, Enzo silencieux. Les actions se jouent pendant les paroles, avec un débit français vif, intelligible et des réponses immédiates. L'image d'ouverture montre Enzo dans le panier du chariot et Jade debout, pas Enzo dans un fauteuil. Un seul salon pendant tout l'épisode, riche et profond, cadre partagé lisible. Le logiciel insère une seule incitation centrale par Clara après 'Tu bouges pas.' et avant 'Clara, je t'aime. C'est toi que je choisis.'. Ne pas écrire la réplique d'incitation dans les prises narratives. Après cet aparté, reprendre immédiatement la même confrontation. Clara sort son téléphone seulement pour la photo finale. Pas de bouquet, pas de sac de linge, pas de téléphone graphique ajouté. La sonnerie finale n'est pas une voix : aucun interlocuteur supplémentaire. La photo est prise puis effectivement envoyée. La mère appelle, mais personne ne décroche. Durée cible une minute, sans silence de remplissage.\n\n" + json.dumps(story, ensure_ascii=False, indent=2)
payload = {'idea': idea, 'objective': "Épisode 1 retravaillé uniquement. Faire comprendre le mensonge immédiatement, montrer deux tactiques échouées d'Enzo, concrétiser la vengeance par la photo envoyée et l'appel de sa mère. Jeu rapide et intelligible, sans attente entre les échanges. Une seule incitation centrale rapide par Clara.", 'request_id': 'mort-de-honte-revision2-20261012', 'prepare_only': True}
if state_path.exists():
    state = json.loads(state_path.read_text(encoding='utf-8'))
else:
    old = api('GET', '/series/18')
    fields = ('concept', 'aspect_ratio', 'language', 'target_seconds', 'bible', 'directing_profile')
    series_payload = {key: old[key] for key in fields}
    series_payload['title'] = 'Mort de honte - Version retravaillée'
    series_payload['bible']['continuity'] = "Mêmes visages, vêtements et voix. Ouverture : Enzo assis dans le panier métallique du chariot, jambes repliées, Clara derrière sa poignée à gauche, Jade debout à droite face à eux. Enzo tente de se redresser pour partir ; Clara bloque son passage et il reste auprès du chariot. Pas de bouquet ni de sac de linge dans cette version. Clara sort son téléphone uniquement pour la photo finale, puis l'envoie. Le téléphone sonne ; l'appel de sa mère n'est pas décroché. Le seul aparté central s'adresse au spectateur, sans téléphone incrusté. Retour immédiat à l'action quittée. Aucune outro."
    series_payload['bible']['ongoing_plot'] = story['ongoing_plot']
    created = api('POST', '/series', series_payload)
    state = {'series_id': created['id'], 'title': created['title'], 'source_project_id': 27, 'status': 'series_created'}
    save(state)
    with service.database() as conn:
        source = conn.execute('SELECT * FROM studio_entities WHERE series_id=? ORDER BY id', (18,)).fetchall()
        for row in source:
            reference = Path(row['reference_path'])
            assert reference.is_file()
            columns = ('entity_key', 'kind', 'name', 'description', 'identity_json', 'reference_request_json')
            cursor = conn.execute('INSERT INTO studio_entities(series_id,' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in range(len(columns) + 1)) + ')', (created['id'], *(row[key] for key in columns)))
            destination = reference.parents[2] / str(created['id']) / 'references' / f'entity-{cursor.lastrowid}{reference.suffix}'
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(reference, destination)
            conn.execute('UPDATE studio_entities SET reference_path=? WHERE id=?', (str(destination), cursor.lastrowid))
        conn.commit()
    state['status'] = 'references_reused'
    save(state)
    brief_path.write_text(json.dumps({'series': series_payload, 'episode': payload, 'expected_story': story}, ensure_ascii=False, indent=2), encoding='utf-8')
if 'project_id' not in state:
    result = api('POST', f"/series/{state['series_id']}/episodes", payload)
    state.update(result, status='preparing', url=f"http://127.0.0.1:8247/studio/{result['project_id']}")
    save(state)
board = api('GET', f"/projects/{state['project_id']}")
print(json.dumps(state | {'production': board['production']}, ensure_ascii=False))
