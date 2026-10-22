"""Appel unique à l'API DeepSeek avec validation Pydantic V2 stricte.

Règles V2 :
- Une seule requête HTTP par tentative explicite de l'utilisateur.
- Aucun second agent critique autonome, aucune boucle de réécriture cachée.
- Les anomalies volontaires de fiction (compteur magique, fruit parlant, paradoxe)
  sont encouragées et jamais bloquées.
- Si la sortie JSON est invalide, l'erreur exacte est remontée à l'utilisateur.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import V2Settings
from core.ltx_prompt import MAX_LTX_PROMPT_WORDS, PromptCapacityError, compile_shot_prompt
from core.scenario import Character, Decor, Project, Shot
from core.storyboard import build_shot_image_prompt, prepare_storyboard

SYSTEM_PROMPT = """Tu es scénariste-réalisateur de courts métrages verticaux (9:16) percutants en français pour le moteur vidéo LTX-2.5.

CONSTRUIS L'HISTOIRE AVANT LES PLANS (préparation intérieure, seul le JSON final est rendu) :
- Conserve le genre, les noms et identités du brief, même dans les descriptions anglaises. Les exemples des bibles ne définissent pas le canon du projet.
- Le brief est le contrat : repère les événements, textes exacts, sons, interdictions et moments imposés. Prévois où chacun sera réellement exécuté dans les champs du scénario ; une exigence présente seulement dans l'idée n'est pas réalisée. N'ajoute ni parole ni voix off à un film demandé sans paroles.
- Détermine le désir ou conflit, le déclencheur réel, les décisions et changements successifs, la progression des enjeux, puis la révélation, chute ou résolution finale.
- Choisis ensuite l'ouverture la plus intrigante. L'ordre du résumé utilisateur n'impose pas l'ordre d'exposition : si un événement fort suit une présentation banale, fais-le commencer au plan 1. Une tristesse générique, une attente ou un décor seuls ne suffisent pas : il faut un enjeu immédiat, une émotion précise ou un détail qui soulève une question concrète.
- Découpe alors en plans utiles, sans répétition ni remplissage. Adapte accroche, respirations et fin au genre (humour, émotion, absurde, aventure, horreur), sans menace ni cliffhanger imposés ni surenchère permanente. Assume les anomalies de fiction demandées.
- Choisis `narration_mode`: `voice_over` pour une narration effectivement voulue et écrite ; `dialogue` pour les échanges ou un film sans parole (alors `dialogue: []` sur chaque plan). Le genre horreur ou le mot "voix off" dans une interdiction ne justifient pas un narrateur. En voix off, garde la même voix et un récit suivi. `dialogue[].delivery` distingue `voice_over` (narrateur invisible), `off_screen` (voix du lieu invisible) et `on_screen` (personnage visible). Ne fais pas réciter le récit aux personnages.
- Paroles en français oral, voix distinctes : chaque réplique cherche à obtenir quelque chose de quelqu'un, révèle un sous-texte ou fait évoluer la relation ; elle ne décrit pas simplement l'image.
- Chaque changement de lieu doit servir une décision ou révélation. Fixe la géographie des pièces et des murs dans les décors ; ne déplace pas un même mur de chambre vers le couloir. Écarte les détours sans cause ni conséquence.

MISE EN SCÈNE : TRADUIRE L'INTENTION EN COMPORTEMENTS (sans nouveau champ JSON) :
- Pour chaque réplique, détermine en silence à qui elle s'adresse, ce que le locuteur veut obtenir et ce qu'il ressent ou dissimule ; cerne aussi ce que chaque autre personnage présent veut, ressent ou retient, et ce que la réplique change entre eux. Ne rends pas cette analyse : montre son résultat.
- Dans `action`, fais jouer l'intention par un comportement précis et filmable : regard, posture, souffle, mains, hésitation, voix qui se brise ou reste trop contrôlée. Quand l'autre personnage est présent, donne-lui une réaction lisible. Un geste peut contredire les mots si cela révèle le sous-texte ; évite l'émotion générique et l'illustration littérale de chaque phrase.
- Décris un beat continu où gestes, réplique et réaction s'enchaînent naturellement. Utilise `start_sec` et la durée pour réserver le temps de parler, d'écouter et de réagir ; le compilateur ne transmet pas `start_sec` comme horodatage à LTX, donc décris relativement dans `action` toute pause qui compte. N'empile pas des gestes impossibles dans le temps restant.
- `initial_state` est l'image réellement visible au début du plan : expression, regard, posture, positions, objet tenu et état des accessoires. Nomme les personnages visibles et décris cet instant, avant les changements de `action`. Un mouvement déjà engagé au plan précédent peut se poursuivre ; ne remets pas les poses à zéro. Le moteur fabrique `image_prompt` depuis cet état, `camera_setup`, les apparences et le décor : n'écris pas une seconde scène pour l'image.
- `action` mène de cette image à `final_state` par une chaîne filmable. Pour un gag ou un effet de suspense, rends lisibles sa préparation, son déclenchement et sa conséquence, sur plusieurs plans si nécessaire. Un personnage ne tient pas déjà l'objet qu'il doit saisir. Les sons utiles, bips, impacts, ambiance, musique ou silence demandés sont décrits ici, liés aux gestes ; ils sont des consignes à l'audio natif, pas des événements synchronisés garantis.
- `camera_setup` décrit seulement le cadrage de départ, en 8 mots anglais maximum. Un mouvement de caméra n'est utile que s'il rend plus claire une révélation, un changement d'attention ou de relation : décris-le dans `action`, lié à ce qu'il révèle. Sinon, garde la caméra immobile. N'impose jamais le même mouvement à chaque scène.

