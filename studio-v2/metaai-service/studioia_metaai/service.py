"""Orchestration du service Meta AI : une image = une requête = une conversation."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .browser import VerifiedBrowser
from .dom import ProvenanceError, RefusalError

SERVICE_VERSION = "1.0.0"


class ServiceBusyError(RuntimeError):
    """Une génération est déjà en cours : aucun empilement de requêtes concurrentes."""


class DuplicateRequestError(RuntimeError):
    """Le même request_id a déjà produit une image : jamais deux fois."""


class ServiceConfigurationError(RuntimeError):
    """Cookies ou environnement incomplets."""


@dataclass
class ServiceState:
    """Mémoire persistante : conversations et requêtes déjà consommées."""

    path: Path
    used_conversations: list[str] = field(default_factory=list)
    used_requests: list[str] = field(default_factory=list)
    max_entries: int = 5000

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.used_conversations = [str(v) for v in data.get("used_conversations", []) if v]
            self.used_requests = [str(v) for v in data.get("used_requests", []) if v]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "used_conversations": self.used_conversations[-self.max_entries:],
            "used_requests": self.used_requests[-self.max_entries:],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class MetaAIImageService:
    """Sérialise les générations et n'expose que des résultats prouvés."""

    def __init__(
        self,
        *,
        cookies: dict[str, str],
        state_path: Path,
        headed: bool = False,
        generation_timeout_s: float = 150.0,
        browser_factory: Callable[..., Any] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.cookies = cookies
        self.headed = headed
        self.generation_timeout_s = generation_timeout_s
        self._lock = threading.Lock()
        self._browser: Any | None = None
        self._browser_factory = browser_factory or VerifiedBrowser
        self._state = ServiceState(path=Path(state_path))
        self._state.load()
        self.generated = 0
        self.last_error = ""
        self.logger = logger

    # ── Diagnostics ─────────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": SERVICE_VERSION,
            "browser_ready": bool(self._browser and getattr(self._browser, "_ready", False)),
            "cookies_configured": bool(self.cookies.get("datr") and self.cookies.get("ecto_1_sess")),
            "generated_this_session": self.generated,
            "known_conversations": len(self._state.used_conversations),
            "last_error": self.last_error[:300],
            "engine": "playwright-fresh-conversation/1",
        }

    # ── Génération ──────────────────────────────────────────────────────────
    def generate(self, prompt: str, request_id: str | None = None) -> dict[str, Any]:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ProvenanceError("Prompt vide : aucune requête envoyée à Meta AI.")
        if len(prompt) > 4000:
            raise ProvenanceError("Prompt supérieur à 4000 caractères : requête refusée.")
        if not (self.cookies.get("datr") and self.cookies.get("ecto_1_sess")):
            raise ServiceConfigurationError(
                "Cookies Meta AI absents. Définissez META_AI_DATR et META_AI_ECTO_1_SESS "
                "dans l'environnement du service, puis redémarrez-le."
            )
        rid = (request_id or "").strip() or str(uuid.uuid4())
        if rid in self._state.used_requests:
            raise DuplicateRequestError(f"request_id déjà exécuté : {rid}")

        if not self._lock.acquire(blocking=False):
            raise ServiceBusyError("Une génération Meta AI est déjà en cours.")
        started = time.monotonic()
        try:
            browser = self._ensure_browser()
            evidence = browser.generate_image(
                prompt,
                used_conversation_ids=set(self._state.used_conversations),
                timeout_s=self.generation_timeout_s,
            )
            payload = evidence.as_dict()
            payload.update(
                {
                    "success": True,
                    "request_id": rid,
                    "prompt": prompt,
                    "service_version": SERVICE_VERSION,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            self._remember(rid, evidence.conversation_id)
            self.generated += 1
            self.last_error = ""
            return payload
        except (ProvenanceError, RefusalError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        except Exception as exc:  # pragma: no cover - dépend du navigateur réel
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._lock.release()

    def _remember(self, request_id: str, conversation_id: str) -> None:
        if conversation_id and conversation_id not in self._state.used_conversations:
            self._state.used_conversations.append(conversation_id)
        self._state.used_requests.append(request_id)
        self._state.save()

    def _ensure_browser(self) -> Any:
        if self._browser is None:
            self._browser = self._browser_factory(
                cookies=self.cookies,
                headed=self.headed,
                logger=self.logger,
            )
            self._browser.setup()
        return self._browser

    def reset(self) -> None:
        """Ferme le navigateur ; ne touche ni aux cookies ni à l'état des conversations."""
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None

    def close(self) -> None:
        self.reset()
