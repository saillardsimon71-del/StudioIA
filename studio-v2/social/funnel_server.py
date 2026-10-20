import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, field_validator

from social.common import ROOT, read_config
from social.funnel import FunnelStore, KitClient, ProviderError, subscribe

WEB = ROOT / "web" / "funnel"


class Subscription(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    consent: StrictBool
    website: str = Field(default="", max_length=500)

    @field_validator("email")
    @classmethod
    def email_is_valid(cls, value):
        if not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", value):
            raise ValueError("Adresse email invalide.")
        return value

    @field_validator("consent")
    @classmethod
    def consent_is_explicit(cls, value):
        if value is not True:
            raise ValueError("Le consentement est obligatoire.")
        return value


def create_funnel_app(*, data_dir=None):
    data = Path(data_dir or os.environ.get("STUDIO_V2_DATA_DIR") or ROOT / "data")
    store = FunnelStore(data / "v2.db")
    app = FastAPI(title="Studio IA - Bible créative", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/funnel")
    @app.get("/funnel/")
    def landing():
        return FileResponse(WEB / "index.html", media_type="text/html")

    @app.get("/api/funnel/config")
    def public_config():
        cfg = read_config()
        return {
            "price_eur": cfg["price_eur"],
            "checkout_url": cfg["checkout_url"],
            "privacy_url": cfg["privacy_url"],
            "signup_enabled": KitClient.from_env(store) is not None,
        }

    @app.get("/funnel/bible.pdf")
    def bible():
        path = WEB / "bible-studio-ia.pdf"
        if not path.is_file():
            raise HTTPException(503, "Le guide est en préparation.")
        return FileResponse(path, media_type="application/pdf", filename="bible-studio-ia.pdf")

    @app.post("/api/funnel/subscribe", status_code=202)
    def subscribe_endpoint(body: Subscription):
        if body.website:
            return {"status": "pending_confirmation"}
        kit = KitClient.from_env(store)
        if kit is None:
            raise HTTPException(503, "L'inscription est momentanément indisponible.")
        try:
            return subscribe(body.email, body.consent, kit, store, read_config())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(503, "L'envoi n'a pas été confirmé. Réessaie dans quelques minutes.") from exc

    app.mount("/funnel/assets", StaticFiles(directory=WEB, check_dir=False), name="funnel-assets")
    return app


app = create_funnel_app()