DÉCOUPAGE ET CONTRAINTES TECHNIQUES LTX-2.5 :
- Chaque élément de `shots` coûte une génération vidéo distincte. Privilégie des prises cohérentes regroupant action et réaction : pour 60 secondes, vise généralement 6 à 8 plans, avec des durées adaptées dans les limites du schéma. Ce repère n'est pas un quota ; un nombre de plans explicitement imposé par l'utilisateur prime. Réserve les prises très courtes aux inserts indispensables. Un rythme rapide vient aussi des gestes, des répliques et de leur enchaînement dans un même plan ; ne le traduis pas automatiquement en davantage de générations. Ne promets pas de coupes internes précises dans une seule génération LTX.
- Prépare intérieurement une seule timeline cumulée de tout l'épisode avant de détailler les plans. Place les événements imposés aux moments demandés, puis calcule les durées ; ne remplis pas la cible avec des attentes répétées. Une coupe sert une action, une information, un point de vue ou une réaction, pas seulement un changement de taille de plan.
- Un décor stable et lisible, peu de personnages (1 à 3 maximum), actions physiques simples et filmables en continu.
- Vise 2.5 mots parlés/seconde, maximum 3.2, dans le temps disponible APRÈS chaque `start_sec`, pauses et répliques comprises, sans chevauchement. Garde aussi un instant pour écouter ou réagir ; raccourcis les répliques ou adapte la durée si nécessaire.
- En anglais visuel au présent : reste concis sans sacrifier les gestes, réactions et intentions utiles. Le garde-fou local de 300 mots du prompt compilé inclut cadrage, apparences, voix et dialogues ; ce n'est pas une limite native de LTX.
- `characters[].description` décrit l'apparence physique fixe en anglais visuel court (15–25 mots).
- `voice_hint` tient en un ou deux mots anglais.
- Le premier plan a `continue_from_previous: false`. Ensuite `true` UNIQUEMENT si la dernière image précédente permet réellement la suite : même lieu, angle, cadrage, positions et état. Réutilise alors le même `camera_setup` (courte description anglaise de la composition fixe, incluant les éléments nécessaires à la suite). Un gros plan après un plan large exige `false` et une nouvelle image, même au même instant. Une coupe ne remet jamais les états à zéro.
- `time_transition` vaut `continuous`, `ellipse` ou `flashback`. Un compteur dépend de la timeline cumulée, même lorsqu'il est hors champ. Pour un décompte normal, retire le temps écoulé ; pour un temps fictionnel accéléré, rends le changement de règle explicite. Les changements de texte utilisent `display_overlay.changes[].at_sec`, relatif au début du plan, et restent dans sa durée. Prévois le temps de lecture des messages, pas seulement les chiffres.
- Ne demande JAMAIS de sous-titres ni de texte généré par LTX. Si un compteur ou affichage précis est utile à l'intrigue, utilise `display_overlay` (l'écran dans l'image reste vierge/noir et FFmpeg incruste les chiffres exacts au montage).

