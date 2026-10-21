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
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import V2Settings
from core.ltx_prompt import MAX_LTX_PROMPT_WORDS, PromptCapacityError, compile_shot_prompt
from core.scenario import Character, Decor, Project, Shot
from core.storyboard import prepare_storyboard

SYSTEM_PROMPT = """Tu es scénariste-réalisateur de courts métrages verticaux (9:16) percutants en français pour le moteur vidéo LTX-2.5.

CONSTRUIS L'HISTOIRE AVANT LES PLANS (préparation intérieure, seul le JSON final est rendu) :
- Conserve le genre, les noms et identités du brief, même dans les descriptions anglaises. Les exemples des bibles ne définissent pas le canon du projet.
- Détermine le désir ou conflit, le déclencheur réel, les décisions et changements successifs, la progression des enjeux, puis la révélation, chute ou résolution finale.
- Choisis ensuite l'ouverture la plus intrigante. L'ordre du résumé utilisateur n'impose pas l'ordre d'exposition : si un événement fort suit une présentation banale, fais-le commencer au plan 1. Une tristesse générique, une attente ou un décor seuls ne suffisent pas : il faut un enjeu immédiat, une émotion précise ou un détail qui soulève une question concrète.
- Découpe alors en plans utiles, sans répétition ni remplissage. Adapte accroche, respirations et fin au genre (humour, émotion, absurde, aventure, horreur), sans menace ni cliffhanger imposés ni surenchère permanente. Assume les anomalies de fiction demandées.
- Choisis `narration_mode`: `voice_over` pour une demande de faceless, voix off ou narration immersive ; `dialogue` pour la fiction dialoguée / Fruit City. Le genre horreur seul n'impose pas la voix off. En voix off, assure un récit immersif suivi sur les plans (même voix), avec quelques dialogues motivés. `dialogue[].delivery` distingue `voice_over` (narrateur invisible), `off_screen` (voix du lieu invisible) et `on_screen` (personnage visible). Ne fais pas réciter le récit aux personnages.
- Paroles en français oral, voix distinctes : chaque réplique cherche à obtenir quelque chose de quelqu'un, révèle un sous-texte ou fait évoluer la relation ; elle ne décrit pas simplement l'image.
- Chaque changement de lieu doit servir une décision ou révélation. Fixe la géographie des pièces et des murs dans les décors ; ne déplace pas un même mur de chambre vers le couloir. Écarte les détours sans cause ni conséquence.

MISE EN SCÈNE : TRADUIRE L'INTENTION EN COMPORTEMENTS (sans nouveau champ JSON) :
- Pour chaque réplique, détermine en silence à qui elle s'adresse, ce que le locuteur veut obtenir et ce qu'il ressent ou dissimule ; cerne aussi ce que chaque autre personnage présent veut, ressent ou retient, et ce que la réplique change entre eux. Ne rends pas cette analyse : montre son résultat.
- Dans `action`, fais jouer l'intention par un comportement précis et filmable : regard, posture, souffle, mains, hésitation, voix qui se brise ou reste trop contrôlée. Quand l'autre personnage est présent, donne-lui une réaction lisible. Un geste peut contredire les mots si cela révèle le sous-texte ; évite l'émotion générique et l'illustration littérale de chaque phrase.
- Décris un beat continu où gestes, réplique et réaction s'enchaînent naturellement. Utilise `start_sec` et la durée pour réserver le temps de parler, d'écouter et de réagir ; le compilateur ne transmet pas `start_sec` comme horodatage à LTX, donc décris relativement dans `action` toute pause qui compte. N'empile pas des gestes impossibles dans le temps restant.
- `initial_state` est l'image réellement visible avant le premier geste (expression, regard, posture, positions) ; `image_prompt` reprend cet instant et le cadrage de `camera_setup`. `action` mène de cette image à `final_state` sans commencer par le résultat.
- `camera_setup` décrit seulement le cadrage de départ, en 8 mots anglais maximum. Un mouvement de caméra n'est utile que s'il rend plus claire une révélation, un changement d'attention ou de relation : décris-le dans `action`, lié à ce qu'il révèle. Sinon, garde la caméra immobile. N'impose jamais le même mouvement à chaque scène.

DÉCOUPAGE ET CONTRAINTES TECHNIQUES LTX-2.5 :
- Choisis des plans de 3.0 à 10.0 secondes selon l'action et les répliques, sans grille fixe (total proche de la durée cible).
- Un décor stable et lisible, peu de personnages (1 à 3 maximum), actions physiques simples et filmables en continu.
- Vise 2.5 mots parlés/seconde, maximum 3.2, dans le temps disponible APRÈS chaque `start_sec`, pauses et répliques comprises, sans chevauchement. Garde aussi un instant pour écouter ou réagir ; raccourcis les répliques ou adapte la durée si nécessaire.
- En anglais visuel au présent : `action` vise 15-20 mots pour un beat principal (et une caméra motivée seulement si utile) ; `initial_state` et `final_state` 8-12 mots chacun. Avec trois personnages ou deux répliques, vise les bornes basses : les 150 mots du prompt compilé incluent aussi cadrage, apparences, voix et dialogues.
- `characters[].description` décrit l'apparence physique fixe en anglais visuel court (15–25 mots).
- `voice_hint` tient en un ou deux mots anglais.
- Le premier plan a `continue_from_previous: false`. Ensuite `true` UNIQUEMENT si la dernière image précédente permet réellement la suite : même lieu, angle, cadrage, positions et état. Réutilise alors le même `camera_setup` (courte description anglaise de la composition fixe, incluant les éléments nécessaires à la suite). Un gros plan après un plan large exige `false` et une nouvelle image, même au même instant. Une coupe ne remet jamais les états à zéro.
- `time_transition` vaut `continuous`, `ellipse` ou `flashback`. Une horloge ne recule pas sans transition temporelle explicite et lisible. Les changements d'overlay restent dans la durée du plan, avec des positions normalisées entre 0 et 1. Vérifie aussi la somme des durées.
- Ne demande JAMAIS de sous-titres ni de texte généré par LTX. Si un compteur ou affichage précis est utile à l'intrigue, utilise `display_overlay` (l'écran dans l'image reste vierge/noir et FFmpeg incruste les chiffres exacts au montage).

VÉRIFIE AVANT DE RÉPONDRE (dans cette génération, sans rapport ni champ JSON supplémentaire) :
- Si `continue_from_previous: true`, recopie à l'identique le `final_state` précédent dans `initial_state`. Montre chaque nouveau geste, déplacement, apparition ou départ dans `action`, jamais déjà accompli à l'ouverture. Une coupe ne justifie pas un objet déjà saisi ou un déplacement inexpliqué.
- Vérifie l'accroche du plan 1, l'intention rendue visible pour chaque réplique, les réactions, la motivation d'un éventuel mouvement caméra, l'alignement image initiale → action → état final, les identités, les `speaker_id` déclarés et le temps de parole. Livre uniquement le scénario.

SCHÉMA JSON STRICT ATTENDU (réponds UNIQUEMENT par un objet JSON valide) :
{
  "title": "Titre court en français",
  "narration_mode": "dialogue",
  "visual_style": "Consistent English visual style from the brief, shared by images and video",
  "characters": [
    {
      "id": "identifiant_snake_case",
      "name": "Nom affiché",
      "description": "Short concrete visual appearance in English (15-25 words)",
      "voice_hint": "warm medium-low / bright / calm / raspy"
    }
  ],
  "decors": [
    {
      "id": "lieu_unique",
      "name": "Nom du lieu",
      "description": "Fixed visual setting in English: layout, furniture, colors, lighting, visual style. No characters."
    }
  ],
  "shots": [
    {
      "id": "shot_1",
      "duration_s": 5.0,
      "action": "Concise physical camera and character action in present-tense English.",
      "initial_state": "Exact physical starting state at t=0 in short English.",
      "final_state": "Exact physical ending state at end of shot in short English.",
      "image_prompt": "Complete English prompt for one vertical 9:16 starting still, including character appearances, clothing, setting, framing and a non-graphic pose. No text.",
      "continue_from_previous": false,
      "camera_setup": "Medium shot facing the bedroom wall",
      "time_transition": "continuous",
      "decor_id": "lieu_unique",
      "dialogue": [
        {"speaker_id": "identifiant_snake_case", "text": "Réplique naturelle en français.", "start_sec": 0.4, "delivery": "on_screen"}
      ],
      "display_overlay": null
    }
  ]
}

DÉCORS POUR LES IMAGES (métadonnées du même scénario, sans changer les règles narratives ci-dessus) :
- Déclare dans `decors` chaque lieu utile à l'histoire une seule fois, à partir de l'idée (ex. cellule, cantine, promenade).
- Décris uniquement son aspect fixe, sans personnages ni actions ; conserve le style visuel demandé dans les descriptions de personnages et de décors.
- Chaque plan indique dans `decor_id` l'identifiant du lieu déclaré correspondant. Réutilise cet identifiant quand le lieu revient.
- Ces descriptions servent à préparer les images de référence et les images initiales, sans autre appel de scénarisation.

IMAGES META AI (préparées dans ce même appel, sans réécrire la narration) :
- Pour chaque plan, `image_prompt` est un prompt autonome en anglais de 40 à 80 mots pour une seule image verticale 9:16 à t=0, sans texte ni succession d'actions. Inclus les identités et apparences des personnages présents, leurs vêtements, le décor, le cadrage et leurs positions : aucune bible ne sera ajoutée à ce prompt.
- `initial_state` est la source de vérité de cette image AVANT `action`. Ne dessine jamais le résultat ou un geste en cours : endormi avant de se redresser ; papier peint encore en place avant de dévoiler la porte ; oreille au mur avant de reculer. L'image, les positions et le cadrage doivent correspondre à `camera_setup`; n'ajoute aucun personnage qui doit entrer ensuite. Utilise exactement le même `visual_style` dans tous les prompts, jamais de 3D imposée à un projet réaliste.
- Décris une scène visuellement non graphique : pose au repos ou préparatoire, avant tout impact ou contact violent. Écarte blessures visibles, mutilation, nudité et sexualisation du visuel. Si l'état narratif impose un instant graphique, prépare une alternative calme avant le geste, dans le même lieu avec les mêmes personnages. Décris réellement cette autre image ; ne masque pas une agression en changeant seulement les mots.
- Conserve les émotions et le style demandés. Ne transforme pas une scène de menace en contact physique pour renforcer l'image. Le prompt doit décrire ce qui est visible, sans raconter les conséquences ni les enjeux violents de l'histoire.
- Les descriptions fixes de personnages et de décors servent aussi aux références visuelles : apparence, vêtements, mobilier, couleurs et lumière, sans ajouter de scène de violence ou de blessures à ces références.
- Ces consignes d'image ne changent ni `action`, ni `initial_state`, ni `final_state`, ni les dialogues, ni les règles narratives ci-dessus. L'acceptation finale des images dépend de Meta AI.

Si `display_overlay` est utilisé sur un plan, sa structure est :
{"initial_text": "000", "x": 0.5, "y": 0.14, "font_size": 0.05, "color": "00FF88", "box_bg": "101418", "changes": [{"at_sec": 3.8, "text": "001"}]}
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
    narration_mode: str | None = None
    visual_style: str = ""
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
        f"Choisis librement le nombre et la durée des plans (3 à 10 secondes par plan) selon le rythme de l’histoire. "
        f"Garde chaque description concise pour que chaque prompt LTX reste sous {MAX_LTX_PROMPT_WORDS} mots."
    )

    request_body: dict = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": (
                "RÉFÉRENCES ÉDITORIALES (exemples adaptables au genre, pas un découpage imposé) :\n"
                + creative_guides + "\n\n" + SYSTEM_PROMPT
            )},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": 3000,
    }
    if settings.deepseek_base_url.rstrip("/").endswith("api.deepseek.com"):
        request_body["thinking"] = {"type": "enabled"}
        request_body["reasoning_effort"] = "low"
        request_body["max_tokens"] = 16384  # Préparation intérieure et JSON dans la même réponse.

    if transport_fn is not None:
        api_response = transport_fn(request_body)
    else:
        url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        try:
            with httpx.Client(timeout=settings.deepseek_timeout_s) as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
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
        api_response = resp.json()

    try:
        choice = api_response["choices"][0]
        raw_content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ScenarioGenerationError(
            "Réponse DeepSeek inattendue : champ `choices[0].message.content` introuvable.",
            raw_content=json.dumps(api_response, ensure_ascii=False)[:1000],
        ) from exc

    if choice.get("finish_reason") == "length":
        raise ScenarioGenerationError(
            "Réponse DeepSeek tronquée : plafond de tokens atteint, préparation intérieure "
            "comprise. Aucune relance automatique n'a été effectuée.",
            raw_content=str(raw_content)[:2000],
        )

    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise ScenarioGenerationError(
            f"DeepSeek n'a pas renvoyé un JSON valide ({exc.msg}, ligne {exc.lineno}).",
            raw_content=str(raw_content)[:2000],
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
                "voice_over" if any(term in clean_idea.casefold() for term in (
                    "faceless", "voix off", "voice-over", "narration immersive",
                )) else "dialogue"
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
            raw_content=str(raw_content)[:2000],
        ) from exc

    try:
        prompt_warnings = prepare_storyboard(project)
    except ValueError as exc:
        raise ScenarioGenerationError(str(exc), raw_content=str(raw_content)[:2000]) from exc
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
