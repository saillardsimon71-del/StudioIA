"""Lecture et vérification du DOM Meta AI — fonctions pures, sans navigateur.

Ce module concentre la partie testable de la garantie de provenance :

1. une requête d'image part **toujours** d'une page d'accueil vierge, donc d'une
   conversation indépendante ;
2. seules les images contenues dans la **réponse de l'assistant du tour courant**
   sont acceptées ;
3. une image déjà présente avant l'envoi, une URL d'avatar, un média hors du
   tour assistant ou une conversation réutilisée sont refusés.

Aucune de ces règles ne dépend de metaai-sdk : le service ne modifie pas le
paquet installé (contrairement aux correctifs V1–V4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

#: Hôtes autorisés par défaut pour un média généré par Meta AI.
DEFAULT_MEDIA_HOST_SUFFIXES: tuple[str, ...] = ("fbcdn.net", "metaaiusercontent.com")

#: Chemins qui ne doivent jamais être acceptés comme conversation de génération.
_FORBIDDEN_PATHS = re.compile(r"settings|applications|/apps(?:/|$|[?#])", re.IGNORECASE)

#: Formules de refus explicites renvoyées par Meta AI (aucune image produite).
_REFUSAL_PATTERNS = (
    re.compile(r"\b(?:i|we)\s+(?:can\s*not|can't|cannot|am unable to|'m unable to)\b", re.IGNORECASE),
    re.compile(r"\b(?:je|nous)\s+ne\s+(?:peux|pouvons)\s+pas\b", re.IGNORECASE),
    re.compile(r"\b(?:cannot|can't)\s+(?:create|generate)\s+(?:that|this|an?\s+image)", re.IGNORECASE),
)

_CONVERSATION_ID = re.compile(r"/prompt/([0-9a-fA-F-]{8,})")


class ProvenanceError(RuntimeError):
    """La réponse ne peut pas être attribuée à la demande courante."""


class RefusalError(RuntimeError):
    """Meta AI a répondu explicitement qu'il ne produirait pas l'image."""


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    inside_assistant_turn: bool
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class PageState:
    path: str = ""
    composer_ready: bool = False
    composer_text: str = ""
    assistant_count: int = 0
    user_count: int = 0
    last_assistant_text: str = ""
    last_user_text: str = ""
    last_assistant_media: tuple[MediaCandidate, ...] = ()
    dialogs: tuple[str, ...] = ()
    visible_buttons: tuple[str, ...] = ()
    media_snapshot: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blank_conversation(self) -> bool:
        return self.composer_ready and self.assistant_count == 0 and self.user_count == 0


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def conversation_id_from_url(url: str) -> str:
    match = _CONVERSATION_ID.search(str(url or ""))
    return match.group(1) if match else ""


def parse_page_state(raw: dict | None, *, snapshot_key: str = "page_media_snapshot") -> PageState:
    """Convertit le dictionnaire renvoyé par ``STATE_JS`` en objet Python."""
    raw = raw or {}
    media: list[MediaCandidate] = []
    for item in raw.get("last_assistant_media") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        media.append(
            MediaCandidate(
                url=url,
                inside_assistant_turn=bool(item.get("inside_assistant_turn", True)),
                width=int(item.get("w") or 0),
                height=int(item.get("h") or 0),
            )
        )
    return PageState(
        path=str(raw.get("path") or ""),
        composer_ready=bool(raw.get("composer_ready")),
        composer_text=normalize_space(raw.get("composer_text") or ""),
        assistant_count=int(raw.get("assistant_count") or 0),
        user_count=int(raw.get("user_count") or 0),
        last_assistant_text=str(raw.get("last_assistant_text") or ""),
        last_user_text=normalize_space(raw.get("last_user_text") or ""),
        last_assistant_media=tuple(media),
        dialogs=tuple(str(value) for value in raw.get("dialogs") or ()),
        visible_buttons=tuple(str(value) for value in raw.get("buttons") or ()),
        media_snapshot=tuple(str(value) for value in raw.get(snapshot_key) or ()),
    )


def is_forbidden_path(path: str) -> bool:
    """Détecte les pages de réglages/applications qui ne sont pas une conversation."""
    return bool(_FORBIDDEN_PATHS.search(str(path or "")))


def host_allowed(url: str, allowed_suffixes: tuple[str, ...] = DEFAULT_MEDIA_HOST_SUFFIXES) -> bool:
    """Le média doit venir d'un CDN Meta attendu, jamais d'un avatar ou d'un tiers."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes)