VÉRIFIE AVANT DE RÉPONDRE (dans cette génération, sans rapport ni champ JSON supplémentaire) :
- À chaque coupe en temps continu, vérifie qui est où, ce qui est tenu, renversé, mouillé ou ouvert, et ce qui vient de changer. Montre chaque nouveau geste, déplacement, apparition ou départ dans `action`, jamais déjà accompli sans cause à l'ouverture. Si `continue_from_previous: true`, recopie à l'identique le `final_state` précédent dans `initial_state` et conserve la composition.
- Relis le film dans l'ordre : enjeu compris dès l'ouverture, exigences du brief présentes dans les champs exécutés, actions et réactions qui progressent, raccords physiques, temps de parole, somme des durées et compteurs. Ajuste ces choix dans cette même réponse, sans autre appel ni rapport d'évaluation. Livre uniquement le scénario.

SCHÉMA JSON STRICT ATTENDU : réponds uniquement par l'objet conforme au schéma fourni par l'API. Utilise les identifiants déclarés dans `characters` et `decors`. Écris les descriptions visuelles et actions en anglais concret, les dialogues et textes affichés dans la langue du brief. `title` est un titre court ; `visual_style` conserve le style demandé pour toutes les images et vidéos. Les chemins de fichiers, validations, images et prompts compilés sont gérés par le moteur.

DÉCORS POUR LES IMAGES (métadonnées du même scénario, sans changer les règles narratives ci-dessus) :
- Déclare dans `decors` chaque lieu utile à l'histoire une seule fois, à partir de l'idée (ex. cellule, cantine, promenade).
- Décris uniquement son aspect fixe, sans personnages ni actions ; conserve le style visuel demandé dans les descriptions de personnages et de décors.
- Chaque plan indique dans `decor_id` l'identifiant du lieu déclaré correspondant. Réutilise cet identifiant quand le lieu revient.
- Ces descriptions servent à préparer les images de référence et les images initiales, sans autre appel de scénarisation.

IMAGES META AI (exécution technique du même scénario) :
- `initial_state` est la source de vérité de l'image à t=0. Écris-le comme une photographie précise, avec les noms des personnages effectivement visibles et la disposition des accessoires utiles. Le moteur lui associe leurs descriptions fixes, le décor et `camera_setup`. Les inserts sans personnage et le noir n'héritent pas du casting de l'épisode.
- Ne décris pas une conséquence future dans cet état : pieds encore stables avant de glisser ; tasse encore droite avant d'être renversée ; mains prêtes avant de pousser. Un écran qui recevra une incrustation reste vierge. Pour un noir, indique seulement un cadre noir sans objets ni personnages, avec `decor_id: null`.
- Choisis dès la mise en scène un départ et un cadrage visuellement non graphiques, avant l'impact ou avec l'élément graphique hors champ. Écarte blessures visibles, mutilation, nudité et sexualisation de l'image initiale. Conserve les personnages, le lieu et les conséquences narratives ; ne masque pas une agression en changeant seulement les mots.
- Conserve les émotions et le style demandés. Ne transforme pas une scène de menace en contact physique pour renforcer l'image. Le prompt doit décrire ce qui est visible, sans raconter les conséquences ni les enjeux violents de l'histoire.
- Les descriptions fixes de personnages et de décors servent aussi aux références visuelles : apparence, vêtements, mobilier, couleurs et lumière, sans ajouter de scène de violence ou de blessures à ces références.
- Ces choix de départ servent la même histoire, sans détourner les événements ni les dialogues du brief. L'acceptation finale des images dépend de Meta AI.

Si `display_overlay` est utilisé sur un plan, sa structure est :
{"initial_text": "000", "x": 0.5, "y": 0.14, "font_size": 0.05, "color": "00FF88", "box_bg": "101418", "changes": [{"at_sec": 3.8, "text": "001"}]}
L'incrustation reste fixe dans l'image, sans suivi de mouvement ni de perspective : prévois un support immobile dans le cadre, ou un cartouche graphique indépendant des objets mobiles.
`initial_text` et `changes[].text` acceptent une chaîne vide pour ne rien afficher pendant cet intervalle (apparition retardée ou disparition). Utilise `display_overlay: null` si aucune incrustation n'est prévue sur le plan. Chaque texte est limité à 80 caractères.
"""


class ScenarioGenerationError(ValueError):
    """Erreur explicite renvoyée à l'utilisateur sans relance automatique du LLM."""

    def __init__(self, message: str, raw_content: str = ""):
        super().__init__(message)
        self.raw_content = raw_content


# Documents éditoriaux locaux : consultés à CHAQUE génération, pas de service externe.
_CREATIVE_GUIDES_DIR = Path(__file__).resolve().parents[1] / "creative-guides"
_CREATIVE_GUIDE_FILES = ("01_attention.md", "02_retention.md", "03_narration.md")


