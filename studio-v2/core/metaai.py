"""Client Meta AI de Studio IA V2 : aucune image acceptée sans provenance prouvée.

Le service Meta AI local renvoie, pour chaque génération, l'identifiant de requête
et la conversation réellement utilisée. Ce module refuse tout ce qui ne peut pas
être rattaché à la demande courante, avant toute écriture dans la bibliothèque :

- requête ou prompt non concordants ;
- conversation neuve non prouvée, ou déjà consommée (table `metaai_generations`) ;
- URL de média hors des CDN Meta autorisés ;
- fichier illisible, trop petit (avatar/vignette) ou déjà présent à l'identique.

Aucun secret : les cookies Meta restent exclusivement dans l'environnement du
service `studio-v2/metaai-service`.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from core.config import V2Settings

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MIN_IMAGE_SIDE = 256
DEFAULT_IMAGE_HOSTS = ("fbcdn.net", "metaaiusercontent.com")

_STATUS_MESSAGES = {
    401: "Meta AI demande une reconnexion. Vérifiez la session (cookies) et la clé du service local.",
    403: "Le service Meta AI refuse l'accès. Vérifiez la clé API configurée dans Studio IA.",
    409: "Le service Meta AI a déjà exécuté cette requête. Relancez la génération du visuel concerné.",
    422: (
        "Meta AI refuse ce visuel. Pour un plan : Assets > Adapter le visuel, puis décrivez "
        "un instant calme avant le geste et relancez ce plan."
    ),
    429: "Meta AI est occupé par une autre génération. Attendez la fin puis reprenez les visuels manquants.",
    503: "Le service Meta AI n'est pas disponible (non démarré, cookies absents ou session à renouveler).",
    504: "Meta AI n'a pas renvoyé d'image à temps. Le lot est arrêté ; les images déjà enregistrées sont conservées.",
}


class MetaAIError(RuntimeError):
    """Erreur Meta AI compréhensible par l'utilisateur, sans retry automatique."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class MetaAIConfigurationError(MetaAIError):
    pass


class MetaAIUnavailableError(MetaAIError):
    pass


class MetaAIProvenanceError(MetaAIError):
    pass


@dataclass
class GeneratedImage:
    """Image validée, prête à être enregistrée par la bibliothèque."""

    raw: bytes
    width: int
    height: int
    source_url: str
    sha256: str
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def suffix(self) -> str:
        try:
            with Image.open(io.BytesIO(self.raw)) as img:
                fmt = (img.format or "png").lower()
        except (OSError, UnidentifiedImageError):  # pragma: no cover - déjà validé
            fmt = "png"
        return {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif", "bmp": ".bmp"}.get(fmt, ".png")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _configured_hosts(raw: str | None) -> tuple[str, ...]:
    values = tuple(
        part.strip().lower().lstrip(".")
        for part in str(raw or "").replace(";", ",").split(",")
        if part.strip()
    )
    return values or DEFAULT_IMAGE_HOSTS


def host_allowed(url: str, allowed: tuple[str, ...]) -> bool:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed)


