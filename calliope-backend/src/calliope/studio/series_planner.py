import copy
import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone

from calliope.agent.llm import generate_structured as _generate_structured
from calliope.config import settings
from calliope.studio.audio import AudioSettings
from calliope.studio.director_contracts import MAX_SPEECH_WORDS_PER_SEC, speech_word_count
from calliope.studio.h3_prompt import (
    LANGUAGES,
    dialogue_language,
    directing_profile,
    validate_h3_prose,
)
from calliope.studio.series import BIBLE_FIELDS

logger = logging.getLogger(__name__)


async def generate_structured(messages, *, role=None, **options):
    """Use the Studio writing service for every story/script/planning phase.

    Agent role assignments are independent of the Studio's chosen writer and
    should never silently override it.
    """
    from calliope.studio.director_contracts import ROLE

    messages = copy.deepcopy(messages)
    if messages and messages[0]['role'] == 'system' and not messages[0]['content'].startswith(ROLE):
        messages[0]['content'] = ROLE + '\n' + messages[0]['content']
    for message in messages:
        for key in [settings.llm_api_key, settings.studio_image_api_key] + [p.get('api_key') for p in settings.llm_profiles]:
            if key and isinstance(message.get('content'), str):
                message['content'] = message['content'].replace(key, '[secret masque]')
    deepseek = settings.llm_base_url.rstrip("/").endswith("api.deepseek.com")
    return await _generate_structured(
        messages, role=None,
        extra_body={"thinking": {"type": "disabled"}} if deepseek else None,
        **options,
    )

MIDROLL_SECONDS = 4.0
FRENCH_MIDROLL = "Abonne-toi, copie le lien et commente GO si tu veux recevoir un guide pour faire des vidéos gratuitement."
MIDROLL_DIALOGUES = {
    "en": "Subscribe, copy the link and comment GO!",
    "fr": "Abonne-toi, copie le lien et commente GO !",
    "ar": "اشترك، انسخ الرابط واكتب GO في التعليقات!",
    "zh": "关注、复制链接并评论GO！",
    "de": "Abonniere, kopiere den Link und kommentiere GO!",
    "it": "Iscriviti, copia il link e commenta GO!",
    "ja": "登録して、リンクをコピーして、GOとコメントしてね！",
    "ko": "구독하고, 링크를 복사하고, GO라고 댓글을 남겨!",
    "pt": "Inscreva-se, copie o link e comente GO!",
    "ru": "Подпишись, скопируй ссылку и напиши GO в комментариях!",
    "es": "Suscríbete, copia el enlace y comenta GO!",
}
MIDROLL_LABELS = {
    "en": ("Subscribe", "Copy the link", "Comment GO"),
    "fr": ("Abonne-toi", "Copie le lien", "Commente GO"),
    "ar": ("اشترك", "انسخ الرابط", "اكتب GO"),
    "zh": ("关注", "复制链接", "评论GO"),
    "de": ("Abonnieren", "Link kopieren", "GO kommentieren"),
    "it": ("Iscriviti", "Copia il link", "Commenta GO"),
    "ja": ("登録してね", "リンクをコピー", "GOとコメント"),
    "ko": ("구독", "링크 복사", "GO 댓글"),
    "pt": ("Inscreva-se", "Copie o link", "Comente GO"),
    "ru": ("Подпишись", "Скопируй ссылку", "Напиши GO"),
    "es": ("Suscríbete", "Copia el enlace", "Comenta GO"),
}


def _native_key(value):
    if not isinstance(value, str):
        return value
    return "".join(c for c in unicodedata.normalize("NFKD", value.strip().casefold())
                   if not unicodedata.combining(c))


def _normalize_native_keys(draft):
    result = copy.deepcopy(draft)

    def key(value):
        return _native_key(value)

    def keyed(value, path):
        if not isinstance(value, dict):
            return value
        normalized = {key(k): v for k, v in value.items()}
        if len(normalized) != len(value):
            raise ValueError(f"{path}: collision entre identifiants après normalisation.")
        return normalized

    entities = result.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                entity["key"] = key(entity.get("key"))
                if isinstance(entity.get("identity"), dict) and "variant_of" in entity["identity"]:
                    entity["identity"]["variant_of"] = key(entity["identity"]["variant_of"])
    canon = result.get("render_canon")
    if isinstance(canon, dict):
        for kind in ("characters", "locations", "props"):
            if kind in canon:
                canon[kind] = keyed(canon[kind], f"render_canon.{kind}")
    scenes = result.get("scenes")
    for scene in scenes if isinstance(scenes, list) else []:
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
            continue
        for shot in scene["shots"]:
            if not isinstance(shot, dict):
                continue
            for field in ("speaker", "location_key"):
                if field in shot:
                    shot[field] = key(shot[field])
            for field in ("character_keys", "listener_keys", "prop_keys"):
                if isinstance(shot.get(field), list):
                    shot[field] = [key(value) for value in shot[field]]
            for field in ("start_state", "end_state"):
                if field in shot:
                    shot[field] = keyed(shot[field], field)
            if isinstance(shot.get("state_changes"), list):
                for change in shot["state_changes"]:
                    if isinstance(change, dict) and "key" in change:
                        change["key"] = key(change["key"])
            if isinstance(shot.get("dialogue_turns"), list):
                for turn in shot["dialogue_turns"]:
                    if isinstance(turn, dict) and "speaker" in turn:
                        turn["speaker"] = key(turn["speaker"])
            if isinstance(shot.get("screen_text"), list):
                for label in shot["screen_text"]:
                    if isinstance(label, dict) and "entity_key" in label:
                        label["entity_key"] = key(label["entity_key"])
    return result


class _NativeValidationError(ValueError):
    def __init__(self, message, paths):
        super().__init__(message)
        self.paths = paths


class _StoryRealizationError(_NativeValidationError):
    pass


# When dialogue counts diverge this much, there is no reliable scene-level
# destination for a "REPAIR VALUES ONLY" request. Retrying would risk
# overwriting valid scenes and spending further model calls.
MAX_TARGETED_DIALOGUE_COUNT_DRIFT = 2


class _UnrecoverableStoryRealizationError(_StoryRealizationError):
    """A large structural loss/gain cannot be repaired by local field updates."""


class _NarrativeDurationError(_NativeValidationError):
    pass


def _native_repair_fields(draft, paths):
    fields = []
    for path in paths:
        node, selected, present = draft, [], True
        for key in path:
            selected.append(key)
            if isinstance(node, dict) and isinstance(key, str):
                if key not in node:
                    node, present = None, False
                    break
                node = node[key]
            elif isinstance(node, list) and type(key) is int and 0 <= key < len(node):
                node = node[key]
            else:
                raise ValueError("Champ de réparation sans destination valide dans le brouillon.")
        if any(selected[:len(field["path"])] == field["path"] for field in fields):
            continue
        fields = [field for field in fields if field["path"][:len(selected)] != selected]
        fields.append({"path": selected, "present": present, "current_value": copy.deepcopy(node)})
    return [{"id": f"field_{index + 1}", **field} for index, field in enumerate(fields)]


def _apply_native_values(draft, paths, response):
    expected = {f"field_{index + 1}" for index in range(len(paths))}
    values = response.get("values") if isinstance(response, dict) else None
    if (not expected or not isinstance(values, dict) or set(values) != expected
            or set(response) != {"values"}):
        raise ValueError("values: fournir exactement les identifiants demandés, sans chemins ni autres modifications.")
    return _apply_native_updates(draft, {"updates": [
        {"path": path, "value": values[f"field_{index + 1}"]} for index, path in enumerate(paths)
    ]})


def _apply_native_updates(draft, response):
    updates = response.get("updates") if isinstance(response, dict) else None
    if not isinstance(updates, list) or not updates:
        raise ValueError("updates: liste de corrections ciblées requise, pas un nouveau scénario complet.")
    result = copy.deepcopy(draft)
    for index, update in enumerate(updates):
        path = update.get("path") if isinstance(update, dict) else None
        if not isinstance(path, list) or not path or "value" not in update:
            raise ValueError(f"updates[{index}]: path non vide et value requis.")
        node = result
        for offset, key in enumerate(path):
            last = offset == len(path) - 1
            valid = (
                isinstance(node, dict) and isinstance(key, str) and (last or key in node)
            ) or (
                isinstance(node, list) and type(key) is int and 0 <= key < len(node)
            )
            if not valid:
                raise ValueError(f"updates[{index}].path: chemin invalide {json.dumps(path)}.")
            if last:
                node[key] = copy.deepcopy(update["value"])
            else:
                node = node[key]
    return result


def _creative_context(context):
    result = copy.deepcopy(context)
    rhythm = {key: value for key, value in (context.get('directing_profile') or {}).items()
              if key in ('dialogue_density', 'reactions', 'active_listening', 'silences')}
    if rhythm:
        result['narrative_rhythm'] = rhythm
    for key in ('id', 'created_at', 'updated_at', 'archived', 'aspect_ratio',
                'directing_profile', 'speaker_ids', '_planner_trace', 'prepare_only', 'prompt_contract_version',
                'creative_candidates'):
        result.pop(key, None)
    for entity in result.get('entities', []):
        for key in entity.keys() - {'key', 'kind', 'name', 'description', 'identity'}:
            entity.pop(key)
        if isinstance(entity.get('identity'), dict):
            for key in entity['identity'].keys() - {'visual_description', 'variant_of', 'attached_value', 'label_key'}:
                entity['identity'].pop(key)
    return result


async def _develop_creative_story(context, idea, objective, trace=None):
    from calliope.studio.authored_script import authored_story

    supplied = authored_story(idea, context.get('entities', []))
    if supplied:
        return supplied, {'source': 'operator_script', 'candidates': []}
    prompt = """Tu écris des scènes de fiction courtes, pas des résumés de concepts.
Ecris trois petites scènes différentes pour le brief ci-dessous. Fais parler les
personnages comme des gens qui se connaissent, se désirent, se jalousent ou se
mentent. Ils savent où appuyer pour blesser, faire céder ou sauver la face.
Leurs réponses rebondissent sur les mots de l'autre et les détails de leur histoire,
avec leur vocabulaire, leur mauvaise foi et leurs contradictions. On découvre leur problème dans l'action,
sans préambule. Fais arriver quelque chose : leurs décisions aggravent ou règlent
ce problème. La dernière action ou réplique donne sa saveur à la scène.
Le canon fixe les personnes et le monde, pas la prochaine intrigue. idea et
objective priment sur les suggestions de series_intent ; conserve leurs demandes
explicites. Le brief fixe le genre. Dans une comédie franchement absurde, joue une
énormité avec les émotions les plus quotidiennes. Dans un drame, respecte le drame.
Les actions sont faciles à visualiser, les paroles faciles à comprendre à l'oral.

Voici deux fragments de démonstration inventés, extérieurs à la série. Ils ne sont
ni des épisodes à adapter, ni un catalogue d'objets ou de répliques à réutiliser :

COMEDIE : Un homme en slip est pendu au lustre. Sa compagne tient l'échelle.
"Tu descends ou j'appelle ta mère !" "C'est elle qui m'a monté !" La mère
reprend l'échelle : "Il fallait passer l'aspirateur dessous." La compagne attrape
l'aspirateur et le tend à la mère : "Alors maintenant, échangez."
La folie est visible avant l'explication. Chacun défend son intérêt, et la dernière
action retourne contre la mère sa propre justification. Pas de morale familiale.

SUSPENSE : Une femme barricade la porte. Son frère frappe dehors : "Ouvre !"
Elle regarde son frère endormi sur le canapé. "Tu es déjà rentré." Le dormeur
ouvre les yeux : "Ne lui dis pas que je suis là." Elle serre la clé dans sa main.
La menace vient d'un fait immédiatement compréhensible, pas du mot "mystérieux".

Cherche tes propres situations. Aucun accessoire, effet ou retournement de ces
fragments n'est imposé. Aucun gag ni cliffhanger obligatoire. Ne commente pas la
qualité de tes scènes, joue-les. Chaque piste est un épisode complet adapté au
target_seconds fourni, avec ses échanges réellement prononcés et sa fin jouée.
Ce n'est pas un petit gag que le rédacteur devra étirer ensuite. Le temps indiqué
est celui du récit. N'ajoute aucune incitation promotionnelle. N'allonge pas les pauses
pour remplir : développe ce que les personnages tentent et ce qui leur arrive.
Retourne uniquement un JSON {"candidates":[{"id":"A","title":"...","scene":"..."},
{"id":"B","title":"...","scene":"..."},{"id":"C","title":"...","scene":"..."}]}.
Actions en français. Paroles dans la langue de la série. Pas de caméra ni de prompt
de rendu, pas de métrique, pas de conseil marketing."""
    story_context = _creative_context(context)
    series_intent = {'concept': story_context.pop('concept', '')}
    if isinstance(story_context.get('bible'), dict):
        series_intent['ongoing_plot'] = story_context['bible'].pop('ongoing_plot', '')
    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content':
        json.dumps({'series': story_context, 'series_intent': series_intent,
                    'idea': idea, 'objective': objective}, ensure_ascii=False)}]
    token = uuid.uuid4().hex
    for attempt in range(2):
        request = copy.deepcopy(messages)
        result, error = None, None
        try:
            result = await generate_structured(messages, temperature=0.95, max_tokens=4500, json_mode=True,
                                               required_keys=('candidates',), role='story')
            candidates = result.get('candidates')
            if not isinstance(candidates, list) or not 2 <= len(candidates) <= 5:
                raise ValueError('candidates: plusieurs scènes à comparer sont requises.')
            if any(not isinstance(candidate, dict) or any(
                    not isinstance(candidate.get(field), str) or not candidate[field].strip()
                    for field in ('id', 'title', 'scene')) for candidate in candidates):
                raise ValueError('candidates: chaque scène doit avoir id, title et scene non vides.')
            if len({candidate['id'] for candidate in candidates}) != len(candidates):
                raise ValueError('candidates: identifiants distincts requis pour la sélection.')
            break
        except ValueError as exc:
            error = str(exc)
            if attempt:
                raise
            messages.extend([{'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)},
                             {'role': 'user', 'content': f'Corrige uniquement ce défaut de format : {exc}'}])
        finally:
            if trace:
                directory = settings.assets_dir / str(trace['project_id']) / 'studio' / 'diagnostics'
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"creative-candidates-{trace['job_id']}-{token}-attempt-{attempt + 1}.json").write_text(
                    json.dumps({'messages': request, 'response': result, 'validation_error': error},
                               ensure_ascii=False, indent=2), encoding='utf-8')
    story = await _write_creative_story(context | {'creative_candidates': candidates}, idea, objective, trace)
    selection = story.pop('editorial_choice')
    return story, {'candidates': candidates, 'selection': selection}


