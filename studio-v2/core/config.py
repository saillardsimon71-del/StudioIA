"""Configuration isolée de Studio IA V2 (aucun accès aux fichiers ni ports de V1)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STUDIO_V2_ROOT = Path(__file__).resolve().parents[1]
V1_FORBIDDEN_PORTS = {8247, 8260}

# Préréglages documentés d'après la grille publique Modal (https://modal.com/pricing)
# L'opérateur peut modifier chaque paramètre ou mettre gpu_usd_per_sec=None si inconnu.
MODAL_GPU_PRESETS: dict[str, dict[str, float | str]] = {
    "L40S_PROD": {
        "label": "Modal L40S + 8 CPU + 64 GiB (déploiement vérifié docs/studio/LTX.md)",
        "gpu_usd_per_sec": 0.000542,       # $1.95 / h
        "cpu_cores": 8.0,
        "cpu_usd_per_core_sec": 0.0000394,
        "memory_gib": 64.0,
        "memory_usd_per_gib_sec": 0.00000672,
    },
    "RTX_PRO_6000": {
        "label": "Modal RTX PRO 6000 + 2 CPU + 32 GiB (deploy/modal_ltx.py)",
        "gpu_usd_per_sec": 0.000850,       # $3.06 / h
        "cpu_cores": 2.0,
        "cpu_usd_per_core_sec": 0.0000394,
        "memory_gib": 32.0,
        "memory_usd_per_gib_sec": 0.00000672,
    },
}


class V2Settings(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8270, ge=1024, le=65535)
    data_dir: Path = Field(default_factory=lambda: STUDIO_V2_ROOT / "data")

    # DeepSeek LLM (un seul appel par clic utilisateur)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_api_key: str = ""
    deepseek_timeout_s: float = 90.0
    deepseek_input_usd_per_1m: float = 0.27
    deepseek_output_usd_per_1m: float = 1.10

    # Moteur vidéo LTX-2.5 sur Modal
    modal_app_name: str = "studio-ia-ltx"
    modal_function_name: str = "render_clip"
    gpu_label: str = "Modal L40S + 8 CPU + 64 GiB"
    gpu_usd_per_sec: float | None = 0.000542
    cpu_cores: float = 8.0
    cpu_usd_per_core_sec: float = 0.0000394
    memory_gib: float = 64.0
    memory_usd_per_gib_sec: float = 0.00000672
    usd_to_eur: float | None = 0.92

    # Paramètres temporels calibrés sur les tirages réels de docs/studio/LTX.md
    # (78,17 s chaud pour 145 frames ; 151,25–197,25 s avec démarrage à froid)
    cold_startup_s: float = 85.0
    warm_sec_per_frame: float = 0.54
    scaledown_window_s: float = 2.0
    worst_scaledown_window_s: float = 60.0

    # Plafond de dépense autorisé par génération unitaire
    max_budget_eur_per_take: float = Field(default=1.00, gt=0, le=100.0)

    # QC local (Whisper CPU & OCR Tesseract)
    whisper_model_or_path: str = "small"
    whisper_language: str = "fr"
    ocr_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "v2.db"

    @property
    def media_dir(self) -> Path:
        return Path(self.data_dir) / "media"

    @property
    def takes_dir(self) -> Path:
        return self.media_dir / "takes"

    @property
    def imports_dir(self) -> Path:
        return self.media_dir / "imports"

    @property
    def cache_dir(self) -> Path:
        return Path(self.data_dir) / "cache"

    @property
    def exports_dir(self) -> Path:
        return Path(self.data_dir) / "exports"

    @property
    def costs_markdown_path(self) -> Path:
        env_override = os.environ.get("STUDIO_V2_COSTS_MD", "").strip()
        if env_override:
            return Path(env_override)
        return STUDIO_V2_ROOT.parent / "COSTS.md"

    def ensure_dirs(self) -> None:
        if self.port in V1_FORBIDDEN_PORTS:
            raise ValueError(
                f"Le port {self.port} est réservé à Studio IA V1 (8247/8260). "
                "Choisissez un port distinct (ex. 8270)."
            )
        for directory in (
            self.data_dir,
            self.media_dir,
            self.takes_dir,
            self.imports_dir,
            self.cache_dir,
            self.exports_dir,
        ):
            Path(directory).mkdir(parents=True, exist_ok=True)

    def effective_deepseek_key(self) -> str:
        return (
            os.environ.get("DEEPSEEK_API_KEY", "").strip()
            or self.deepseek_api_key.strip()
        )

    def container_usd_per_sec(self) -> float | None:
        if self.gpu_usd_per_sec is None:
            return None
        return (
            self.gpu_usd_per_sec
            + self.cpu_cores * self.cpu_usd_per_core_sec
            + self.memory_gib * self.memory_usd_per_gib_sec
        )

    def container_eur_per_sec(self) -> float | None:
        usd_rate = self.container_usd_per_sec()
        if usd_rate is None or self.usd_to_eur is None:
            return None
        return usd_rate * self.usd_to_eur

    def public_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "deepseek_api_key_configured": bool(self.effective_deepseek_key()),
            "modal_app_name": self.modal_app_name,
            "modal_function_name": self.modal_function_name,
            "gpu_label": self.gpu_label,
            "gpu_usd_per_sec": self.gpu_usd_per_sec,
            "cpu_cores": self.cpu_cores,
            "cpu_usd_per_core_sec": self.cpu_usd_per_core_sec,
            "memory_gib": self.memory_gib,
            "memory_usd_per_gib_sec": self.memory_usd_per_gib_sec,
            "usd_to_eur": self.usd_to_eur,
            "container_eur_per_sec": self.container_eur_per_sec(),
            "cold_startup_s": self.cold_startup_s,
            "warm_sec_per_frame": self.warm_sec_per_frame,
            "scaledown_window_s": self.scaledown_window_s,
            "max_budget_eur_per_take": self.max_budget_eur_per_take,
            "whisper_model_or_path": self.whisper_model_or_path,
            "whisper_language": self.whisper_language,
            "ocr_enabled": self.ocr_enabled,
            "presets": MODAL_GPU_PRESETS,
        }


def config_file_path() -> Path:
    override = os.environ.get("STUDIO_V2_CONFIG", "").strip()
    return Path(override) if override else (STUDIO_V2_ROOT / "config.json")


def load_settings() -> V2Settings:
    path = config_file_path()
    payload: dict = {}
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
    env_data = os.environ.get("STUDIO_V2_DATA_DIR", "").strip()
    if env_data:
        payload["data_dir"] = Path(env_data)
    env_port = os.environ.get("STUDIO_V2_PORT", "").strip()
    if env_port:
        payload["port"] = int(env_port)
    env_host = os.environ.get("STUDIO_V2_HOST", "").strip()
    if env_host:
        payload["host"] = env_host
    settings = V2Settings.model_validate(payload)
    settings.ensure_dirs()
    return settings


def save_settings(settings: V2Settings) -> Path:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = settings.model_dump(mode="json")
    path.write_text(
        json.dumps(serializable, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    settings.ensure_dirs()
    return path
