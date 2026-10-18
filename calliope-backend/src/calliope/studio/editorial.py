"""Contrôles éditoriaux : naturalité du montage et durée du CTA."""

from __future__ import annotations

import math


def validate_plan(plan: dict) -> dict:
    """Vérifie qu'un plan ne découpe pas chaque réplique.

    Le seuil est volontairement simple : au plus une coupe par 8 secondes
    de dialogue, hors ouvertures/fermetures. Un plan de 42 s avec 5 plans
    (4 coupes) passe ; un plan de 42 s avec 15 plans échoue.
    """
    shots = [
        shot for scene in plan.get("scenes", [])
        for shot in scene.get("shots", [])
    ]
    total = sum(float(shot.get("duration_sec") or 0) for shot in shots)
    cuts = max(0, len(shots) - 1)
    errors: list[str] = []
    warnings: list[str] = []
    if total <= 0:
        errors.append("Durée totale nulle.")
    if cuts > max(1, math.ceil(total / 8)):
        errors.append(
            f"Trop de coupes : {cuts} pour {total:.1f} s "
            f"(maximum {max(1, math.ceil(total / 8))})."
        )
    for index, shot in enumerate(shots):
        duration = float(shot.get("duration_sec") or 0)
        if duration < 2.5 and shot.get("purpose") not in ("midroll", "outro"):
            errors.append(f"Plan {index + 1} trop court ({duration:.1f} s).")
        if len(shot.get("dialogue_turns") or []) > 1 and not (
            plan.get("post_dub") or shot.get("post_dub")
        ):
            warnings.append(
                f"Plan {index + 1} multi-locuteur sans post-doublage : "
                "risque d'inversion de voix."
            )
    cta = next(
        (shot for shot in shots if shot.get("purpose") in ("midroll", "outro")),
        shots[-1] if shots else None,
    )
    if cta and float(cta.get("duration_sec") or 0) > 4.0:
        errors.append(
            f"CTA trop long ({float(cta['duration_sec']):.1f} s, maximum 4,0 s)."
        )
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "shots": len(shots),
        "cuts": cuts,
        "duration_sec": round(total, 3),
        "cuts_per_10s": round(cuts / (total / 10), 3) if total else 0.0,
    }