async def _write_creative_story(context, idea, objective, trace=None, *, previous_story=None, revision_note=None):
    from calliope.studio.planner import WORDS_PER_SEC

    profile = context.get('directing_profile') or {}
    density = profile.get('dialogue_density',
                          'brisk' if profile.get('preset') == 'fast_dialogue_social_fiction' else 'natural')
    spoken_rate = 3.0 if density == 'brisk' else WORDS_PER_SEC
    prompt = """CREATIVE STORY : écris une scène de série courte qui mérite d'être regardée.
Tu es son auteur. Trouve une vraie idée et fais vivre les personnages. idea et
objective sont la demande actuelle, prioritaire sur les pistes de series_intent.
Les exemples et intrigues proposés dans series_intent peuvent être réinventés.
series contient le canon ; seuls ses événements antérieurs sont déjà arrivés.
Invente librement les situations et les mots, en conservant les
personnages, leurs relations, les faits antérieurs et les règles de leur univers.
Respecte les événements, conditions de départ et répliques que l'utilisateur
exige explicitement. Une condition de départ imposée existe dès l'ouverture.
Fais exister toutes les séquences et tous les lieux explicitement demandés dans
idea : ne réduis pas un épisode avec des préparatifs et un rendez-vous à sa seule
dispute d'ouverture. Développe les actions et les conséquences utiles à la durée.
Si previous_story et revision_note sont fournis, tu reprends ton propre brouillon
pour corriger ce défaut. Tu restes l'auteur des ajouts et des dialogues ; respecte
le brief actuel, les faits du canon et les paroles imposées par l'utilisateur.

Quelqu'un découvre cette série sans contexte. Dès l'ouverture, un événement
concret et une réplique adressée à un autre personnage lui font comprendre
le problème et lui donnent envie d'entendre la réponse. Choisis un parti fort
adapté au genre demandé. En comédie absurde, confronte des émotions familières
à une situation énorme et visuelle, prise au sérieux par les personnages.
Le brief donne le niveau d'irrévérence, d'intimité ou d'humour cru. Le gag vient
de la situation et des réactions, au-delà d'un décor ou de personnages originaux.

Écris des paroles qu'on pourrait entendre dans une vraie embrouille : chacun
cherche à obtenir quelque chose de l'autre, sauver la face ou toucher son point
sensible. Fais entendre leurs personnalités dans les mots, les esquives, les
détails embarrassants qu'ils connaissent. Une accusation précise blesse plus
qu'une formule passe-partout ; une réponse de mauvaise foi fait avancer le conflit.
Exemple extérieur au canon : à "Tu m'as menti", préfère un détail vécu tel que
"T'avais même pas fini de pleurer que tu m'envoyais ton RIB." Invente tes propres
phrases, sans reprendre cet exemple ni ajouter des insultes pour faire du bruit.
Les décisions ont des conséquences visibles, l'embrouille évolue et aboutit à
une vraie chute ou récompense. Celle-ci peut ouvrir un problème précis pour la
suite. Garde les actions utiles à cette idée, faciles à suivre.
Une scène courte et forte vaut mieux qu'une idée mince étirée pour remplir.
Le temps indiqué est celui du récit. N'ajoute aucune incitation promotionnelle.

Retourne uniquement un objet JSON. Écris d'abord title et beats, la scène jouée :
beats:[{action,dialogue_turns:[{speaker,text}],consequence}]. Un beat est un
événement causal, pas une prise vidéo. Ajoute ensuite summary, events (liste de
faits), ongoing_plot et narrative_outline:{opening,question,progression,payoff,ending}
à partir de cette scène. Les explications et actions sont en français, les mots
prononcés dans la langue de la série. speaker est la clé du personnage.
text contient seulement les mots exacts prononcés, sans didascalies, noms de
locuteurs ni balises H3 ; les indications de jeu vont dans action. Ne produis
pas de prompts d'image, d'états techniques, de recette caméra ou de minutage."""
    candidates = context.get('creative_candidates')
    if candidates:
        prompt += """
Tu es maintenant le rédacteur en chef. Lis les pistes creative_candidates comme
des scènes à regarder, en particulier leurs paroles et leur fin. Compare leur
attrait concret : qu'attend-on après la première action ou phrase ? Les échanges
font-ils évoluer la situation, ou commentent-ils longtemps le même gag ? La chute
récompense-t-elle cette attente ? Les personnages ont-ils des réponses propres,
orales, surprenantes, ou des répliques génériques ? Lis l'échange à voix haute
mentalement : entend-on quelqu'un qui tente quelque chose, ou un auteur qui
explique l'intrigue ? Réécris les phrases interchangeables à partir de ce que
ce personnage reproche précisément à l'autre. Juge selon le genre du brief.

Retiens la piste la plus forte et réécris sa faiblesse principale. Tu peux
réinventer une réponse ou une action pour améliorer cette piste, en conservant
les éléments explicitement imposés. Evite de mélanger toutes les idées.
Les pistes sont des brouillons, pas des commandes : aucune de leurs répliques
n'est imposée. Tu peux les remplacer et les raccourcir librement. Seules les
demandes explicites de l'utilisateur dans idea et objective sont imposées ;
les faits du canon restent vrais. Tes propres versions précédentes sont aussi
des brouillons, pas des exigences supplémentaires.
Pas de chronologie universelle ni de quotas de surprises : écris la scène qui
mérite d'être regardée jusqu'à sa fin. Ne promet pas de résultat d'audience.
Ne défends pas une piste faible parce qu'il faut en choisir une. Si les pistes
commentent le même petit gag, prends la meilleure comme point de départ et
réinvente vraiment sa scène. Coupe son installation calme. Mets le problème
dans sa première image et ses premiers mots. Développe ce qui arrive ensuite,
avec des paroles qui répondent à ce qui vient d'être dit. La chute se voit,
son action est compréhensible ; un jeu de mots ou "tu verras" ne la remplace pas.
Ajoute editorial_choice:{candidate_id,reason,revision} : id de la piste retenue,
raison concrète du choix et faiblesse traitée dans la scène finale. Ces notes
éditoriales ne sont ni des paroles ni des instructions de rendu.
"""
    if "target_seconds" in context:
        target = float(context["target_seconds"])
        prompt += (
            f"\nTemps utile du récit : {target:g} secondes, actions et réactions incluses. "
            f"La capacité indicative avant les actions est d'environ {int(target * spoken_rate)} mots prononcés au total, "
            "pas par personnage ou événement. Vise en dessous pour laisser jouer la scène. "
            "Choisis une scène compacte avec des échanges vifs et laisse de la place aux gestes utiles. "
            "Conserve les répliques imposées ; coupe le remplissage plutôt que leur sens."
        )
    story_context = _creative_context(context)
    series_intent = {"concept": story_context.pop("concept", "")}
    if isinstance(story_context.get("bible"), dict):
        series_intent["ongoing_plot"] = story_context["bible"].pop("ongoing_plot", "")
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content":
        json.dumps({"series": story_context, "series_intent": series_intent,
                    "idea": idea, "objective": objective,
                    **({'creative_candidates': candidates} if candidates else {}),
                    **({'previous_story': previous_story, 'revision_note': revision_note}
                       if previous_story is not None else {})}, ensure_ascii=False)}]
    token = uuid.uuid4().hex
    for attempt in range(3):
        request = copy.deepcopy(messages)
        result = await generate_structured(messages, temperature=0.9, max_tokens=4500,
                                           required_keys=("title", "narrative_outline", "beats")
                                           + (('editorial_choice',) if candidates else ()), role='script')
        error = None
        try:
            if not isinstance(result, dict) or not isinstance(result.get("title"), str) or not result["title"].strip():
                raise ValueError("creative_story.title: titre requis.")
            if candidates:
                selection = result.get('editorial_choice')
                if (not isinstance(selection, dict)
                        or not isinstance(selection.get('candidate_id'), str)
                        or selection.get('candidate_id') not in {candidate['id'] for candidate in candidates}
                        or any(not isinstance(selection.get(field), str) or not selection[field].strip()
                               for field in ('reason', 'revision'))):
                    raise ValueError('editorial_choice: sélectionner une piste existante et expliquer le choix et la révision.')
            for field in ("summary", "ongoing_plot"):
                if field in result and not isinstance(result[field], str):
                    raise ValueError(f"creative_story.{field}: texte requis.")
            if "events" in result and (not isinstance(result["events"], list)
                    or any(not isinstance(event, str) for event in result["events"])):
                raise ValueError("creative_story.events: liste de faits textuels requise.")
            outline = result.get("narrative_outline")
            if not isinstance(outline, dict) or any(not isinstance(outline.get(k), str) or not outline[k].strip()
                    for k in ("opening", "question", "progression", "payoff", "ending")):
                raise ValueError("creative_story.narrative_outline: progression narrative requise.")
            beats = result.get("beats")
            if not isinstance(beats, list) or not beats or len(beats) > 100:
                raise ValueError("creative_story.beats: événements causaux requis.")
            for index, beat in enumerate(beats):
                if not isinstance(beat, dict) or not isinstance(beat.get("action"), str) or not beat["action"].strip():
                    raise ValueError(f"creative_story.beats[{index}].action: événement requis.")
                turns = beat.get("dialogue_turns")
                if not isinstance(turns, list) or len(turns) > 30:
                    raise ValueError(f"creative_story.beats[{index}].dialogue_turns: liste requise.")
                for turn in turns:
                    if not isinstance(turn, dict) or any(not isinstance(turn.get(k), str) or not turn[k].strip()
                                                       for k in ("speaker", "text")):
                        raise ValueError(f"creative_story.beats[{index}]: réplique attribuée requise.")
                    turn["speaker"] = _native_key(turn["speaker"])
                    if not re.fullmatch(r"[a-z0-9_-]{1,80}", turn["speaker"]):
                        raise ValueError(f"creative_story.beats[{index}]: clé de locuteur invalide.")
                    if re.match(r"\s*[\[(]", turn["text"]):
                        raise ValueError(f"creative_story.beats[{index}]: déplacer la didascalie hors des paroles.")
                    validate_h3_prose(turn["text"])
            if "target_seconds" in context:
                words = sum(len(turn["text"].split()) for beat in beats for turn in beat["dialogue_turns"])
                target = float(context["target_seconds"])
                if words / spoken_rate > target + max(2.0, target * 0.1):
                    raise ValueError(
                        f"creative_story: {words} mots prononcés, durée de parole estimée {words / spoken_rate:.1f} s "
                        f"pour {target:g} s disponibles, avant les actions et réactions. "
                        f"Vise moins de {int(target * spoken_rate)} mots prononcés au total. "
                        "Raccourcir les répliques sans supprimer les lignes imposées ni la récompense narrative."
                    )
            return result
        except ValueError as exc:
            error = str(exc)
            if attempt == 2:
                raise
            messages.extend([{"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                             {"role": "user", "content": f"Repair this story JSON: {exc}"}])
        finally:
            if trace:
                directory = settings.assets_dir / str(trace["project_id"]) / "studio" / "diagnostics"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"creative-story-{trace['job_id']}-{token}-attempt-{attempt + 1}.json").write_text(
                    json.dumps({"messages": request, "response": result, "validation_error": error},
                               ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_story_realization(story, plan, draft=None):
    expected = [{"speaker": t["speaker"], "text": t["text"].strip()}
                for beat in story["beats"] for t in beat["dialogue_turns"]]
    actual, origins = [], []
    previous_beat = -1
    authored_lines = [[] for _ in story['beats']]
    invalid_indices = []
    for si, scene in enumerate(plan["scenes"]):
        for ti, shot in enumerate(scene["shots"]):
            if shot.get("purpose") in ("midroll", "outro"):
                continue
            turns = shot.get("dialogue_turns") or (
                [{"speaker": shot["speaker"], "text": shot["dialogue"]}] if shot.get("dialogue") else [])
            if story.get('source_locked'):
                beat_index = shot.get('source_beat_index')
                if (type(beat_index) is not int or not previous_beat <= beat_index < len(story['beats'])
                        or beat_index < 0 or any(
                            {'speaker': t['speaker'], 'text': t['text'].strip()}
                            not in story['beats'][beat_index]['dialogue_turns'] for t in turns)):
                    invalid_indices.append(['scenes', si, 'shots', ti, 'source_beat_index'])
                else:
                    authored_lines[beat_index].extend(
                        {'speaker': t['speaker'], 'text': t['text'].strip()} for t in turns)
                    previous_beat = beat_index
            actual.extend({"speaker": t["speaker"], "text": t["text"].strip()} for t in turns)
            raw = (draft or plan)["scenes"][si]["shots"][ti]
            for index in range(len(turns)):
                path = ["scenes", si, "shots", ti]
                origins.append(path + ["dialogue_turns", index] if raw.get("dialogue_turns") else path)
    # Detect large dialogue loss before source-locked validation, which
    # otherwise exposes only the surviving scene paths as repair targets.
    drift = abs(len(expected) - len(actual))
    if drift > MAX_TARGETED_DIALOGUE_COUNT_DRIFT:
        raise _UnrecoverableStoryRealizationError(
            f"Arrêt sans réparation automatique : {drift} répliques manquantes "
            "ou supplémentaires dans la réalisation, aucune destination locale fiable.",
            [],
        )
    if invalid_indices:
        raise _NativeValidationError(
            'source_beat_index: conserver les étapes du scénario fourni, sans les fusionner ni déplacer leurs répliques. '
            'Corriger tous les indices signalés ensemble : entiers à partir de zéro dans creative_story.beats ; '
            'une suite silencieuse conserve son étape source.', invalid_indices)
    if story.get('source_locked') and any(
            lines != beat['dialogue_turns'] for lines, beat in zip(authored_lines, story['beats'])):
        raise _NativeValidationError(
            'Scénario fourni : une étape ou une réplique a été omise ou dupliquée.',
            [['scenes', i, 'shots'] for i in range(len(plan['scenes']))])
    if actual != expected:
        message = ("creative_story: la préparation doit conserver les répliques, leur ordre et leurs locuteurs. "
                   f"Attendu {json.dumps(expected, ensure_ascii=False)}; reçu {json.dumps(actual, ensure_ascii=False)}.")
        if len(actual) != len(expected):
            raise _StoryRealizationError(
                message, [['scenes', i, 'shots'] for i in range(len(plan['scenes']))]
            )
        paths = []
        for wanted, received, path in zip(expected, actual, origins):
            for field in ("speaker", "text"):
                if wanted[field] != received[field]:
                    paths.append(path + ["dialogue" if field == "text" and path[-2] == "shots" else field])
        raise _StoryRealizationError(message, paths)


def _validate_initial_states(plan):
    available, previous = set(), None
    errors, paths = [], []
    canon = plan.get("render_canon")
    if not isinstance(canon, dict):
        raise _NativeValidationError("render_canon: dictionnaire requis.", [["render_canon"]])
    for field in ("characters", "locations"):
        if not isinstance(canon.get(field), dict):
            raise _NativeValidationError(f"render_canon.{field}: dictionnaire requis.", [["render_canon", field]])
    for key, entry in canon["characters"].items():
        if not isinstance(entry, dict) or any(not isinstance(entry.get(field), str) or not entry[field].strip()
                                             for field in ("name", "appearance", "voice")):
            raise _NativeValidationError(f"render_canon.characters[{key}]: identité et voix requises.", [["render_canon", "characters", key]])
    declared = canon.get("props")
    if not isinstance(declared, dict):
        raise _NativeValidationError("render_canon.props: dictionnaire requis.", [["render_canon", "props"]])
    invalid_props = [key for key, value in declared.items() if not isinstance(value, str) or not value.strip()]
    if invalid_props:
        raise _NativeValidationError(
            "render_canon.props: chaque description doit être un texte anglais non vide, "
            f"pas un objet name/description/identity. Objets à corriger : {json.dumps(invalid_props)}.",
            [["render_canon", "props", key] for key in invalid_props],
        )
    for si, scene in enumerate(plan.get("scenes", [])):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list) or not scene["shots"]:
            raise _NativeValidationError(f"scenes[{si}]: scène et liste de plans requises.", [["scenes", si]])
        for ti, shot in enumerate(scene["shots"]):
            if not isinstance(shot, dict):
                raise _NativeValidationError(f"scenes[{si}].shots[{ti}]: plan invalide.", [["scenes", si, "shots", ti]])
            if shot.get("purpose") in ("midroll", "outro"):
                continue
            target = ["scenes", si, "shots", ti]
            label = f"scenes[{si}].shots[{ti}]"
            turns = shot.get("dialogue_turns") or []
            if not isinstance(turns, list) or any(not isinstance(turn, dict) for turn in turns):
                raise _NativeValidationError(f"{label}.dialogue_turns: liste de tours de parole requise.", [target + ["dialogue_turns"]])
            if "state_changes" not in shot:
                end = shot.get("end_state")
                available = set(end) if isinstance(end, dict) else set()
                previous = shot
                continue
            start = shot.get("start_state", {})
            if not isinstance(start, dict):
                raise _NativeValidationError(f"{label}.start_state: dictionnaire requis.", [target + ["start_state"]])
            props = shot.get("prop_keys")
            if not isinstance(props, list) or any(not isinstance(key, str) for key in props):
                raise _NativeValidationError(f"{label}.prop_keys: liste de clés textuelles requise.", [target + ["prop_keys"]])
            if isinstance(declared, dict) and any(key not in declared for key in props):
                raise _NativeValidationError(f"{label}.prop_keys: objets non déclarés.", [target + ["prop_keys"]])
            if not shot.get("continue_from_previous"):
                available = set(start)
            elif previous is None:
                raise _NativeValidationError(f"{label}.continue_from_previous: aucun plan narratif précédent.", [target + ["continue_from_previous"]])
            else:
                available.update(start)
            for key in props:
                if key not in available:
                    errors.append(f"{label}.start_state[{key}]: nouvel objet sans état initial.")
                    paths.append(target + ["start_state", key])
                    available.add(key)
            previous = shot
    if errors:
        raise _NativeValidationError("\n".join(errors), paths)


def _resolve_native_states(plan):
    _validate_initial_states(plan)
    previous = None
    errors, error_paths = [], []
    for si, scene in enumerate(plan.get("scenes", [])):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
            continue
        for ti, shot in enumerate(scene["shots"]):
            if not isinstance(shot, dict) or shot.get("purpose") in ("midroll", "outro"):
                continue
            path = f"scenes[{si}].shots[{ti}]"
            target = ["scenes", si, "shots", ti]
            if "state_changes" not in shot:
                previous = shot
                continue
            start = shot.get("start_state", {})
            if not isinstance(start, dict):
                raise _NativeValidationError(f"{path}.start_state: dictionnaire requis.", [target + ["start_state"]])
            start = copy.deepcopy(start)
            if shot.get("continue_from_previous"):
                if previous is None:
                    raise _NativeValidationError(f"{path}.continue_from_previous: aucun plan narratif précédent.", [target + ["continue_from_previous"]])
                inherited = previous.get("end_state", {})
                if not isinstance(inherited, dict):
                    raise ValueError(f"{path}.start_state: état de fin précédent invalide.")
                for key, value in inherited.items():
                    if key in start and start[key] != value:
                        errors.append(f"{path}.start_state[{key}]: contradiction explicite; "
                                      f"attendu {json.dumps(value)}, reçu {json.dumps(start[key])}.")
                        error_paths.append(target + ["start_state", key])
                start = start | copy.deepcopy(inherited)
            elif previous is not None and not str(shot.get("cut_reason") or "").strip():
                raise _NativeValidationError(f"{path}.cut_reason: changement de prise à justifier.", [target + ["cut_reason"]])
            changes = shot["state_changes"]
            props = shot.get("prop_keys")
            if not isinstance(props, list) or any(not isinstance(key, str) for key in props):
                raise _NativeValidationError(f"{path}.prop_keys: liste de clés textuelles requise.", [target + ["prop_keys"]])
            canon = plan.get("render_canon")
            declared = canon.get("props") if isinstance(canon, dict) else None
            if isinstance(declared, dict) and any(key not in declared for key in props):
                raise _NativeValidationError(f"{path}.prop_keys: objets non déclarés.", [target + ["prop_keys"]])
            turns = shot.get("dialogue_turns") or []
            if not isinstance(turns, list) or any(not isinstance(turn, dict) for turn in turns):
                raise _NativeValidationError(f"{path}.dialogue_turns: liste de tours de parole requise.", [target + ["dialogue_turns"]])
            missing = [key for key in props if key not in start]
            if missing:
                raise _NativeValidationError("\n".join(
                    f"{path}.start_state[{key}]: nouvel objet sans état initial." for key in missing),
                    [target + ["start_state", key] for key in missing])
            if not isinstance(changes, list):
                raise _NativeValidationError(f"{path}.state_changes: liste de changements motivés requise.", [target + ["state_changes"]])
            end = copy.deepcopy(start)
            actions = [("action", shot.get("action", ""))]
            for turn_index, turn in enumerate(turns):
                actions.extend([(f"dialogue_turns.{turn_index}.{field}", turn.get(field, ""))
                                for field in ("action", "reaction")])
            actions.append(("reaction", shot.get("reaction", "")))
            last_action = 0
            for index, change in enumerate(changes):
                field = f"{path}.state_changes[{index}]"
                if not isinstance(change, dict) or any(not isinstance(change.get(k), str) or not change[k].strip()
                                                      for k in ("key", "from", "to")):
                    raise _NativeValidationError(f"{field}: key, from, to et référence d'action requis.", [target + ["state_changes", index]])
                key = change["key"]
                if key not in end or end[key] != change["from"]:
                    raise _NativeValidationError(f"{field}.from: état initial incompatible pour {key}; "
                                     f"attendu {json.dumps(end.get(key))}, reçu {json.dumps(change['from'])}.", [target + ["state_changes", index]])
                matching = [i for i, (ref, action) in enumerate(actions)
                            if isinstance(action, str) and action.strip() and i >= last_action
                            and (("action_ref" in change and ref == change["action_ref"])
                                 or ("action_ref" not in change and action == change.get("action")))]
                if not matching:
                    raise _NativeValidationError(f"{field}.action: changement absent des actions du plan ou dans un ordre incompatible.", [target + ["state_changes", index]])
                last_action = matching[0]
                end[key] = change["to"]
            if "end_state" in shot and shot["end_state"] != end:
                errors.append(f"{path}.end_state: contradiction avec les changements motivés.")
                error_paths.append(target + ["end_state"])
            shot.update(start_state=start, end_state=end)
            previous = shot
    if errors:
        raise _NativeValidationError("\n".join(errors), error_paths)


async def prepare_native_states(draft, trace=None):
    prompt = """Compute physical OBJECT continuity only, not story or character states.
Return JSON:
{"initial":{"prop_key":"initial state"},"changes":[{"key":"prop_key",
"to":"new state","action_ref":"action"}],"new_props":{}}
All state values are English. initial is a DIRECT map, without a wrapper. Set
every required_initial_key BEFORE actions. Continuous takes inherit all existing
states unchanged; do not repeat them. Independent takes initialize required props
from initial_image (the still before any action), preserving previous events.
An object already open or held there starts in that state. Track EVERY physical
change through the final action, including closures, power and transfers.
Software calculates from/end_state and orders
changes by the supplied action refs. A change belongs only to a physical object,
never a character, spoken line, reaction, abstract position or action. Use exactly
an allowed_action_ref whose action physically changes that object. Describe its
FULL resulting state in to, including relevant ownership, position, door or power.
Never invent an event. If tracked objects do not change, changes is [].
Track only plot-relevant objects, not decorative items. For a missing important
physical object, declare a stable lowercase ASCII key and immutable English visual
description in new_props, plus its initial state. Never redefine known props or
characters. No character aliases, abstract states, speech or action keys as props.
"""
    result = copy.deepcopy(draft)
    previous = {}
    token = uuid.uuid4().hex
    for si, scene in enumerate(result["scenes"]):
        for ti, shot in enumerate(scene["shots"]):
            if shot.get("purpose") in ("midroll", "outro"):
                continue
            actions = {"action": shot.get("action", "")}
            for index, turn in enumerate(shot.get("dialogue_turns") or []):
                for field in ("action", "reaction"):
                    actions[f"dialogue_turns.{index}.{field}"] = turn.get(field, "")
            actions["reaction"] = shot.get("reaction", "")
            continuous = shot.get("continue_from_previous", False)
            request = {
                "take_index": [si, ti],
                "take": {key: shot.get(key) for key in ("prop_keys", "continue_from_previous")},
                "inherited_state": previous,
                "required_initial_keys": [key for key in shot["prop_keys"] if not continuous or key not in previous],
                "prop_descriptions": result["render_canon"]["props"],
                "location": result["render_canon"]["locations"].get(shot.get("location_key"), ""),
                "characters": {key: value["name"] for key, value in result["render_canon"]["characters"].items()},
                "dialogue_turns": shot.get("dialogue_turns") or [],
                "source_story": result.get("creative_story"),
                "allowed_action_refs": {key: value for key, value in actions.items() if isinstance(value, str) and value.strip()},
            }
            if not continuous:
                request["initial_image"] = shot.get("image_prompt", "")
            for attempt in range(2):
                response, error = None, None
                messages = [{"role": "system", "content": prompt},
                            {"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
                try:
                    response = await generate_structured(messages, temperature=0.0, max_tokens=2600, json_mode=True,
                                                         required_keys=("initial", "changes"), role='planner')
                    if not isinstance(response, dict) or set(response) - {"initial", "changes", "new_props"}:
                        raise ValueError("initial, changes et new_props uniquement, sans chemins ni autres modifications.")
                    candidate = copy.deepcopy(result)
                    props = candidate["render_canon"]["props"]
                    props.update(request["prop_descriptions"])
                    additions = response.get("new_props", {})
                    if not isinstance(additions, dict):
                        raise ValueError("new_props: dictionnaire d'objets physiques requis.")
                    entity_keys = set(candidate["render_canon"]["characters"]) | set(candidate["render_canon"]["locations"])
                    for key, value in additions.items():
                        if key in props:
                            continue
                        if (not re.fullmatch(r"[a-z0-9_-]{1,80}", key) or key in entity_keys
                                or not isinstance(value, str) or not value.strip()):
                            raise ValueError(f"new_props[{key}]: nouvel objet valide requis; ne pas modifier le canon.")
                        validate_h3_prose(value)
                        props[key] = value
                    request["prop_descriptions"] = copy.deepcopy(props)
                    request["required_initial_keys"] = list(dict.fromkeys(request["required_initial_keys"]
                        + [key for key in props if key not in result["render_canon"]["props"] and (not continuous or key not in previous)]))
                    current = candidate["scenes"][si]["shots"][ti]
                    current.pop("end_state", None)
                    start = response.get("initial")
                    if (not isinstance(start, dict) or any(not isinstance(value, str) or not value.strip() for value in start.values())):
                        raise ValueError("initial: map DIRECTE de clés d'objets déclarés vers des textes, sans wrapper start_state.")
                    unknown = [key for key in start if key not in props]
                    problems = []
                    if unknown:
                        problems.append(f"initial: objets non déclarés {json.dumps(unknown)}. Déclarer les objets physiques importants dans new_props, jamais des états de personnages/lieux, paroles ou actions. Objets déclarés: {json.dumps(list(props))}.")
                    if continuous:
                        start = {key: value for key, value in start.items() if key not in previous}
                    changes = response.get("changes")
                    if not isinstance(changes, list):
                        raise ValueError("changes: liste requise.")
                    state = start | previous if continuous else copy.deepcopy(start)
                    prepared = []
                    for index, change in enumerate(changes):
                        if not isinstance(change, dict):
                            problems.append(f"state_changes[{index}]: objet requis.")
                            continue
                        key = change.get("key")
                        valid = True
                        if not isinstance(key, str) or key not in props or key not in state:
                            problems.append(f"state_changes[{index}].key: {json.dumps(key)} doit être un objet déclaré et initialisé.")
                            valid = False
                        if not isinstance(change.get("to"), str) or not change["to"].strip():
                            problems.append(f"state_changes[{index}].to: état textuel requis.")
                            valid = False
                        ref = change.get("action_ref")
                        if isinstance(ref, str) and ref.strip() and ref not in actions:
                            matches = [name for name, text in actions.items() if isinstance(text, str) and ref in text]
                            if len(matches) == 1:
                                ref = matches[0]
                        if not isinstance(ref, str) or ref not in request["allowed_action_refs"]:
                            problems.append(f"state_changes[{index}].action_ref: {json.dumps(ref)} absent des allowed_action_refs {json.dumps(list(request['allowed_action_refs']))}.")
                            valid = False
                        if valid:
                            prepared.append({"key": key, "to": change["to"], "action_ref": ref})
                    missing_initial = [key for key in request["required_initial_keys"] if key not in state]
                    if missing_initial:
                        problems.append(f"initial: états initiaux requis pour {json.dumps(missing_initial)}.")
                    if problems:
                        raise ValueError("\n".join(problems))
                    prepared.sort(key=lambda change: list(actions).index(change["action_ref"]))
                    for change in prepared:
                        key = change["key"]
                        change["from"] = state[key]
                        state[key] = change["to"]
                    current.update(start_state=copy.deepcopy(start), state_changes=prepared)
                    current["prop_keys"] = list(dict.fromkeys(current["prop_keys"] + list(start)
                        + request["required_initial_keys"] + [change["key"] for change in prepared]))
                    prefix = copy.deepcopy(candidate)
                    prefix["scenes"] = prefix["scenes"][:si + 1]
                    prefix["scenes"][-1]["shots"] = prefix["scenes"][-1]["shots"][:ti + 1]
                    _resolve_native_states(prefix)
                    current.update(prefix["scenes"][-1]["shots"][-1])
                    previous = copy.deepcopy(current["end_state"])
                    result = candidate
                    break
                except ValueError as exc:
                    error = str(exc)
                    if attempt:
                        raise ValueError(f"scenes[{si}].shots[{ti}]: {exc}") from exc
                    request["validation_error"] = error
                    request["previous_values"] = response
                finally:
                    if trace:
                        directory = settings.assets_dir / str(trace["project_id"]) / "studio" / "diagnostics"
                        directory.mkdir(parents=True, exist_ok=True)
                        (directory / f"planner-{trace['job_id']}-{token}-state-{si}-{ti}-attempt-{attempt + 1}.json").write_text(
                            json.dumps({"messages": messages, "response": response, "validation_error": error},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def plan_episode(context, idea, objective):
    prompt = """You write filmable episodes for a GENERALIST studio. The supplied series is
the only canon. Preserve identities, wardrobe, relationships, geography and
unresolved events. Follow its genre, language and explicit brief.
Write the story before dividing it into generated takes. Total useful STORY
time should be near target_seconds. Each take lasts 1-15 seconds, one decimal, only as long as its
useful exchange, action or reaction needs. The number of takes is free; provide
at least two narrative takes for the useful events of the story.
A speaker change is not a reason to cut. Include speakers and listeners in the
same composition when the scene benefits from it. Declare ALL visible characters
in character_keys and silent conversational partners in listener_keys.
Use dialogue_turns for ordered exchanges: one known speaker and exact plain
spoken text per turn. No H3 tags, language markers or stage directions in speech.
Attribute every voice to a character. A notification without a speaking character
is a physical sound or visual event. Muet means dialogue='', dialogue_turns=[],
speaker='' and audio_mode='silent'. Preserve masks and non-human anatomy.
Use stable lowercase ASCII keys. entities contains characters and locations
ONLY. A handheld object such as an iron, case or book belongs in render_canon.props,
not entities. Declare every used location, including new rooms.
Track only plot-relevant persistent props, not every decorative object. Every key
in prop_keys needs an initial state when first introduced.
Use plain strings for states, never nested objects. Static furniture belongs in
the location. Declare furniture/appliances as props when their state affects the plot.
Develop missing bible fields without contradicting existing canon. Translate
the supplied immutable visual identities faithfully into render_canon; include
all canon character/location entries plus any new ones. Locations and props
are plain English strings, not objects. Character entries have name, appearance,
voice. Keep voice as stable vocal identity (timbre, register and accent). Describe
the changing intention and emotional delivery in prompt and turn.action alongside
the visible acting; a character trying to bluff, escape or plead should sound
invested in that attempt. This is a projection of canon, not permission to redesign it.
Respect directing_profile as realization preferences, overridden by explicit
scene framing and the brief. Keep conversations intelligible, short and responsive.
Native video has speech and quiet physical/location sounds. Any music is a file
imported by the user in editing, never composed here. Soundscape contains nonverbal sounds only.
image_prompt describes a starting still, never future actions or revelations.
Use the same reference_key only for the same identity, location and composition.
Write summary/events as facts actually shown. editorial_review and narrative_outline
are French operator explanations of concrete script moments, never performance predictions.
Return ONLY one JSON object following this schema (example keys are placeholders):
{
"prompt_contract_version":2,
"render_canon":{"style":"English visual style",
 "characters":{"character_key":{"name":"Name","appearance":"English immutable visual design and wardrobe","voice":"English stable vocal timbre and delivery"}},
 "locations":{"location_key":"English stable architecture and furniture"},
 "props":{"prop_key":"English stable shape, material, size and distinctive features"}},
"narrative_outline":{"opening":"","question":"","progression":"","payoff":"","ending":""},
"title":"episode title","summary":"factual recap","events":["actual event"],"ongoing_plot":"unresolved events, or empty on closure",
"editorial_review":{"hook":"specific opening","stakes":"desire and risk","progression":"causal changes","payoff":"actual reward","next_episode":"specific consequence or final closure","risks":"remaining ambiguity"},
"bible":{"universe":"","tone":"","visual_style":"","characters":"","relationships":"","locations":"","world_rules":"","past_events":"","ongoing_plot":"","continuity":""},
"entities":[{"key":"character_key","kind":"character","name":"Name","description":"permanent identity",
 "identity":{"visual_description":"immutable species, face, colors and proportions","variant_of":"","attached_value":null,"label_key":""}},
 {"key":"location_key","kind":"location","name":"Location name","description":"permanent room description",
 "identity":{"visual_description":"stable architecture and furniture","variant_of":"","attached_value":null,"label_key":""}}],
"scenes":[{"heading":"scene title","action":"author description","dialog":"scene speech",
 "blocking":"English fixed positions, furniture, conversation axis and eyelines",
 "shots":[{"description":"author beat","prompt":"English current visible context",
 "image_prompt":"English t=0 still including speaking and listening characters",
 "character_keys":["character_key"],"listener_keys":[],"location_key":"location_key",
 "prop_keys":["prop_key"],"start_state":{"prop_key":"English initial state, only on first appearance or independent take"},
 "state_changes":[{"key":"prop_key","from":"English state before this action","to":"English state after this action","action":"Exact owning action field text"}],"continue_from_previous":false,
 "cut_reason":"French reason for the cut","reference_key":"composition-key",
 "duration_sec":3.4,"audio_mode":"native","speaker":"character_key","dialogue":"",
 "dialogue_turns":[{"speaker":"character_key","text":"exact plain words in the series language","action":"English action during this line","reaction":"English reaction after this line"}],
 "action":"English visible event before speech","reaction":"English final event after all lines",
 "blocking":"English positions","framing":"English composition","camera":"English movement",
 "soundscape":"English nonverbal sound or N/A","screen_text":[]}]}],
"audio":{"enabled":true,"music":"none","music_gain_db":-12,"voice_gain_db":0,
 "ducking":true,"ambience":"none","ambience_gain_db":-34,
 "sfx":[{"effect":"click","shot_index":0,"at_sec":0.2,"gain_db":-18}],"holds":[]}}
Keep music="none" and do not select a music asset. The user imports music separately.
Choose ambience and effects suited to the actual genre and series identity.
Supported ambience: none, room, wind, rain, cafe, night, city, forest.
Supported effects: click, beep, impact, boom, whoosh, riser, door, footstep, pop,
boing, scratch. Cue shot_index refers to narrative takes before software insertion.
Use effects only on meaningful events. A hold is an optional motivated silence,
with shot_index, at_sec, duration_sec and drop_db. No timed effects recipe."""
    prompt += """
NATIVE PROMPT CONTRACT VERSION 2:
Return prompt_contract_version: 2, and render_canon with style (English),
characters:{key:{name,appearance,voice}}, locations:{key:English description},
props:{key:English stable shape, material, dimensions and recognizable features}.
Project the supplied immutable identities faithfully into this English render canon;
the author bible stays separate. Never change approved designs or voices in translation.
Provide narrative_outline:{opening,question,progression,payoff,ending} in French.
Write a short story understandable to a viewer who has never seen the series.
For this short-fiction brief, make the first two seconds meaningful: a concrete
visible anomaly or conflict that the first line explains or acts on.
Connect useful actions through causes and consequences. Deliver an actual reward.
Do not impose fixed reveal timestamps, a universal camera style or a shot count.
The explicit brief takes precedence over editorial preferences.
All render descriptions and render_canon are English; only speech keeps its language.
image_prompt describes t=0 only, never a future reveal or the final state.
prompt gives current visual context, not the whole bible or a duplicate of action.
blocking and framing give composition; camera gives movement.
action occurs before the first line; each dialogue_turns.action occurs with its
line, then its reaction; shot.reaction is the final visible event after all lines.
Write each event in only its owning field, with brisk intelligible dialogue and
active listening where the scene calls for it. No decorative actions to fill time.
Declare prop_keys for persistent objects and scene states. For a continuation,
OMIT inherited start_state entries: the software transmits the previous ending.
Declare initial states only for new objects or independent takes. Never output
end_state: the software computes it. state_changes lists ONLY actual changes in
action order; use [] when nothing changes. Each change's from must match the current
state and action must exactly equal its owning shot action/reaction or dialogue
turn action/reaction. Do not invent an action just to justify an object reset.
For an independent take, explain the useful cut or ellipse in cut_reason.
Never write promotional speech or a call to action.
OUTPUT LANGUAGES: render_canon and ALL realization fields are English, even when
the input brief or a validation error is French. This includes prompt, image_prompt,
blocking, framing, camera, action, reaction, soundscape and state values. French is
for operator explanations and the series' French spoken text only. Put the opening
line in the opening take, not in a later take after a silent establishing shot.
Exact scores, numbers or labels important to the story belong in screen_text.
For a number use value as an integer, never punctuation in text. The software
formats all numbers with the series number_format. For a literal label use text.
Keep their exact spelling stable until an explicit change/reveal beat. Reuse
display_values from the series canon. Never ask the image/video model to write
these values: describe a blank illuminated panel or empty score position instead.
Coordinates are normalized to the reference image. Use screen anchors for a
fixed display; character anchors require a tight, textured anchor_box around the
correct character, excluding other characters. Keep the composition simple.
No decorative score changes. Mark event=appear/change/reveal only at real beats.
The software adds the exact text and event sound in post-production.
If directing_profile.attached_values is true, declare each required character's
initial integer identity.attached_value and stable identity.label_key. Every
visible character with a configured value MUST have its own numeric screen_text
entry attached to that entity_key. Do not replace this data with prose or omit it.
For an in-shot change, keep value as the initial value and add
changes:[{"at_sec":1.2,"value":2}], with event="change"; at_sec is the useful
edited shot time. The next shot starts with that final value. Never infer a
change from action text. Preserve all other characters' values.
Identity variants retain the same immutable species, colors and facial traits;
variant_of supplies their existing reference, it is not a new random design.
If require_reference_approval is true, visual_description must explicitly fix
the immutable design of every character and location. References are reviewed before video.
"""
    trace = context.get("_planner_trace")
    trace_token = uuid.uuid4().hex
    public_context = {k: v for k, v in context.items() if k not in ("entities", "_planner_trace")}
    public_context["directing_profile"] = directing_profile(context.get("directing_profile"))
    public_context["entities"] = [
        {k: e[k] for k in ("key", "kind", "name", "description", "identity") if k in e}
        for e in context["entities"]
        if e["key"] != "studio_cta_host"
    ]
    episode_target = float(context.get("target_seconds", 65))
    target = max(5.0, episode_target - _midroll_spec(context)["duration_sec"])
    duration_instruction = (
        f"{target:g} seconds of story, with no promotional speech or CTA."
        if public_context["directing_profile"]["cta_placement"] == "none" else
        f"{target:g} seconds of story plus an automatic {_midroll_spec(context)['duration_sec']:g}-second midroll, near {episode_target:g} seconds total."
    )
    public_context["target_seconds"] = target
    native = context.get("prompt_contract_version") == 2
    creative_story, creative_workshop = (
        await _develop_creative_story(public_context, idea, objective, trace) if native else (None, None)
    )
    if native:
        prompt = """REALIZATION ONLY. Translate creative_story into the studio's native
filmable plan. The story and supplied identities are authoritative. Preserve every
useful action, cause, reward and consequence. Preserve EVERY exact spoken line,
its speaker and order. Do not write a new story or promotional speech.
When creative_story.source_locked is true, this is an already authored script:
each take must declare source_beat_index, the zero-based index of its source beat.
Never merge distinct authored beats or move their actions to an earlier reply.
Include source_beat_index on EVERY take, including silent consequences. A silent
continuation of the final beat keeps that beat's index; never invent another beat.
Preserve each beat's initial action, exact line and consequence in that order.
You may split a beat into useful takes sharing its index, without repeating events.

LANGUAGES: every image/video direction, visual canon, voice description and state
is natural ENGLISH. Only exact speech retains its language. French source actions
must be translated, never copied as render directions. Operator titles and reviews
are French. No H3 tags or stage directions inside speech.

Cut the story freely into useful 1-15 second takes, durations to one decimal, total
near target_seconds. At least two narrative takes; no promotional speech. Speaker
changes do not require cuts. Respect the brief's wide framing and depth when asked,
and its geography, visible participants, eyelines and responsive performances.
image_prompt is the SINGLE complete initial composition: name who is where,
what each person is doing at t=0, their eyelines, the relevant objects' visible
states, the framing and the environment visible from this viewpoint. This field
alone carries the staging to the image service; blocking, framing and state tables
are not appended to it. Describe ONE frozen instant, never an action sequence
or future outcome. Preserve the source's physical event order:
an exit precedes closing the door; a reaction follows its cause. Do not enact an
already-established starting condition again. Do not duplicate physical events
across action and dialogue_turns. Keep appliances in their source power state.

CONTINUITY: continue_from_previous reuses the previous video's final frame as the
next video's initial image. It is false on the first take or a new camera angle.
Use true only for a continuous action with compatible framing and visible participants.
A camera cut alone does not reset the story, positions or objects. For true,
OMIT inherited start_state entries: software transmits the previous ending. Declare
initial states for newly introduced objects. For independent takes provide all used
initial states. Never return end_state. Use state_changes:[] when nothing changes.
Each actual change specifies key, from, to and action_ref pointing to its owning
field: action, reaction, or dialogue_turns.0.action / dialogue_turns.0.reaction, etc.
References follow action order; state from must match the current transmitted value.
Do not copy action prose into state_changes. Do not invent an action to justify a reset.
Track only plot-relevant objects/states; keep static furniture in the location canon.
Declare appliances/furniture as props when their doors, ownership or power change.

The still image is t=0, never a future event. prompt gives the performance and
current dramatic context without restating positions or imposing a new framing
on that image. blocking/framing document the same composition for the operator;
camera describes movement from it. action starts the visible
event; speech can begin with that action rather than wait for it to finish. Each
turn action accompanies that line, its reaction follows, and shot reaction ends the take.
Assign each physical event to exactly one action field. Do not summarize the whole
take in shot.action and repeat those events in dialogue_turns. A starting situation
belongs in the still and staging, not in an action that enacts it again.
Choose duration from the actual spoken exchange and indispensable visible movement.
Allow natural expressive speech around 3 words/second, never over 4.2 words/second
in a shot. Allocate that speech time before gestures or pauses. Preserve every
source line and its speaker; rebalance shot durations rather than rush the dialogue.
A short line does not need a long listening pose to fill the episode's target time.
For brisk dialogue, keep lead-in gestures brief and let the speaker react while speaking.
Direct each line as an attempt to affect the interlocutor: its emotional delivery
belongs with the visible acting in prompt and turn.action. The canon voice keeps
the same timbre, register and accent; it does not freeze the character's emotion.
For a brisk exchange, the reply picks up as the preceding words finish. A bluff
can turn into a rushed excuse or an urgent plea as the character loses control;
let the source scene decide this, rather than imposing one mood on every speaker.
Across continuous takes, the last reaction launches the next action or reply,
which is already underway at the next opening; do not restage the same pause.
Dialogue takes are edited around actual speech with a brief margin: play gestures
and reactions WITH the exchange. If a physical event needs its own silent time
to be understood, give it a silent take of its actual useful duration, rather than
hiding a plot-critical event before or after a spoken take. A silent take is not
needed for every gesture. Keep any final physical action needed to understand
the consequence. Choose an
independent cut or ellipse when the source story changes place or moment.
Declare all visible characters and conversational listeners. Declare every location.
Keys are stable lowercase ASCII. Canon comes from the supplied bible and identities:
translate faithfully, never redesign, revoice or change wardrobe. Develop only missing
canon. entities contains characters and locations; props belong in render_canon.props.
Location canon describes the unoccupied architecture and furnishings; occupants,
seating assignments and temporary poses belong in image_prompt. Make ambiguous
visual nouns physically explicit, faithful to the supplied identity and reference.
For a requested dense, deep environment, turn that intent into concrete set dressing
and spatial layers in the location canon AND the initial composition: foreground,
playing area and readable background, materials, light, furnishings and signs of use
suited to this world. "Wide", "dense" and "deep" alone do not build a set. Preserve
specified landmarks and geography; invent compatible missing dressing, not extra
plot restrictions such as an empty site or inactive equipment unless requested.
Other visual styles remain governed by their own brief.
Native soundscape is quiet nonverbal physical/location sound; no music or hidden speech.

Return ONLY this JSON structure, with complete fields and freely chosen scene/take count:
{"prompt_contract_version":2,"title":"","summary":"","events":[],"ongoing_plot":"",
"narrative_outline":{"opening":"","question":"","progression":"","payoff":"","ending":""},
"editorial_review":{"hook":"","stakes":"","progression":"","payoff":"","next_episode":"","risks":""},
"bible":{},"entities":[{"key":"character_key","kind":"character","name":"Name",
"description":"Permanent identity","identity":{"visual_description":"Immutable appearance"}},
{"key":"location_key","kind":"location","name":"Name","description":"Stable room"}],
"render_canon":{"style":"English visual style","characters":{"character_key":
{"name":"Name","appearance":"English immutable appearance and wardrobe","voice":"English stable voice"}},
"locations":{"location_key":"English stable architecture and furniture"},
"props":{"prop_key":"English stable object shape, size and features"}},
"scenes":[{"heading":"","blocking":"English geography","shots":[{
"description":"Operator beat","source_beat_index":0,"prompt":"English current context","image_prompt":"English t=0 still",
"character_keys":["character_key"],"listener_keys":[],"location_key":"location_key",
"prop_keys":["prop_key"],"start_state":{"prop_key":"English initial state ONLY when required"},
"state_changes":[],"continue_from_previous":true,"cut_reason":"Useful cut or ellipse",
"reference_key":"composition-key","duration_sec":5.0,"audio_mode":"native","speaker":"",
"dialogue":"","dialogue_turns":[{"speaker":"character_key","text":"Exact source words",
"action":"English action during this line","reaction":"English reaction after this line"}],
"action":"English action accompanying the opening exchange or silent beat","reaction":"English final event",
"blocking":"English positions","framing":"English composition","camera":"English movement",
"soundscape":"English nonverbal sounds","screen_text":[]}]}],
"audio":{"enabled":true,"music":"none","voice_gain_db":0,"ducking":true,
"ambience":"none","ambience_gain_db":-34,"sfx":[],"holds":[]}}

Choose the export sound mix as well as each take's native soundscape. Keep speech
clear and use a quiet nonverbal ambience suited to the location when appropriate.
Supported ambience: none, room, wind, rain, cafe, night, city, forest.
Supported effects: click, beep, impact, boom, whoosh, riser, door, footstep, pop,
boing, scratch. Each sfx cue has effect, shot_index (zero-based narrative take),
at_sec and gain_db. Add cues only for visible meaningful events, without duplicating
native effects. Use holds only for motivated silences, with shot_index, at_sec,
duration_sec and drop_db. Keep music="none"; only the user can import music.

Use audio_mode=silent, no speaker and no dialogue for silent takes. For exact labels
use screen_text with software-rendered text/value and normalized coordinates; leave
blank surfaces in image/video directions. Each label needs key, x and y, plus text
for literal words OR value as an integer for a number; never put words in value.
Minimal literal label: {"key":"material","text":"POLYSTYRÈNE","x":0.5,"y":0.8,
"anchor":"screen","event":"reveal"}. A screen anchor is a fixed overlay, not an
object tracker: target_prop_key and placement are unsupported. For character
tracking use anchor="character", entity_key and a normalized anchor_box [x,y,w,h].
Numeric changes use event="change" and changes:[{"at_sec":1.2,"value":2}].
Preserve attached values, variants and reference approvals in the
directing_profile. Do not bake labels or digits into images. No decorative text.
"""
    estimated_shots = max(8, round(episode_target / 3))
    if native and settings.studio_video_profile == 'ltx':
        prompt += """\nLTX REALIZATION: give each technical take one active speaking character.
Consecutive lines by that same character may share a take. Keep all conversational
participants in the established shared composition. A reply by another character
continues from the preceding final frame with continue_from_previous=true, the same
camera, eyelines and ongoing action. This is a technical continuation, not a reverse
angle, new scene, pause or visible edit. Describe only the current take's acting.
Keep exact source words, voices, roles and useful actions. Never add dialogue to fill time.
"""
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "series": public_context,
                    "idea": idea,
                    "objective": objective,
                    **({"creative_story": creative_story} if native else {}),
                    "target_seconds": target,
                    "ending": (
                        "No promotional speech, CTA or marketing overlay. The entire target duration "
                        "belongs to the story. End on its payoff and any narrative consequence."
                        if public_context["directing_profile"]["cta_placement"] == "none" else
                        "The software appends the spoken promotion AFTER the complete story. "
                        "Do not write that promotion or interrupt the story for it. Keep the "
                        "story ending for the payoff and the next episode's consequence."
                        if public_context["directing_profile"]["cta_placement"] == "end" else
                        "The software inserts a brief spoken aside near the middle, using "
                        "a story character. Do not write that promotion. Keep the ending "
                        "for the local payoff and the next episode's consequence."
                    ),
                    "pacing": (
                        "free shot count, one decimal duration_sec per shot, "
                        "each as short as its beat allows, total near target_seconds"
                    ),
                },
                ensure_ascii=False,
            ),
        },
    ]
    result = None
    repair_fields = []
    states_prepared = False
    initial_messages = copy.deepcopy(messages)
    duration_failure = None
    for attempt in range(3):
        request_messages = copy.deepcopy(messages)
        response = None
        validation_error = None
        applied = False
        try:
            states_prepared = False
            repairing = native and bool(repair_fields)
            response = await generate_structured(
                messages,
                temperature=0.45,
                max_tokens=max(12000 if context.get("prompt_contract_version") == 2 else 4096,
                               min(16000, estimated_shots * 650 + 2000)),
                role='planner' if native else None,
                **({"required_keys": ("values",) if repairing else ("prompt_contract_version", "scenes")}
                   if native else {}),
            )
            result = _apply_native_values(result, [field["path"] for field in repair_fields], response) if repairing else response
            applied = True
            if isinstance(result, dict) and result.get("prompt_contract_version") == 2:
                result = _normalize_native_keys(result)
            if native and (not isinstance(result, dict) or result.get("prompt_contract_version") != 2):
                raise ValueError("prompt_contract_version: le nouveau scénario doit utiliser le contrat 2.")
            if native and creative_story.get('source_locked'):
                try:
                    _validate_story_realization(creative_story, result, result)
                except (TypeError, KeyError, AttributeError):
                    raise _NativeValidationError(
                        'Scénario fourni : structure de scènes et de répliques invalide.',
                        [['scenes']]) from None
            multi_speaker = False
            if native and settings.studio_video_profile == 'ltx' and isinstance(result, dict):
                for scene in result.get('scenes') or []:
                    if not isinstance(scene, dict):
                        continue
                    for shot in scene.get('shots') or []:
                        if not isinstance(shot, dict):
                            continue
                        turns = shot.get('dialogue_turns')
                        if not isinstance(turns, list):
                            continue
                        speakers = {
                            turn.get('speaker') for turn in turns
                            if isinstance(turn, dict) and isinstance(turn.get('speaker'), str)
                        }
                        if len(speakers) > 1:
                            multi_speaker = True
                            break
                    if multi_speaker:
                        break
            if multi_speaker:
                shots = [
                    shot for scene in result.get('scenes') or [] if isinstance(scene, dict)
                    for shot in scene.get('shots') or [] if isinstance(shot, dict)
                ]
                if any('state_changes' not in shot for shot in shots):
                    result['creative_story'] = copy.deepcopy(creative_story)
                    result = await prepare_native_states(result, trace)
                    states_prepared = True
                from calliope.studio.ltx_realization import prepare_ltx_plan

                result = prepare_ltx_plan(result)
            missing = []
            if native and isinstance(result.get("scenes"), list):
                for si, scene in enumerate(result["scenes"]):
                    if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
                        continue
                    for ti, shot in enumerate(scene["shots"]):
                        if isinstance(shot, dict) and shot.get("purpose") not in ("midroll", "outro") and "state_changes" not in shot:
                            missing.append(["scenes", si, "shots", ti, "state_changes"])
            try:
                plan = normalize_plan(copy.deepcopy(result), context | {"target_seconds": target})
            except _NativeValidationError as exc:
                if not native or states_prepared or not any(
                        len(path) >= 5 and path[0] == "scenes" and path[2] == "shots"
                        and path[4] in ("start_state", "end_state", "state_changes") for path in exc.paths):
                    raise
                result['creative_story'] = copy.deepcopy(creative_story)
                result = await prepare_native_states(result, trace)
                states_prepared = True
                plan = normalize_plan(copy.deepcopy(result), context | {"target_seconds": target})
            if native and missing and not states_prepared:
                result['creative_story'] = copy.deepcopy(creative_story)
                result = await prepare_native_states(result, trace)
                states_prepared = True
                plan = normalize_plan(copy.deepcopy(result), context | {"target_seconds": target})
            if native:
                _validate_story_realization(creative_story, plan, result)
                plan["creative_story"] = copy.deepcopy(creative_story)
                plan['creative_workshop'] = copy.deepcopy(creative_workshop)
                for field in ("title", "summary", "events", "ongoing_plot", "narrative_outline"):
                    if field in creative_story:
                        plan[field] = copy.deepcopy(creative_story[field])
                plan["_states_prepared"] = True
            if native and settings.studio_video_profile == 'ltx':
                from calliope.studio.ltx_realization import prepare_ltx_plan
                plan = prepare_ltx_plan(plan)
                _resolve_native_states(plan)
                _validate_native_contract(plan, list({e['key']: e for e in
                    context.get('entities', []) + plan.get('entities', [])}.values()))
            return insert_midroll(plan, context)
        except ValueError as exc:
            validation_error = str(exc)
            logger.warning("Planner validation failed attempt=%s: %s", attempt + 1, exc)
            if isinstance(exc, _UnrecoverableStoryRealizationError):
                # Do not convert a collapsed plan into whole-scene replacement
                # requests. The originating draft remains available in trace.
                raise
            if (native and attempt < 2 and duration_failure
                    and isinstance(exc, _StoryRealizationError)
                    and not creative_story.get('source_locked')):
                creative_story = await _write_creative_story(
                    public_context, idea, objective, trace,
                    previous_story=creative_story,
                    revision_note=(
                        duration_failure + " La réparation technique a changé les paroles. "
                        "Reprends l'écriture pour remplir la durée avec des événements utiles, "
                        "en couvrant toutes les séquences et tous les lieux demandés dans idea. "
                        "N'ajoute ni pauses de remplissage ni incitation promotionnelle."
                    ),
                )
                messages = copy.deepcopy(initial_messages)
                request = json.loads(messages[1]['content'])
                request['creative_story'] = creative_story
                messages[1]['content'] = json.dumps(request, ensure_ascii=False)
                result, repair_fields, duration_failure = None, [], None
                continue
            if isinstance(exc, _NarrativeDurationError):
                duration_failure = str(exc)
            if attempt == 2:
                raise
            if native:
                if isinstance(exc, _NativeValidationError):
                    paths = exc.paths
                    repair_fields = _native_repair_fields(result, paths)
                elif applied or not repair_fields:
                    raise
                messages = messages[:2]
                messages[0] = {"role": "system", "content": """REPAIR VALUES ONLY.
The assistant JSON is the current draft. Software has selected repair_fields;
their ids identify the ONLY requested replacement values. Return ONLY:
{"values":{"field_1":"replacement value"}}
Use every supplied id exactly once. A value may be text, a number, boolean,
object or array as required by that field. Return no destinations, other keys,
whole episode or unrelated edits. Software applies values to their retained
destinations and validates the whole plan again.
For source_beat_index return an integer from the zero-based creative_story.beats
list. Match the exact speaker and line; a silent continuation keeps its source beat.
For a requested render_canon.props description, return one plain English string,
never an object with name, description or identity. Props map keys to strings.
Preserve silent events too: an empty dialogue turn is a silent action, not a
spoken line. Move its action/reaction to the appropriate supplied action or
reaction fields without changing event order, then omit that empty turn.
Preserve the supplied creative_story's exact lines, speakers and order. Repair
only the diagnosed defect, without inventing actions, changing the plot or
disabling continuity to bypass a mismatch. Render descriptions and object
states are English; speech stays in the original language. Initial states
describe t=0, not future events. A state change must belong to an existing
action, with action_ref selecting action, reaction or dialogue_turns.N.action
or dialogue_turns.N.reaction in event order. Unchanged states are inherited.
For a requested start_state or end_state, the value is an object mapping prop
keys to English state strings, e.g. {"prop_key":"closed on the table"}.
For a requested state_changes, the value is an array, e.g.
[{"key":"prop_key","from":"closed on the table","to":"open on the table",
"action_ref":"action"}], or [] when no event changes a state. key, from,
to and action_ref are required on EVERY change. Copy the current state exactly
into from. Use a reference to an existing nonempty action, never action prose.
"""}
                messages.extend([
                    {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                    {"role": "user", "content": json.dumps({
                        "validation_error": str(exc), "repair_fields": repair_fields,
                        "task": "Repair only the requested values.",
                        "duration": duration_instruction,
                    }, ensure_ascii=False)},
                ])
                continue
            messages.extend(
                [
                    {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            f"Validation failed: {exc}. "
                            f"{'Return targeted updates for the current draft. ' if native else 'Repair the complete JSON episode. '}"
                            f"Keep free per-shot durations; adjust the cutting or the "
                            f"dialogue so the useful total stays near {target} seconds "
                            f"of story. {duration_instruction} "
                            "without stretching any shot. "
                            "Preserve the story and series canon. Shorten dialogue if needed. "
                            "Repair the identified field, preserving unaffected shots. "
                            "Repair every listed continuity mismatch in this response. "
                            "For a continuation, omit inherited start_state entries; the software carries them. "
                            "Correct state_changes when an actual action changes an object; never set end_state. "
                            "Preserve creative_story's exact lines, speakers and order. "
                            "Do not turn off continuation merely to bypass a mismatch. "
                            "Each dialogue turn must have one speaker and plain verbatim text. "
                            "For multiple voices use ordered dialogue_turns inside the same "
                            "shot when the composition supports the exchange. Do not discard "
                            "a character's reply or hide it in another field. "
                            f"{'Return only the updates object. ' if native else 'Return the complete corrected JSON, with all scenes and shots. '}"
                            " Keep render_canon and all realization/state descriptions in English; "
                            "do not copy the validation error's language into visual directions. "
                            "Declare every used location in entities with kind='location'. "
                            "Every new prop_keys entry needs an initial state; unchanged objects are carried automatically."
                        ),
                    },
                ]
            )
        finally:
            if trace:
                directory = (
                    settings.assets_dir / str(trace["project_id"]) / "studio" / "diagnostics"
                )
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / (
                    f"planner-{trace['job_id']}-{trace_token}-attempt-{attempt + 1}.json"
                )
                path.write_text(
                    json.dumps(
                        {
                            "project_id": trace["project_id"],
                            "job_id": trace["job_id"],
                            "attempt": attempt + 1,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "messages": request_messages,
                            "response": result,
                            "repair_response": response if native and attempt else None,
                            "validation_error": validation_error,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )


def canonical_key(value, entities, kind):
    value = str(value or "").strip()
    if not value:
        return ""
    candidates = [e for e in entities if e.get("kind", "character") == kind]
    if any(e["key"] == value for e in candidates):
        return value
    normalized = re.sub(r"[\s_-]+", "_", value.casefold())
    keys = {
        e["key"]
        for e in candidates
        if normalized
        in {
            re.sub(r"[\s_-]+", "_", str(e.get(field) or "").strip().casefold())
            for field in ("key", "name")
        }
    }
    return next(iter(keys)) if len(keys) == 1 else value


def _midroll_spec(context):
    from calliope.studio.planner import beat_duration

    if directing_profile(context.get("directing_profile"))["cta_placement"] == "none":
        return {"dialogue": "", "duration_sec": 0.0}
    label = dialogue_language(context.get("language"))
    language = next(code for code, name in LANGUAGES.items() if name == label)
    dialogue = FRENCH_MIDROLL if language == "fr" else MIDROLL_DIALOGUES[language]
    return {"dialogue": dialogue, "duration_sec": beat_duration(dialogue, words_per_sec=4.2) if language == "fr" else MIDROLL_SECONDS}


def _speaker_ids_for_plan(plan):
    ids = {}
    for scene in plan["scenes"]:
        for shot in scene["shots"]:
            if shot.get("audio_mode", "native") == "silent":
                continue
            turns = shot.get("dialogue_turns") or (
                [{"speaker": shot["speaker"], "text": shot["dialogue"]}]
                if shot.get("dialogue") and shot.get("speaker") else []
            )
            for turn in turns:
                if turn["speaker"] not in ids:
                    ids[turn["speaker"]] = f"S{len(ids) + 1}"
    return ids


def _validate_state_link(previous, current, path):
    if not current.get("continue_from_previous"):
        return
    if previous is None:
        raise ValueError(f"{path}.continue_from_previous: aucun plan narratif précédent.")
    errors = []
    for key, value in previous.get("end_state", {}).items():
        actual = current.get("start_state", {}).get(key)
        if actual != value:
            errors.append(
                f"{path}.start_state[{key}]: raccord incompatible avec le plan précédent; "
                f"attendu {json.dumps(value, ensure_ascii=False)}, "
                f"reçu {json.dumps(actual, ensure_ascii=False)}."
            )
    if errors:
        raise ValueError("\n".join(errors))


def _validate_native_contract(plan, entities):
    canon = plan.get("render_canon")
    if not isinstance(canon, dict):
        raise _NativeValidationError("render_canon: dictionnaire requis.", [["render_canon"]])
    if not isinstance(canon.get("style"), str) or not canon["style"].strip():
        raise _NativeValidationError("render_canon.style: projection anglaise requise.", [["render_canon", "style"]])
    for kind in ("characters", "locations", "props"):
        if not isinstance(canon.get(kind), dict):
            raise _NativeValidationError(f"render_canon.{kind}: dictionnaire requis.", [["render_canon", kind]])
    for entity in entities:
        key = entity["key"]
        kind = "characters" if entity["kind"] == "character" else "locations"
        entry = canon[kind].get(key)
        fields = ("name", "appearance", "voice") if kind == "characters" else ()
        if fields:
            if not isinstance(entry, dict) or any(not isinstance(entry.get(f), str) or not entry[f].strip() for f in fields):
                raise _NativeValidationError(f"render_canon.{kind}[{key}]: identité et voix requises.", [["render_canon", kind, key]])
            for field in fields:
                validate_h3_prose(entry[field])
        elif not isinstance(entry, str) or not entry.strip():
            raise _NativeValidationError(f"render_canon.{kind}[{key}]: description requise.", [["render_canon", kind, key]])
    for key, value in canon["props"].items():
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", key):
            raise _NativeValidationError(f"render_canon.props[{key}]: identifiant d'objet invalide.", [["render_canon", "props"]])
        if not isinstance(value, str) or not value.strip():
            raise _NativeValidationError(f"render_canon.props[{key}]: objet invalide.", [["render_canon", "props", key]])
        validate_h3_prose(value)
    outline = plan.get("narrative_outline")
    if not isinstance(outline, dict) or any(not isinstance(outline.get(k), str) or not outline[k].strip()
                                          for k in ("opening", "question", "progression", "payoff", "ending")):
        raise _NativeValidationError("narrative_outline: ouverture, question, progression, récompense et fin requises.", [["narrative_outline"]])
    known = {e["key"] for e in entities} | set(canon["props"])
    previous = None
    link_errors, link_paths = [], []
    for si, scene in enumerate(plan["scenes"]):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list) or not scene["shots"]:
            raise _NativeValidationError(f"scenes[{si}]: scène et liste de plans requises.", [["scenes", si]])
        for ti, shot in enumerate(scene["shots"]):
            path = f"scenes[{si}].shots[{ti}]"
            target = ["scenes", si, "shots", ti]
            if not isinstance(shot, dict):
                raise _NativeValidationError(f"{path}: plan invalide.", [target])
            props = shot.get("prop_keys")
            if not isinstance(props, list) or any(not isinstance(key, str) or key not in canon["props"] for key in props):
                raise _NativeValidationError(f"{path}.prop_keys: objets non déclarés.", [target + ["prop_keys"]])
            for field in ("start_state", "end_state"):
                state = shot.get(field)
                if not isinstance(state, dict) or any(k not in known or not isinstance(v, str) or not v.strip()
                                                      for k, v in state.items()) or any(k not in state for k in props):
                    raise _NativeValidationError(f"{path}.{field}: états textuels des objets déclarés requis.", [target + [field]])
            if not isinstance(shot.get("continue_from_previous", False), bool):
                raise _NativeValidationError(f"{path}.continue_from_previous: booléen requis.", [target + ["continue_from_previous"]])
            if not isinstance(shot.get("cut_reason", ""), str):
                raise _NativeValidationError(f"{path}.cut_reason: texte requis.", [target + ["cut_reason"]])
            if shot.get("purpose") not in ("midroll", "outro"):
                try:
                    _validate_state_link(previous, shot, path)
                except ValueError as exc:
                    if previous is None:
                        raise _NativeValidationError(str(exc), [target + ["continue_from_previous"]]) from None
                    link_errors.append(str(exc))
                    link_paths.extend(target + ["start_state", key] for key, value in previous.get("end_state", {}).items()
                                      if shot.get("start_state", {}).get(key) != value)
                previous = shot
    if link_errors:
        raise _NativeValidationError("\n".join(link_errors), link_paths)


def insert_midroll(plan, context):
    label = dialogue_language(context.get("language"))
    language = next(code for code, name in LANGUAGES.items() if name == label)
    positions, scenes, story = {}, [], []
    old_index = 0
    for scene in plan.get("scenes", []):
        shots = []
        for shot in scene.get("shots", []):
            if shot.get("purpose") not in ("outro", "midroll"):
                positions[old_index] = len(story)
                story.append(shot)
                shots.append(shot)
            old_index += 1
        if shots:
            scenes.append(scene | {"shots": shots})
    if directing_profile(context.get("directing_profile"))["cta_placement"] == "none":
        plan["scenes"] = scenes
        config = plan.get("audio") or {}
        for kind in ("sfx", "holds"):
            if kind in config:
                config[kind] = [
                    cue | {"shot_index": positions[cue.get("shot_index", 0)]}
                    for cue in config[kind] if cue.get("shot_index", 0) in positions
                ]
        if plan.get("prompt_contract_version") == 2:
            plan["speaker_ids"] = _speaker_ids_for_plan(plan)
        return plan
    if len(story) < 2:
        raise ValueError(
            "Prévoir au moins deux plans narratifs pour intégrer l'incitation au milieu."
        )
    at_end = directing_profile(context.get("directing_profile"))["cta_placement"] == "end"
    if at_end:
        insertion = len(story)
    else:
        elapsed, boundaries = 0.0, []
        for index, shot in enumerate(story[:-1], 1):
            elapsed += float(shot["duration_sec"])
            boundaries.append((index, elapsed))
        middle = sum(float(shot["duration_sec"]) for shot in story) / 2
        insertion = min(boundaries, key=lambda item: abs(item[1] - middle))[0]
    preceding = story[insertion - 1]
    entities = {e["key"]: e for e in context.get("entities", []) + plan.get("entities", [])}
    characters = [e for e in entities.values()
                  if e.get("kind") == "character" and e["key"] != "studio_cta_host"]
    speaker = next((key for key in preceding.get("character_keys", [])
                    if key in entities and entities[key].get("kind") == "character"
                    and key != "studio_cta_host"), None)
    if not speaker and characters:
        speaker = characters[0]["key"]
    if not speaker:
        raise ValueError("Un personnage de la série est nécessaire pour l'incitation.")
    location = preceding.get("location_key", "")
    dialogue = MIDROLL_DIALOGUES[language]
    shot = {
        "description": (
            "Une courte parenthèse : le personnage pointe le téléphone et invite à agir."
        ),
        "prompt": (
            "A brief fourth-wall aside in the current story location. The canonical character "
            "turns toward the viewer and points with one finger toward a smartphone graphic "
            "in the lower-right foreground, added in postproduction. Leave that region clear. "
            "Keep the established costume, identity and voice. Only this character speaks."
        ),
        "image_prompt": (
            "Medium close-up of the canonical speaking character in the current location, "
            "matching the series style and lighting. Face and pointing hand visible on the "
            "left; lower-right foreground empty for the smartphone added in postproduction. "
            "No generated text or typography."
        ),
        "dialogue": dialogue,
        "dialogue_turns": [{"speaker": speaker, "text": dialogue}],
        "speaker": speaker,
        "character_keys": [speaker],
        "location_key": location,
        "blocking": preceding.get("blocking", ""),
        "reference_key": f"studio-midroll-{speaker}-{location or 'setting'}",
        "duration_sec": MIDROLL_SECONDS,
        "audio_mode": "native",
        "language": language,
        "purpose": "midroll",
        "framing": "Face and pointing hand on the left; smartphone region clear on the right.",
        "camera": "A quick motivated zoom toward the character, then steady framing.",
        "action": "The character points toward the smartphone and speaks to the viewer.",
        "reaction": "A knowing smile, then the story resumes immediately.",
        "soundscape": (
            "Only the tagged dialogue, without ambience, music or physical sound effects."
        ),
        "screen_text": [
            {"key": f"studio-midroll-{index}", "text": text, "anchor": "screen",
             "x": 0.68, "y": 0.60 + index * 0.07, "size": 0.025, "color": "FFFFFF"}
            for index, text in enumerate(MIDROLL_LABELS[language])
        ],
    }
    if plan.get("prompt_contract_version") == 2:
        spec = _midroll_spec(context)
        shot.update(
            description="Le personnage s'adresse brièvement au spectateur.",
            prompt="An energetic, conversational aside in the current story location.",
            image_prompt="The speaking character already faces the viewer in the current location, face and free hand clearly visible, matching the story lighting.",
            dialogue=spec["dialogue"], dialogue_turns=[{"speaker": speaker, "text": spec["dialogue"]}],
            duration_sec=spec["duration_sec"],
            blocking="The speaking character remains in the established location, with face and free pointing hand clearly visible.",
            framing="A readable medium close-up of the character's face and pointing hand.",
            camera="A quick small zoom accompanies the first words, then holds the framing during speech.",
            action="The character points one finger toward the viewer while speaking immediately.",
            reaction="", soundscape="N/A", screen_text=[], prop_keys=[],
            start_state={}, end_state={}, cut_reason="Parenthèse centrale, puis retour à l'action narrative."
        )
    if at_end:
        shot["description"] = "Incitation brève après la chute de l'histoire."
        shot["reaction"] = ""
        shot["cut_reason"] = "L'histoire est terminée ; incitation finale sans interruption du récit."
    identity = entities[speaker].get("identity") or {}
    if identity.get("attached_value") is not None:
        value = identity["attached_value"]
        for previous in story[:insertion]:
            for numeric in previous.get("screen_text", []):
                if numeric.get("entity_key") == speaker and numeric.get("value") is not None:
                    value = (numeric.get("changes") or [{"value": numeric["value"]}])[-1]["value"]
        shot["screen_text"].append({
            "key": identity.get("label_key") or speaker, "entity_key": speaker,
            "value": value, "text": str(value), "anchor": "character", "x": 0.22, "y": 0.15,
            "anchor_box": [0.06, 0.12, 0.35, 0.65],
        })
    count = 0
    for scene in scenes:
        if count < insertion <= count + len(scene["shots"]):
            scene["shots"].insert(insertion - count, shot)
            break
        count += len(scene["shots"])
    plan["scenes"] = scenes
    config = plan.setdefault("audio", {})
    for kind in ("sfx", "holds"):
        config[kind] = [
            cue | {"shot_index": positions[cue.get("shot_index", 0)]
                   + (positions[cue.get("shot_index", 0)] >= insertion)}
            for cue in config.get(kind, []) if cue.get("shot_index", 0) in positions
        ]
    config["holds"].append({
        "shot_index": insertion, "at_sec": 0, "duration_sec": shot["duration_sec"], "drop_db": -90,
    })
    if plan.get("prompt_contract_version") == 2:
        plan["speaker_ids"] = _speaker_ids_for_plan(plan)
    return plan


def normalize_dialogue(dialogue, speaker, entities):
    aliases = {}
    for entity in entities:
        if entity.get("kind") == "location":
            continue
        for name in (entity.get("key"), entity.get("name")):
            if name:
                aliases.setdefault(str(name).strip().casefold(), set()).add(entity["key"])
    quotes = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "\u00ab": "\u00bb"}
    had_label = False
    while True:
        before = dialogue
        if len(dialogue) >= 2 and quotes.get(dialogue[0]) == dialogue[-1]:
            dialogue = dialogue[1:-1].strip()
        prefix = re.match(r"^([^:\n]{1,80}):\s*", dialogue)
        owners = aliases.get(prefix[1].strip().casefold(), set()) if prefix else set()
        if owners:
            if len(owners) != 1 or (speaker and speaker not in owners):
                raise ValueError(
                    "La réplique contient plusieurs voix ou un locuteur contradictoire."
                )
            speaker = next(iter(owners))
            dialogue = dialogue[prefix.end() :].strip()
            had_label = True
        wrapper = re.fullmatch(r"<d>\s*(.*?)\s*</d>", dialogue, re.S | re.I)
        if wrapper and not re.search(r"<[^>]+>", wrapper[1]):
            dialogue = wrapper[1].strip()
            language = re.match(r"^\[([^\]]+)\]\s*", dialogue)
            if language and language[1].casefold() in (
                name.casefold() for name in LANGUAGES.values()
            ):
                dialogue = dialogue[language.end() :].strip()
        if before == dialogue:
            break
    labels = re.findall(r"(?:^|\s)([^\W\d_][\w '-]{0,30}):", dialogue, re.I)
    if len(labels) > 1 and any(
        aliases.get(label.strip().casefold()) != {speaker} for label in labels
    ):
        raise ValueError(
            "La réplique contient plusieurs voix non identifiées; utilisez dialogue_turns."
        )
    lines = []
    for index, line in enumerate(dialogue.splitlines()):
        line = line.strip()
        prefix = re.match(r"^([^:\n]{1,80}):\s*", line)
        owners = aliases.get(prefix[1].strip().casefold(), set()) if prefix else set()
        if owners:
            if len(owners) != 1 or (speaker and speaker not in owners):
                raise ValueError(
                    "La réplique contient plusieurs voix; séparez les locuteurs en dialogue_turns."
                )
            speaker = next(iter(owners))
            line = line[prefix.end() :].strip()
            had_label = True
        elif index and had_label and prefix:
            raise ValueError(f"La réplique contient plusieurs voix possibles: {prefix[1]!r}.")
        lines.append(line)
    dialogue = " ".join(" ".join(lines).split())
    for alias in aliases:
        if re.search(r"(?<!\w)" + re.escape(alias) + r"\s*:", dialogue, re.I):
            raise ValueError(
                "La réplique contient plusieurs voix ou une étiquette de locuteur interne."
            )
    if re.search(
        r"<[^>]+>|\[[^\]]*\]|\([^)]*\)|\bsay exactly\b|\bspoken dialogue\b", dialogue, re.I
    ):
        raise ValueError("La réplique contient des instructions ou une syntaxe H3 non isolable.")
    return dialogue, speaker


def normalize_plan(result, context):
    if not isinstance(result, dict) or not isinstance(result.get("scenes"), list):
        if isinstance(result, dict) and result.get("prompt_contract_version") == 2:
            raise _NativeValidationError("L'IA n'a pas renvoyé de scénario exploitable.", [["scenes"]])
        raise ValueError("L'IA n'a pas renvoyé de scénario exploitable.")
    if result.get("prompt_contract_version") == 2:
        result = _normalize_native_keys(result)
        _resolve_native_states(result)
    profile = directing_profile(context.get("directing_profile"))
    canon = {item["key"]: item for item in context.get("entities", [])}
    known = set(canon)
    new_entities = result.get("entities", [])
    if not isinstance(new_entities, list) or len(new_entities) > 30:
        raise ValueError("Liste de personnages ou lieux invalide.")
    for index, entity in enumerate(new_entities):
        if not isinstance(entity, dict):
            raise ValueError(f"entities[{index}]: objet d'identité requis.")
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", str(entity.get("key", ""))):
            raise ValueError(f"entities[{index}].key: identifiant ASCII en minuscules requis, reçu {json.dumps(entity.get('key'))}.")
        if entity.get("kind") not in ("character", "location") or not str(entity.get("description", "")).strip():
            raise ValueError(f"entities[{index}]: kind character/location et description non vide requis.")
        if entity["key"] in canon:
            original = canon[entity["key"]]
            for field in ("name", "description", "identity"):
                if field in original:
                    entity[field] = copy.deepcopy(original[field])
        known.add(entity["key"])
    all_entities = list((canon | {e["key"]: e for e in new_entities}).values())
    version = result.get("prompt_contract_version", 1)
    if version not in (1, 2):
        raise ValueError("prompt_contract_version: version non prise en charge.")
    if version == 2:
        _validate_native_contract(result, all_entities)
    required_values = {}
    for entity in all_entities:
        identity = entity.get("identity") or {}
        if (
            profile["require_reference_approval"]
            and not identity.get("visual_description", "").strip()
        ):
            raise ValueError(
                f"entities[{entity['key']}].identity.visual_description: "
                "identité immuable manquante."
            )
        if entity.get("kind", "character") != "character":
            continue
        if identity.get("attached_value") is not None:
            value = identity["attached_value"]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"entities[{entity['key']}].identity.attached_value: entier requis."
                )
            required_values[entity["key"]] = (identity.get("label_key") or entity["key"], value)
    if profile["attached_values"] and not required_values:
        raise ValueError(
            "entities.identity.attached_value: "
            "déclarez les valeurs entières structurées des personnages."
        )
    attached_required = profile["attached_values"] or bool(required_values)
    characters = {e["key"] for e in all_entities if e.get("kind", "character") == "character"}
    result["speaker_ids"] = dict(context.get("speaker_ids") or {})
    for entity in all_entities:
        if entity["key"] in characters and entity["key"] not in result["speaker_ids"]:
            result["speaker_ids"][entity["key"]] = f"S{len(result['speaker_ids']) + 1}"
    reference_errors, reference_paths = [], []
    for scene_index, scene in enumerate(result["scenes"]):
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
            continue
        for shot_index, shot in enumerate(scene["shots"]):
            if not isinstance(shot, dict):
                continue
            field = f"scenes[{scene_index}].shots[{shot_index}]"
            target = ["scenes", scene_index, "shots", shot_index]
            location = canonical_key(shot.get("location_key"), all_entities, "location")
            if location and location not in known:
                reference_errors.append(f"{field}.location_key: Lieu non déclaré {location!r}.")
                reference_paths.append(["entities"] if version == 2 and location in result["render_canon"]["locations"]
                                       else target + ["location_key"])
            turns = shot.get("dialogue_turns") or []
            if not isinstance(turns, list) or len(turns) > 30:
                continue
            for turn_index, turn in enumerate(turns):
                turn_field = f"{field}.dialogue_turns[{turn_index}]"
                if not isinstance(turn, dict):
                    reference_errors.append(f"{turn_field}: Tour de parole invalide.")
                    reference_paths.append(target + ["dialogue_turns", turn_index])
                    continue
                try:
                    turn_speaker = canonical_key(turn.get("speaker"), all_entities, "character")
                    text, turn_speaker = normalize_dialogue(
                        str(turn.get("text") or "").strip(), turn_speaker, all_entities
                    )
                    if turn_speaker not in characters:
                        raise ValueError(
                            f"speaker: Locuteur inconnu {turn_speaker!r}. "
                            f"Réplique: {str(turn.get('text'))[:240]!r}."
                        )
                    if not text:
                        raise ValueError("text: Réplique vide.")
                    turn.update(speaker=turn_speaker, text=text)
                except ValueError as exc:
                    reference_errors.append(f"{turn_field}.{exc}")
                    if not str(turn.get("text") or "").strip():
                        reference_paths.extend(target + [name] for name in ("dialogue_turns", "action", "reaction"))
                    else:
                        reference_paths.append(target + ["dialogue_turns", turn_index])
    if reference_errors:
        raise _NativeValidationError(
            "Références du scénario à corriger ensemble: "
            + "\n".join(reference_errors)
            + "\nDéclarez tous les lieux dans entities. Attribuez chaque réplique à un "
            "personnage connu; une notification sans personnage parlant est un son ou "
            "une action, pas une réplique sans locuteur.", reference_paths
        )
    total_duration = 0
    speech_errors, speech_paths = [], []
    display_values = dict(context.get("display_values") or {})
    if not result["scenes"]:
        raise ValueError("L'épisode doit contenir au moins une scène.")
    for scene_index, scene in enumerate(result["scenes"]):
        if (
            not isinstance(scene, dict)
            or not isinstance(scene.get("shots"), list)
            or not scene["shots"]
        ):
            raise ValueError("Le scénario contient une scène vide.")
        blocking = scene.get("blocking", "")
        if not isinstance(blocking, str) or len(blocking) > 5000:
            raise ValueError("La disposition de la scène est invalide.")
        validate_h3_prose(blocking)
        for shot_index, shot in enumerate(scene["shots"]):
            if (
                not isinstance(shot, dict)
                or not str(shot.get("prompt", "")).strip()
                or not str(shot.get("image_prompt", "")).strip()
            ):
                raise ValueError("Le scénario contient un plan sans prompt.")
            if blocking and not shot.get("blocking"):
                shot["blocking"] = blocking
            try:
                duration = float(shot.get("duration_sec") or 0)
            except (TypeError, ValueError):
                duration = 0
            if not 1 <= duration <= 15:
                if version == 2:
                    raise _NativeValidationError(
                        f"scenes[{scene_index}].shots[{shot_index}].duration_sec: "
                        "chaque prise doit durer entre 1 et 15 secondes. "
                        "Découper l'échange en prises utiles en conservant les paroles, leur ordre "
                        "et les locuteurs ; ne pas étirer les silences ni réécrire le scénario.",
                        [["scenes", scene_index, "shots"]])
                raise ValueError("Chaque plan doit viser une durée utile entre 1 et 15 secondes.")
            duration = round(duration, 1)
            turns = shot.get("dialogue_turns") or []
            if not isinstance(turns, list) or len(turns) > 30:
                raise ValueError("Les tours de parole doivent être une liste ordonnée.")
            for turn_index, turn in enumerate(turns):
                for key in ("action", "reaction"):
                    if key in turn:
                        validate_h3_prose(turn[key])
            dialogue = (
                " ".join(t["text"] for t in turns)
                if turns
                else str(shot.get("dialogue") or "").strip()
            )
            words = speech_word_count(dialogue)
            if version == 2 and words > duration * MAX_SPEECH_WORDS_PER_SEC + 1e-6:
                speech_errors.append(
                    f'scenes[{scene_index}].shots[{shot_index}]: parole trop rapide '
                    f'({words} mots en {duration:g} s), au moins '
                    f'{words / MAX_SPEECH_WORDS_PER_SEC:.2f} s nécessaires.')
                speech_paths.append(['scenes', scene_index, 'shots', shot_index, 'duration_sec'])
            speaker = canonical_key(shot.get("speaker"), all_entities, "character")
            if turns:
                voices = {t["speaker"] for t in turns}
                speaker = turns[0]["speaker"] if len(voices) == 1 else ""
            try:
                if not turns:
                    dialogue, speaker = normalize_dialogue(dialogue, speaker, all_entities)
            except ValueError as exc:
                field = f"scenes[{scene_index}].shots[{shot_index}].dialogue"
                excerpt = str(shot.get("dialogue"))[:240]
                raise ValueError(f"{field}: {exc} Extrait brut: {excerpt!r}") from None
            if not dialogue:
                speaker = ""
            if speaker and speaker not in characters:
                raise ValueError(
                    f"scenes[{scene_index}].shots[{shot_index}].speaker: "
                    f"Le locuteur {speaker!r} est inconnu. "
                    f"Clés de personnages autorisées: {sorted(characters)!r}."
                )
            if dialogue and not speaker and not turns:
                raise ValueError("Chaque réplique doit avoir un unique locuteur identifié.")
            keys = shot.get("character_keys") or []
            if isinstance(keys, list):
                keys = [canonical_key(key, all_entities, "character") for key in keys]
            location = canonical_key(shot.get("location_key"), all_entities, "location")
            if (
                not isinstance(keys, list)
                or any(key not in (characters if version == 2 else known) for key in keys)
                or (location and location not in known)
            ):
                raise ValueError("Un plan utilise une référence étrangère à cette série.")
            speaking_keys = (
                [t["speaker"] for t in turns] if turns else [speaker] if dialogue else []
            )
            listeners = shot.get("listener_keys") or []
            if not isinstance(listeners, list):
                raise ValueError("Les personnages qui écoutent doivent être une liste de clés.")
            listeners = [canonical_key(key, all_entities, "character") for key in listeners]
            if any(key not in characters for key in listeners):
                raise ValueError("Un personnage qui écoute est inconnu dans cette série.")
            shot["listener_keys"] = listeners
            missing_keys = [
                key for key in dict.fromkeys(speaking_keys + listeners) if key not in keys
            ]
            if missing_keys:
                if version == 2:
                    raise _NativeValidationError(f"scenes[{scene_index}].shots[{shot_index}].character_keys: participants absents {missing_keys}.",
                                                 [["scenes", scene_index, "shots", shot_index, "character_keys"]])
                keys.extend(missing_keys)
                names = {e["key"]: e.get("name") or e["key"] for e in all_entities}
                participants = "speaking and listening" if listeners else "speaking"
                direction = (
                    f" Keep these {participants} characters visibly in the frame throughout the "
                    f"continuous shot: {', '.join(names[key] for key in missing_keys)}. "
                    "Use a composition that includes every speaking character; no offscreen speech."
                )
                for field in ("prompt", "image_prompt"):
                    shot[field] = str(shot[field]) + direction
            shot["duration_sec"] = duration
            total_duration += duration
            shot["dialogue"] = dialogue
            if version == 2 and dialogue and not turns:
                shot["dialogue_turns"] = [{"speaker": speaker, "text": dialogue}]
            shot["speaker"] = speaker
            shot["character_keys"] = keys
            shot["location_key"] = location
            from calliope.studio.screen_text import ScreenText

            labels = shot.get("screen_text") or []
            if not isinstance(labels, list) or len(labels) > 12:
                raise ValueError("Liste de textes à l'écran invalide.")
            try:
                shot["screen_text"] = [
                    ScreenText.model_validate(
                        label | {"number_format": profile["number_format"]}
                        if isinstance(label, dict) and label.get("value") is not None
                        else label
                    ).model_dump()
                    for label in labels
                ]
            except ValueError as exc:
                if version == 2:
                    raise _NativeValidationError(
                        f"scenes[{scene_index}].shots[{shot_index}].screen_text: {exc}",
                        [["scenes", scene_index, "shots", shot_index, "screen_text"]],
                    ) from exc
                raise
            for label in shot["screen_text"]:
                label["entity_key"] = canonical_key(label["entity_key"], all_entities, "character")
            if attached_required:
                missing = [
                    key
                    for key in keys
                    if key in required_values
                    and not any(
                        label["entity_key"] == key
                        and label["value"] is not None
                        and label["anchor"] == "character"
                        for label in shot["screen_text"]
                    )
                ]
                if missing:
                    raise ValueError(
                        f"scenes[{scene_index}].shots[{shot_index}].screen_text: "
                        f"valeurs attachées manquantes pour {', '.join(missing)}."
                    )
            label_keys = set()
            for label in shot["screen_text"]:
                if label["entity_key"] and label["entity_key"] not in keys:
                    raise ValueError("Un texte référence un personnage absent du plan.")
                if label["key"] in label_keys:
                    raise ValueError("Deux textes du même plan utilisent la même clé.")
                label_keys.add(label["key"])
                if attached_required and label["entity_key"] in required_values:
                    required_key, initial_value = required_values[label["entity_key"]]
                    if label["key"] != required_key:
                        raise ValueError(
                            f"Clé de valeur incohérente pour {label['entity_key']}: {required_key}."
                        )
                    prior_text = display_values.get(required_key)
                    expected_value = (
                        int(re.sub(r"[ ,]", "", prior_text))
                        if prior_text is not None
                        else initial_value
                    )
                    if label["value"] != expected_value and label["event"] not in (
                        "change",
                        "reveal",
                    ):
                        raise ValueError(
                            f"La valeur de {label['entity_key']} doit rester {expected_value} "
                            "sans événement structuré."
                        )
                changes = label.get("changes", [])
                if changes and label["event"] not in ("change", "reveal"):
                    raise ValueError(
                        "Un changement temporel exige un événement narratif change/reveal."
                    )
                if any(change["at_sec"] >= duration for change in changes):
                    raise ValueError(
                        "Un changement de valeur dépasse la durée utile prévue du plan."
                    )
                previous = display_values.get(label["key"])
                if (
                    previous is not None
                    and previous != label["text"]
                    and label["event"] not in ("change", "reveal")
                ):
                    raise ValueError("Une valeur affichée change sans événement narratif.")
                final_label = (
                    ScreenText.model_validate(label | {"value": changes[-1]["value"]})
                    if changes
                    else None
                )
                display_values[label["key"]] = final_label.text if final_label else label["text"]
            if shot["screen_text"]:
                from calliope.studio.visual_identity import strip_generated_text

                blank = (
                    " Leave clean blank space for the exact labels added in editing; "
                    "generate no text or digits."
                )
                for key in ("prompt", "image_prompt"):
                    shot[key] = strip_generated_text(shot[key], shot["screen_text"]) + blank
            shot["audio_mode"] = "native" if dialogue else shot.get("audio_mode", "native")
            if shot["audio_mode"] not in ("native", "silent"):
                raise ValueError("Mode audio invalide.")
            if not dialogue and profile["preset"] == "fast_dialogue_social_fiction":
                shot["audio_mode"] = "silent"
            shot["prompt"] = re.sub(
                r"<d>.*?</d>", "", str(shot["prompt"]), flags=re.S | re.I
            ).strip()
            for field in ("action", "reaction", "framing", "camera", "soundscape", "blocking"):
                if field in shot and (not isinstance(shot[field], str) or len(shot[field]) > 5000):
                    raise ValueError("Une directive de réalisation est invalide.")
                if field in shot:
                    validate_h3_prose(shot[field])
    if speech_errors:
        raise _NativeValidationError(
            '\n'.join(speech_errors) + '\nRééquilibrez les durées sans changer les paroles, '
            'leur ordre ni leurs locuteurs. Conservez la durée totale visée.', speech_paths)
    if "target_seconds" in context:
        target = float(context["target_seconds"])
        tolerance = max(2.0, target * 0.1)
        if total_duration < target - tolerance - 1e-6:
            if version == 2 and not context.get('studio_director'):
                review = result.get("editorial_review")
                if not isinstance(review, dict):
                    review = {}
                warning = (f"Durée narrative prévue : {total_duration:g} s pour une cible de {target:g} s. "
                           "Vérifier que l'histoire est complète ; ne pas ajouter de remplissage.")
                risks = str(review.get("risks") or "")
                if warning not in risks:
                    review["risks"] = (risks + "\n" + warning).strip()
                result["editorial_review"] = review
            else:
                message = (
                    f"Le scénario ne remplit que {total_duration:g} s utiles sur "
                    f"les {target:g} s du format : l'histoire est trop courte. "
                    "Développez l'événement ou ajoutez une action/réaction utile, "
                    "sans jamais étirer un plan existant."
                )
                if version == 2:
                    raise _NarrativeDurationError(message, [
                        ['scenes', i, 'shots'] for i in range(len(result['scenes']))])
                raise ValueError(message)
        if total_duration > target + tolerance + 1e-6:
            message = (
                f"La durée utile du scénario ({total_duration:g} s) dépasse la "
                f"durée demandée ({target:g} s). Ajustez le découpage ou les "
                "répliques, sans étirer les plans."
            )
            if version == 2:
                raise _NarrativeDurationError(message, [
                    ['scenes', i, 'shots'] for i in range(len(result['scenes']))])
            raise ValueError(message)
    raw_audio = result.get("audio") or {}
    if isinstance(raw_audio, dict):
        for cue in raw_audio.get("sfx") or []:
            if isinstance(cue, dict) and cue.get("effect") == "chime":
                cue["effect"] = "beep"
    result["audio"] = AudioSettings.model_validate(raw_audio).model_dump()
    profile = directing_profile(context.get("directing_profile"))
    result["audio"]["music_gain_db"] = profile["music_gain_db"]
    result["audio"]["ambience_gain_db"] = profile["ambience_gain_db"]
    result["display_values"] = display_values
    for shot_index, shot in enumerate(s for scene in result["scenes"] for s in scene["shots"]):
        for label in shot.get("screen_text", []):
            effect = {"appear": "click", "change": "beep", "reveal": "impact"}.get(label["event"])
            if effect and not any(c["shot_index"] == shot_index for c in result["audio"]["sfx"]):
                result["audio"]["sfx"].append(
                    {
                        "effect": effect,
                        "shot_index": shot_index,
                        "at_sec": label.get("changes", [{}])[0].get("at_sec", 0.15)
                        if label.get("changes")
                        else 0.15,
                        "gain_db": -18,
                    }
                )
    result["bible"] = {
        key: str(value) for key, value in (result.get("bible") or {}).items() if key in BIBLE_FIELDS
    }
    if not isinstance(result.get("events", []), list):
        raise ValueError("Le récapitulatif de continuité est invalide.")
    if version == 2:
        result["speaker_ids"] = _speaker_ids_for_plan(result)
    return result
