"""Appel unique à l'API DeepSeek avec validation Pydantic V2 stricte.

Règles V2 :
- Une seule requête HTTP par tentative explicite de l'utilisateur.
- Aucun second agent critique autonome, aucune boucle de réécriture cachée.
- Les anomalies volontaires de fiction (compteur magique, fruit parlant, paradoxe)
  sont encouragées et jamais bloquées.
- Si la sortie JSON est invalide, l'erreur exacte est remontée à l'utilisateur.
"""

from __future__ import annotations

import json
import uuid
from typing import Callable

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import V2Settings
from core.ltx_prompt import MAX_LTX_PROMPT_WORDS, PromptCapacityError, compile_shot_prompt
from core.scenario import Character, Project, Shot

SYSTEM_PROMPT = """Tu es scénariste-réalisateur de courts métrages verticaux (9:16) percutants en français pour le moteur vidéo LTX-2.5.

OBJECTIFS CRÉATIFS :
- Hook visuel et verbal immédiat dès la première seconde.
- Dialogues parlés en français naturel, vif, oral, pleins de mauvaise foi, d'humour ou de tension.
- Suspense progressif et cliffhanger net sur le dernier plan.
- Les anomalies volontaires de fiction (fruits anthropomorphes, compteur mystérieux, objet impossible) font partie de l'histoire : assume-les pleinement.

CONTRAINTES TECHNIQUES LTX-2.5 RÉELLES :
- Chaque plan dure entre 3.0 et 10.0 secondes (total proche de la durée cible demandée).
- Un décor stable et lisible, peu de personnages (1 à 3 maximum), actions physiques simples et filmables en continu.
- Environ 2.5 à 3.2 mots parlés par seconde maximum pour laisser respirer le jeu.
- `action`, `initial_state` et `final_state` sont concis (1 à 2 phrases courtes en anglais visuel concret, au présent) afin que le prompt LTX compilé reste largement sous 150 mots.
- `characters[].description` décrit l'apparence physique fixe en anglais visuel court (15–25 mots).
- Le plan 1 a `continue_from_previous: false`. Les plans suivants dans le même décor continuent de préférence (`continue_from_previous: true`) pour garder les raccords cohérents.
- Ne demande JAMAIS de sous-titres ni de texte généré par LTX. Si un compteur ou affichage précis est utile à l'intrigue, utilise `display_overlay` (l'écran dans l'image reste vierge/noir et FFmpeg incruste les chiffres exacts au montage).

SCHÉMA JSON STRICT ATTENDU (réponds UNIQUEMENT par un objet JSON valide) :
{
  "title": "Titre court en français",
  "characters": [
    {
      "id": "identifiant_snake_case",
      "name": "Nom affiché",
      "description": "Short concrete visual appearance in English (15-25 words)",
      "voice_hint": "warm medium-low / bright / calm / raspy"
    }
  ],
  "shots": [
    {
      "id": "shot_1",
      "duration_s": 5.0,
      "action": "Concise physical camera and character action in present-tense English.",
      "initial_state": "Exact physical starting state at t=0 in short English.",
      "final_state": "Exact physical ending state at end of shot in short English.",
      "continue_from_previous": false,
      "dialogue": [
        {"speaker_id": "identifiant_snake_case", "text": "Réplique naturelle en français.", "start_sec": 0.4}
      ],
      "display_overlay": null
    }
  ]
}

Si `display_overlay` est utilisé sur un plan, sa structure est :
{"initial_text": "000", "x": 0.5, "y": 0.14, "font_size": 0.05, "color": "00FF88", "box_bg": "101418", "changes": [{"at_sec": 3.8, "text": "001"}]}
"""


class ScenarioGenerationError(ValueError):
    """Erreur explicite renvoyée à l'utilisateur sans relance automatique du LLM."""

    def __init__(self, message: str, raw_content: str = ""):
        super().__init__(message)
        self.raw_content = raw_content


class ScenarioDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    characters: list[Character] = Field(min_length=1)
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

    user_message = (
        f"IDÉE DE L'UTILISATEUR :\n{clean_idea}\n\n"
        f"DURÉE TOTALE VISÉE : environ {target_duration_s:g} secondes en format vertical 9:16 "
        f"(soit typiquement {max(1, round(target_duration_s / 5))} plan(s) de ~5 secondes). "
        f"Garde chaque description concise pour que chaque prompt LTX reste sous {MAX_LTX_PROMPT_WORDS} mots."
    )

    request_body: dict = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": 3000,
    }
    if settings.deepseek_base_url.rstrip("/").endswith("api.deepseek.com"):
        request_body["thinking"] = {"type": "disabled"}

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
        raw_content = api_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ScenarioGenerationError(
            "Réponse DeepSeek inattendue : champ `choices[0].message.content` introuvable.",
            raw_content=json.dumps(api_response, ensure_ascii=False)[:1000],
        ) from exc

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
            characters=draft.characters,
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

    prompt_warnings: list[str] = []
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
    }
    return project, meta
