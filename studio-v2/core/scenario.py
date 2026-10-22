"""Modèles centraux Pydantic V2 — Source unique de vérité de Studio IA V2.

Aucun modèle parallèle ne duplique ces structures : le scénario, les dialogues,
les états initial/final, les références visuelles et le prompt LTX vivent ici.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

LTX_MAX_DURATION_S = 15.041  # 361 frames à 24 fps


def _coerce_state_text(value: Any) -> str:
    """Accepte une chaîne ou un dictionnaire JSON issu du LLM et normalise en texte lisible."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = [f"{k}: {v}" for k, v in value.items() if str(v).strip()]
        return "; ".join(parts)
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


StateText = Annotated[str, BeforeValidator(_coerce_state_text)]


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    image_ref: str | None = None
    voice_hint: str = ""


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=600)
    start_sec: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    delivery: Literal["on_screen", "voice_over", "off_screen"] = "on_screen"


class Decor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    asset_id: str | None = None


class DisplayChange(BaseModel):
    """Changement horodaté d'une zone d'affichage fixe ajoutée au montage FFmpeg."""

    model_config = ConfigDict(extra="forbid")

    at_sec: float = Field(gt=0.0, allow_inf_nan=False)
    text: str = Field(max_length=80)


class DisplayOverlay(BaseModel):
    """Zone visuelle fixe réservée (ex. compteur sur écran vierge), incrustée au montage.

    LTX ne génère jamais de chiffres ni de sous-titres : la prise est tournée avec
    un écran vierge, et FFmpeg compose les valeurs exactes aux instants déclarés.
    """

    model_config = ConfigDict(extra="forbid")

    initial_text: str = Field(max_length=80)
    x: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    y: float = Field(default=0.14, ge=0.0, le=1.0, allow_inf_nan=False)
    font_size: float = Field(default=0.05, ge=0.015, le=0.18, allow_inf_nan=False)
    color: str = Field(default="00FF88", pattern=r"^[0-9A-Fa-f]{6}$")
    box_bg: str | None = Field(default="101418", pattern=r"^[0-9A-Fa-f]{6}$")
    changes: list[DisplayChange] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _ordered_changes(self) -> DisplayOverlay:
        times = [c.at_sec for c in self.changes]
        if times != sorted(set(times)):
            raise ValueError(
                "Les changements d'affichage doivent être strictement chronologiques."
            )
        return self


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    duration_s: float = Field(gt=0.5, le=LTX_MAX_DURATION_S, allow_inf_nan=False)
    action: str = Field(min_length=1, max_length=2000)
    dialogue: list[DialogueLine] = Field(default_factory=list)
    initial_state: StateText = ""
    final_state: StateText = ""
    image_prompt: str = Field(default="", max_length=4000)
    image_ref: str | None = None
    continue_from_previous: bool = False
    camera_setup: str = Field(default="", max_length=120)
    time_transition: Literal["continuous", "ellipse", "flashback"] = "continuous"
    decor_id: str | None = None
    decor_asset_id: str | None = None
    ltx_prompt: str = ""

    # Métadonnées opérationnelles rattachées au même objet Shot (aucun modèle dupliqué)
    seed: int = Field(default=42, ge=0)
    image_validated: bool = False
    display_overlay: DisplayOverlay | None = None
    rendered_video_ref: str | None = None
    human_validated: bool = False

    @model_validator(mode="after")
    def _validate_shot_bounds(self) -> Shot:
        if not math.isfinite(self.duration_s):
            raise ValueError("La durée du plan doit être un nombre fini.")
        for line in self.dialogue:
            if line.start_sec is not None and line.start_sec >= self.duration_s:
                raise ValueError(
                    f"Plan « {self.id} » : la réplique « {line.text[:30]}… » "
                    f"démarre à {line.start_sec}s pour un plan de {self.duration_s}s."
                )
        if self.display_overlay:
            for change in self.display_overlay.changes:
                if change.at_sec >= self.duration_s:
                    raise ValueError(
                        f"Plan « {self.id} » : changement d'affichage à {change.at_sec}s "
                        f"hors durée du plan ({self.duration_s}s)."
                    )
        return self


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    title: str = ""
    idea: str = Field(min_length=1, max_length=4000)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    target_duration_s: float = Field(default=15.0, gt=1.0, le=120.0, allow_inf_nan=False)
    narration_mode: Literal["dialogue", "voice_over"] = "dialogue"
    visual_style: str = Field(default="", max_length=160)
    characters: list[Character] = Field(default_factory=list)
    decors: list[Decor] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_references(self) -> Project:
        char_ids = [c.id for c in self.characters]
        if len(char_ids) != len(set(char_ids)):
            raise ValueError("Identifiants de personnages dupliqués dans le projet.")
        known_speakers = set(char_ids)

        decor_ids = [decor.id for decor in self.decors]
        if len(decor_ids) != len(set(decor_ids)):
            raise ValueError("Identifiants de decors dupliques dans le projet.")

        shot_ids = [s.id for s in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("Identifiants de plans dupliqués dans le projet.")

        for index, shot in enumerate(self.shots):
            if shot.decor_id is not None and shot.decor_id not in decor_ids:
                raise ValueError(f"Plan {shot.id} : decor inconnu {shot.decor_id}.")
            if index == 0 and shot.continue_from_previous:
                raise ValueError(
                    f"Le premier plan (« {shot.id} ») ne peut pas continuer depuis un plan précédent."
                )
            for line in shot.dialogue:
                if line.speaker_id not in known_speakers:
                    raise ValueError(
                        f"Plan « {shot.id} » : locuteur inconnu « {line.speaker_id} » "
                        f"(personnages déclarés : {sorted(known_speakers) or ['aucun']})."
                    )
        return self