class MetaAIImageClient:
    """Appelle le service local et vérifie chaque preuve avant d'accepter l'image."""

    def __init__(
        self,
        settings: V2Settings,
        *,
        http_client_factory: Callable[..., Any] = httpx.Client,
        request_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        base_url = str(settings.metaai_api_url or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise MetaAIConfigurationError(
                "L'URL du service Meta AI doit commencer par http:// ou https://.",
                status_code=400,
            )
        self.base_url = base_url
        self.api_key = settings.metaai_api_key
        self.timeout_s = settings.metaai_timeout_s
        self.allowed_hosts = _configured_hosts(getattr(settings, "metaai_image_hosts", ""))
        self.min_image_side = int(getattr(settings, "metaai_min_image_side", MIN_IMAGE_SIDE))
        self._client_factory = http_client_factory
        self._request_id_factory = request_id_factory

    # ── API publique ────────────────────────────────────────────────────────
    def generate(self, prompt: str) -> GeneratedImage:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise MetaAIConfigurationError("Prompt image vide : génération refusée.", status_code=400)
        request_id = str(self._request_id_factory())
        payload = self._request_image(prompt, request_id)
        provenance = self._validate_provenance(payload, prompt=prompt, request_id=request_id)
        return self._download_and_validate(payload, provenance)

    # ── Étapes ──────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_image(self, prompt: str, request_id: str) -> dict[str, Any]:
        try:
            with self._client_factory(timeout=self.timeout_s, follow_redirects=True) as client:
                response = client.post(
                    f"{self.base_url}/image",
                    json={
                        "prompt": prompt,
                        "request_id": request_id,
                        # Le service impose une conversation neuve ; le champ est explicite
                        # pour ne jamais dépendre d'un défaut implicite.
                        "new_conversation": True,
                    },
                    headers=self._headers(),
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    # 401/403/429/503/504 sont actionnables tels quels ; tout le reste
                    # devient une erreur de provenance (502) sans détail brut.
                    mapped = status if status in {401, 403, 429, 503, 504} else (422 if status == 422 else 502)
                    raise MetaAIError(
                        _STATUS_MESSAGES.get(
                            status, "Échec du service Meta AI. Réessayez le visuel concerné."
                        ),
                        status_code=mapped,
                    ) from None
                try:
                    payload = response.json()
                except ValueError:
                    raise MetaAIProvenanceError(
                        "Le service Meta AI a renvoyé une réponse illisible.",
                        status_code=502,
                    ) from None
        except httpx.RequestError:
            raise MetaAIUnavailableError(
                "Le service Meta AI local ne répond pas. Démarrez-le puis reprenez les visuels manquants.",
                status_code=503,
            ) from None
        if not isinstance(payload, dict):
            raise MetaAIProvenanceError("Réponse Meta AI inattendue : objet JSON requis.", status_code=502)
        return payload

    def _validate_provenance(self, payload: dict[str, Any], *, prompt: str, request_id: str) -> dict[str, Any]:
        if not payload.get("success", False):
            raise MetaAIProvenanceError(
                "Le service Meta AI n'a pas confirmé la génération (success absent).", status_code=502
            )
        returned_request = str(payload.get("request_id") or "")
        if returned_request != request_id:
            raise MetaAIProvenanceError(
                "Provenance invalide : la réponse ne correspond pas à la requête envoyée.", status_code=502
            )
        returned_prompt = normalize_space(payload.get("prompt") or "")
        if returned_prompt != normalize_space(prompt):
            raise MetaAIProvenanceError(
                "Provenance invalide : l'image renvoyée ne correspond pas au prompt demandé.", status_code=502
            )
        if str(payload.get("attribution") or "") != "fresh_assistant_response":
            raise MetaAIProvenanceError(
                "Provenance invalide : la réponse ne provient pas d'une conversation neuve vérifiée.",
                status_code=502,
            )
        if payload.get("prompt_echo_verified") is not True:
            raise MetaAIProvenanceError(
                "Provenance invalide : le message réellement envoyé à Meta AI n'a pas été confirmé.",
                status_code=502,
            )
        conversation_id = str(payload.get("conversation_id") or "").strip()
        if not conversation_id:
            raise MetaAIProvenanceError(
                "Provenance invalide : conversation Meta AI absente.", status_code=502
            )
        urls = payload.get("image_urls") or payload.get("images") or []
        if isinstance(urls, str):
            urls = [urls]
        urls = [str(url) for url in urls if url]
        if not urls:
            raise MetaAIProvenanceError("Meta AI n'a renvoyé aucune image exploitable.", status_code=502)
        for url in urls:
            if not host_allowed(url, self.allowed_hosts):
                raise MetaAIProvenanceError(
                    "Provenance invalide : le média ne vient pas d'un CDN Meta autorisé "
                    f"({urlparse(url).hostname or 'hôte inconnu'}).",
                    status_code=502,
                )
        return {
            "attribution": "fresh_assistant_response",
            "conversation_id": conversation_id,
            "previous_conversation_id": str(payload.get("previous_conversation_id") or ""),
            "request_id": request_id,
            "prompt": prompt,
            "source_url": urls[0],
            "service_version": str(payload.get("service_version") or ""),
            "generated_at": str(payload.get("generated_at") or ""),
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "prompt_echo_verified": True,
        }

    def _download_and_validate(self, payload: dict[str, Any], provenance: dict[str, Any]) -> GeneratedImage:
        urls = payload.get("image_urls") or payload.get("images") or []
        if isinstance(urls, str):
            urls = [urls]
        url = str(urls[0])
        try:
            with self._client_factory(timeout=self.timeout_s, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                raw = response.content
        except httpx.HTTPStatusError:
            raise MetaAIUnavailableError(
                "Le fichier image Meta AI n'a pas pu être téléchargé. Relancez ce visuel.", status_code=503
            ) from None
        except httpx.RequestError:
            raise MetaAIUnavailableError(
                "Téléchargement de l'image Meta AI interrompu. Relancez ce visuel.", status_code=503
            ) from None

        if not raw:
            raise MetaAIProvenanceError("Meta AI a renvoyé une image vide.", status_code=502)
        if len(raw) > MAX_IMAGE_BYTES:
            raise MetaAIProvenanceError("Image Meta AI supérieure à la limite de 25 Mo.", status_code=502)
        try:
            with Image.open(io.BytesIO(raw)) as img:
                img.verify()
            with Image.open(io.BytesIO(raw)) as img:
                width, height = img.size
                fmt = (img.format or "").lower()
        except (OSError, UnidentifiedImageError, ValueError):
            raise MetaAIProvenanceError(
                "Meta AI a renvoyé un fichier qui n'est pas une image lisible.", status_code=502
            ) from None
        if fmt not in {"jpeg", "png", "webp", "bmp"}:
            raise MetaAIProvenanceError(f"Format d'image Meta AI non pris en charge : {fmt or 'inconnu'}.", status_code=502)
        if min(width, height) < self.min_image_side:
            raise MetaAIProvenanceError(
                f"Image Meta AI trop petite ({width}×{height}) : avatar ou vignette refusé, "
                "aucune image enregistrée.",
                status_code=502,
            )
        provenance = {**provenance, "width": width, "height": height, "bytes": len(raw)}
        return GeneratedImage(
            raw=raw,
            width=width,
            height=height,
            source_url=url,
            sha256=hashlib.sha256(raw).hexdigest(),
            provenance=provenance,
        )

    def write_image(self, image: GeneratedImage, directory: Path) -> Path:
        """Écrit l'image validée sous un nom neuf ; jamais d'écrasement."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{uuid.uuid4().hex[:12]}{image.suffix}"
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(image.raw)
        temporary.replace(target)
        return target