def load_creative_guides() -> tuple[str, str]:
    """Retourne les trois guides et leur empreinte courte, sans appel réseau."""
    sections: list[str] = []
    for name in _CREATIVE_GUIDE_FILES:
        path = _CREATIVE_GUIDES_DIR / name
        try:
            guide = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ScenarioGenerationError(
                f"Bible créative indisponible : {path} ({type(exc).__name__}: {exc})."
            ) from exc
        if not guide:
            raise ScenarioGenerationError(f"Bible créative vide : {path}.")
        sections.append(f"### {name}\n{guide}")

    content = "\n\n".join(sections)
    revision = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return content, revision


class ScenarioDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    narration_mode: Literal["dialogue", "voice_over"] | None = None
    visual_style: str = Field(default="", max_length=160)
    characters: list[Character] = Field(min_length=1)
    decors: list[Decor] = Field(default_factory=list)
    shots: list[Shot] = Field(min_length=1)


def generate_scenario_once(
    *,
    settings: V2Settings,
    idea: str,
    target_duration_s: float = 15.0,
    project_id: str | None = None,
    transport_fn: Callable[[dict], dict] | None = None,
) -> tuple[Project, dict]:
    """Effectue UN SEUL appel DeepSeek et valide la réponse avec Pydantic V2."""
    clean_idea = str(idea or "").strip()
    if not clean_idea:
        raise ScenarioGenerationError("Saisissez une idée avant de demander un scénario.")

    api_key = settings.effective_deepseek_key()
    if not api_key and transport_fn is None:
        raise ScenarioGenerationError(
            "Clé API DeepSeek absente. Renseignez `DEEPSEEK_API_KEY` ou le champ "
            "correspondant dans Paramètres (`studio-v2/config.json`)."
        )

    creative_guides, creative_guides_version = load_creative_guides()

    user_message = (
        f"IDÉE DE L'UTILISATEUR :\n{clean_idea}\n\n"
        f"DURÉE TOTALE VISÉE : environ {target_duration_s:g} secondes en format vertical 9:16 "
        f"Respecte un nombre de plans explicitement imposé ; sinon privilégie peu de prises cohérentes, "
        f"sans assimiler un rythme rapide à davantage de générations. "
        f"Garde chaque description concise pour que chaque prompt LTX reste sous {MAX_LTX_PROMPT_WORDS} mots."
    )

    official_api = settings.deepseek_base_url.rstrip("/").endswith("api.deepseek.com")
    schema = ScenarioDraft.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"].append("narration_mode")
    # The director supplies creative fields; local file paths and render state belong to the engine.
    for name, fields in {
        "Character": ("image_ref",),
        "Decor": ("asset_id",),
        "Shot": ("image_prompt", "image_ref", "decor_asset_id", "ltx_prompt", "seed", "image_validated",
                 "rendered_video_ref", "human_validated"),
    }.items():
        for field in fields:
            schema["$defs"][name]["properties"].pop(field)
    shot_schema = schema["$defs"]["Shot"]
    for field in ("initial_state", "final_state", "camera_setup"):
        shot_schema["required"].append(field)
        shot_schema["properties"][field]["minLength"] = 1
    system_message = (
        "RÉFÉRENCES ÉDITORIALES (exemples adaptables au genre, pas un découpage imposé) :\n"
        + creative_guides + "\n\n" + SYSTEM_PROMPT
    )
    if not official_api:
        system_message += "\nCONTRAT JSON V2 :\n" + json.dumps(schema, ensure_ascii=False)

    request_body: dict = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": 3000,
    }
    if official_api:
        request_body["thinking"] = {"type": "enabled"}
        request_body["reasoning_effort"] = "low"
        request_body["max_tokens"] = 65536  # Budget partagé entre préparation et storyboard complet.

    if transport_fn is not None:
        api_response = transport_fn(request_body)
    else:
        url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        http_body = request_body
        if official_api:
            url = settings.deepseek_base_url.rstrip("/") + "/responses"
            http_body = {
                "model": request_body["model"],
                "input": request_body["messages"],
                "reasoning": {"effort": "low"},
                "max_output_tokens": request_body["max_tokens"],
                "text": {"format": {"type": "json_schema", "name": "studio_director_v2", "schema": schema}},
            }
        try:
            timeout = httpx.Timeout(settings.deepseek_timeout_s,
                                    read=max(settings.deepseek_timeout_s, 300) if official_api else settings.deepseek_timeout_s)
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=http_body,
                )
        except httpx.HTTPError as exc:
            raise ScenarioGenerationError(
                f"Échec réseau lors de l'appel unique DeepSeek ({type(exc).__name__}): {exc}"
            ) from exc

        if resp.status_code >= 400:
            detail = resp.text[:600]
            raise ScenarioGenerationError(
                f"DeepSeek a renvoyé HTTP {resp.status_code} : {detail}"
            )
        try:
            api_response = resp.json()
        except ValueError as exc:
            raise ScenarioGenerationError("DeepSeek a renvoyé une réponse HTTP non JSON.") from exc
        if official_api:
            if not isinstance(api_response, dict):
                raise ScenarioGenerationError("Réponse structurée DeepSeek inattendue : objet JSON requis.")
            content = "".join(
                part.get("text", "")
                for item in (api_response.get("output") or []) if item.get("type") == "message"
                for part in (item.get("content") or []) if part.get("type") == "output_text"
            )
            status = api_response.get("status")
            if status != "completed":
                reason = (api_response.get("incomplete_details") or {}).get("reason")
                detail = (api_response.get("error") or {}).get("message") or reason or status
                label = "tronquée" if reason == "max_output_tokens" else "incomplète ou échouée"
                raise ScenarioGenerationError(f"Réponse DeepSeek {label} : {detail}.", raw_content=content)
            usage = api_response.get("usage") or {}
            api_response = {
                "model": api_response.get("model"),
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": usage.get("input_tokens", 0),
                          "completion_tokens": usage.get("output_tokens", 0)},
            }

    try:
        choice = api_response["choices"][0]
        raw_content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ScenarioGenerationError(
            "Réponse DeepSeek inattendue : champ `choices[0].message.content` introuvable.",
            raw_content=json.dumps(api_response, ensure_ascii=False)[:1000],
        ) from exc

    if not isinstance(raw_content, str) or not raw_content.strip():
        raise ScenarioGenerationError("DeepSeek a renvoyé un contenu vide ou non textuel.")

    if choice.get("finish_reason") == "length":
        raise ScenarioGenerationError(
            "Réponse DeepSeek tronquée : plafond de tokens atteint, préparation intérieure "
            "comprise. Aucune relance automatique n'a été effectuée.",
            raw_content=str(raw_content),
        )

    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ScenarioGenerationError(
            f"DeepSeek n'a pas renvoyé un JSON valide ({exc.msg}, ligne {exc.lineno}).",
            raw_content=str(raw_content),
        ) from exc

    try:
        draft = ScenarioDraft.model_validate(parsed)
        project = Project(
            id=project_id or f"proj-{uuid.uuid4().hex[:8]}",
            title=draft.title.strip() or clean_idea[:60],
            idea=clean_idea,
            aspect_ratio="9:16",
            target_duration_s=target_duration_s,
            narration_mode=draft.narration_mode or (
                "voice_over" if any(line.delivery == "voice_over" for shot in draft.shots
                                    for line in shot.dialogue) else "dialogue"
            ),
            visual_style=draft.visual_style,
            characters=draft.characters,
            decors=draft.decors,
            shots=draft.shots,
        )
    except ValidationError as exc:
        formatted = "; ".join(
            f"{'.'.join(map(str, err['loc']))}: {err['msg']}"
            for err in exc.errors(include_input=False)
        )
        raise ScenarioGenerationError(
            f"Scénario DeepSeek invalide selon le schéma V2 : {formatted}",
            raw_content=str(raw_content),
        ) from exc

    try:
        for shot in project.shots:
            if not shot.image_prompt and shot.initial_state.strip():
                shot.image_prompt = build_shot_image_prompt(project, shot)
        prompt_warnings = prepare_storyboard(project)
    except ValueError as exc:
        raise ScenarioGenerationError(str(exc), raw_content=str(raw_content)) from exc
    for shot in project.shots:
        try:
            shot.ltx_prompt = compile_shot_prompt(project, shot)
        except PromptCapacityError as exc:
            prompt_warnings.append(str(exc))

    usage = api_response.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    usd = (
        (prompt_tokens / 1_000_000) * settings.deepseek_input_usd_per_1m
        + (completion_tokens / 1_000_000) * settings.deepseek_output_usd_per_1m
    )
    eur = round(usd * settings.usd_to_eur, 6) if settings.usd_to_eur is not None else None

    meta = {
        "model": api_response.get("model") or settings.deepseek_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": round(usd, 6),
        "estimated_cost_eur": eur,
        "warnings": prompt_warnings,
        "creative_guides_version": creative_guides_version,
    }
    return project, meta