def detect_refusal(text: str) -> bool:
    return any(pattern.search(str(text or "")) for pattern in _REFUSAL_PATTERNS)


def select_images(
    state: PageState,
    *,
    before: set[str],
    allowed_suffixes: tuple[str, ...] = DEFAULT_MEDIA_HOST_SUFFIXES,
) -> tuple[list[str], list[str]]:
    """Retourne (URL acceptées, motifs de rejet) pour le tour assistant courant.

    Une URL n'est acceptée que si elle est :
    - rattachée au noeud de réponse de l'assistant (jamais la barre latérale, un
      avatar ou l'historique) ;
    - hébergée sur un CDN Meta autorisé ;
    - absente de l'instantané pris **avant** l'envoi du prompt.
    """
    accepted: list[str] = []
    rejected: list[str] = []
    for candidate in state.last_assistant_media:
        if not candidate.inside_assistant_turn:
            rejected.append(f"média hors du tour assistant ({_safe(candidate.url)})")
            continue
        if not host_allowed(candidate.url, allowed_suffixes):
            rejected.append(f"origine de média non fiable ({_safe(candidate.url)})")
            continue
        if candidate.url in before:
            rejected.append(f"média déjà présent avant l'envoi ({_safe(candidate.url)})")
            continue
        if candidate.url not in accepted:
            accepted.append(candidate.url)
    return accepted, rejected


def verify_blank_home(state: PageState) -> None:
    """Vérifie que la page est une conversation vierge prête à recevoir le prompt."""
    if is_forbidden_path(state.path):
        raise ProvenanceError(
            f"Meta AI a ouvert une page de paramètres au lieu du chat ({state.path[:120]})."
        )
    if not state.composer_ready:
        raise ProvenanceError(
            "Le champ de saisie Meta AI n'est pas disponible : session expirée, "
            "écran de consentement ou interface modifiée."
        )
    if state.assistant_count or state.user_count:
        raise ProvenanceError(
            "La page n'est pas une conversation vierge "
            f"({state.assistant_count} réponse(s) assistant, {state.user_count} message(s) utilisateur)."
        )


def verify_prompt_sent(state: PageState, prompt: str) -> None:
    expected = normalize_space(prompt)
    if not state.last_user_text:
        raise ProvenanceError("Le message envoyé n'apparaît pas dans la conversation.")
    actual = state.last_user_text
    if actual != expected:
        # Une reformulation/troncature côté interface rendrait la provenance invérifiable.
        raise ProvenanceError(
            "Le message affiché dans la conversation ne correspond pas exactement au prompt envoyé."
        )


def verify_conversation(
    conversation_id: str,
    *,
    previous_conversation_id: str | None,
    used_conversation_ids: set[str],
) -> None:
    """Refuse une conversation réutilisée (source des échecs 'identifiant déjà pris')."""
    if not conversation_id:
        raise ProvenanceError(
            "Aucun identifiant de conversation Meta AI n'a pu être relevé : provenance non prouvable."
        )
    if conversation_id == (previous_conversation_id or ""):
        raise ProvenanceError("La génération a repris la conversation précédente au lieu d'en ouvrir une neuve.")
    if conversation_id in used_conversation_ids:
        raise ProvenanceError(f"Conversation Meta AI déjà utilisée : {conversation_id}.")


def _safe(url: str, limit: int = 120) -> str:
    text = str(url or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"
