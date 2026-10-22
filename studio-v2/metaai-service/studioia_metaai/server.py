"""Serveur HTTP local du service Meta AI (compatible avec Studio IA V2).

Contrat public :

    GET  /healthz  → état du service, sans secret
    POST /image    → {"prompt": str, "request_id": str|None}
                     → image_urls + preuves de provenance (conversation neuve)
    POST /reset    → ferme le navigateur (les cookies restent hors de Git)

Le service est volontairement minimal : une génération à la fois, aucun cache,
aucun retry automatique silencieux, aucune écriture dans le paquet metaai-sdk.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .dom import ProvenanceError, RefusalError
from .service import (
    SERVICE_VERSION,
    DuplicateRequestError,
    MetaAIImageService,
    ServiceBusyError,
    ServiceConfigurationError,
)

logger = logging.getLogger("studioia_metaai")
SERVICE_ROOT = Path(__file__).resolve().parents[1]


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    request_id: Optional[str] = Field(default=None, max_length=120)
    # Conservé pour compatibilité avec le contrat metaai-api ; toujours vrai ici.
    new_conversation: bool = True
    orientation: Optional[str] = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


def _cookies_from_env() -> dict[str, str]:
    cookies = {
        "datr": os.getenv("META_AI_DATR", "").strip(),
        "ecto_1_sess": os.getenv("META_AI_ECTO_1_SESS", "").strip(),
    }
    abra = os.getenv("META_AI_ABRA_SESS", "").strip()
    if abra:
        cookies["abra_sess"] = abra
    return cookies


def create_app(service: MetaAIImageService | None = None) -> FastAPI:
    state: dict[str, MetaAIImageService | None] = {"service": service}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if state["service"] is not None:
                state["service"].close()

    app = FastAPI(title="Studio IA — service Meta AI", version=SERVICE_VERSION, lifespan=lifespan)

    def get_service() -> MetaAIImageService:
        if state["service"] is None:
            state_path = Path(os.getenv("META_AI_STATE", str(SERVICE_ROOT / "data" / "state.json")))
            state["service"] = MetaAIImageService(
                cookies=_cookies_from_env(),
                state_path=state_path,
                headed=_env_bool("META_AI_HEADED", False),
                generation_timeout_s=float(os.getenv("META_AI_TIMEOUT_S", "150")),
                logger=logger,
            )
        return state["service"]  # type: ignore[return-value]

    app.state.service = get_service

    def verify_api_key(authorization: Optional[str] = Header(None)) -> None:
        expected = os.getenv("META_AI_API_KEY", "").strip()
        if not expected:
            return
        token = (authorization or "")[7:] if (authorization or "").startswith("Bearer ") else (authorization or "")
        if token != expected:
            raise HTTPException(status_code=401, detail="Clé API du service Meta AI invalide.")

    @app.get("/healthz")
    def healthz() -> dict:
        return get_service().health()

    @app.get("/")
    def root() -> dict:
        return {
            "service": "studioia-metaai",
            "version": SERVICE_VERSION,
            "endpoints": ["/healthz", "/image", "/reset", "/docs"],
        }

    @app.post("/image", dependencies=[Depends(verify_api_key)])
    def generate_image(req: ImageRequest) -> dict:
        service = get_service()
        try:
            return service.generate(req.prompt, req.request_id)
        except DuplicateRequestError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ServiceConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ServiceBusyError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None
        except RefusalError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ProvenanceError as exc:
            message = str(exc)
            status = 504 if "délai imparti" in message else 502
            raise HTTPException(status_code=status, detail=message) from None
        except Exception as exc:  # pragma: no cover - navigateur réel
            logger.exception("Échec inattendu de la génération Meta AI")
            raise HTTPException(
                status_code=500,
                detail=f"Échec interne du service Meta AI ({type(exc).__name__}).",
            ) from None

    @app.post("/reset", dependencies=[Depends(verify_api_key)])
    def reset() -> dict:
        service = get_service()
        service.reset()
        return {"status": "reset", "version": SERVICE_VERSION}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("META_AI_HOST", "127.0.0.1")
    port = int(os.getenv("META_AI_PORT", "8000"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.getenv("META_AI_DATR") or not os.getenv("META_AI_ECTO_1_SESS"):
        print("ERREUR : définissez META_AI_DATR et META_AI_ECTO_1_SESS dans l'environnement.", file=sys.stderr)
        print("Voir studio-v2/metaai-service/README.md (section Cookies).", file=sys.stderr)
        raise SystemExit(2)
    if not os.getenv("META_AI_API_KEY"):
        print("AVERTISSEMENT : META_AI_API_KEY non définie ; le service écoute en local sans clé.", file=sys.stderr)
    print(json.dumps({"service": "studioia-metaai", "version": SERVICE_VERSION, "host": host, "port": port}))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
