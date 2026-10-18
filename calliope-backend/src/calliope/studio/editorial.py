"""Contrôles éditoriaux : naturalité du montage et durée du CTA."""

from __future__ import annotations

import math


def validate_plan(plan: dict) -> dict:
    """Vérifie qu'un plan ne découpe pas chaque réplique.

    Le seuil est volontairement simple : au plus une coupe par 8 secondes
    de dialogue, avec au moins deux coupes tolérées sur un format court, hors
    ouvertures/fermetures. Un plan de 42 s avec 5 plans (4 coupes) passe ;
    un plan de 42 s avec 15 plans échoue.

    Le contrôle couvre aussi les répliques réellement ciblées par un
    post-doublage et les états déclarés entre deux plans. Il ne juge ni le jeu,
    ni les voix, ni la qualité du scénario.
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
    if cuts > max(2, math.ceil(total / 8)):
        errors.append(
            f"Trop de coupes : {cuts} pour {total:.1f} s "
            f"(maximum {max(2, math.ceil(total / 8))})."
        )
    for index, shot in enumerate(shots):
        duration = float(shot.get("duration_sec") or 0)
        turns = shot.get("dialogue_turns") or []
        spoken = bool(turns or str(shot.get("dialogue") or "").strip())
        if duration < 1.0 and shot.get("purpose") not in ("midroll", "outro"):
            errors.append(f"Plan {index + 1} trop court ({duration:.1f} s).")
        elif duration < 2.5 and spoken and shot.get("purpose") not in ("midroll", "outro"):
            warnings.append(
                f"Plan {index + 1} court pour sa réplique ({duration:.1f} s) : "
                "vérifiez le débit et le jeu à l'écoute."
            )
        targets = shot.get("post_dub_turns")
        if targets is not None:
            if not isinstance(targets, list):
                errors.append(f"Plan {index + 1}: post_dub_turns doit être une liste.")
            else:
                seen = set()
                for value in targets:
                    if isinstance(value, bool) or not isinstance(value, int):
                        errors.append(
                            f"Plan {index + 1}: réplique à doubler non entière ({value!r})."
                        )
                    elif value < 0 or value >= len(turns):
                        errors.append(
                            f"Plan {index + 1}: réplique {value + 1} hors plan "
                            f"({len(turns)} répliques)."
                        )
                    elif value in seen:
                        errors.append(f"Plan {index + 1}: réplique {value + 1} dupliquée.")
                    else:
                        seen.add(value)
        elif (plan.get("post_dub") or shot.get("post_dub")) and turns:
            warnings.append(
                f"Plan {index + 1} : post-doublage global sans répliques ciblées ; "
                "toutes les voix seraient remplacées. Sélectionnez post_dub_turns."
            )
        if len(turns) > 1 and not (
            plan.get("post_dub") or shot.get("post_dub") or targets
        ):
            warnings.append(
                f"Plan {index + 1} multi-locuteur sans post-doublage : "
                "risque d'inversion de voix."
            )
    for index in range(len(shots) - 1):
        left = shots[index].get("end_state") or {}
        right = shots[index + 1].get("start_state") or {}
        changed = {
            key for key in set(left) & set(right) if left[key] != right[key]
        }
        declared = {
            change.get("key")
            for change in shots[index + 1].get("state_changes") or []
            if isinstance(change, dict)
        }
        undeclared = sorted(changed - declared)
        if undeclared:
            warnings.append(
                f"Raccord plan {index + 1}→{index + 2} : état non raccordé pour "
                f"{', '.join(undeclared)}."
            )
    outro = next(
        (shot for shot in shots if shot.get("purpose") == "outro"),
        None,
    )
    if outro and float(outro.get("duration_sec") or 0) > 4.0:
        errors.append(
            f"CTA trop long ({float(outro['duration_sec']):.1f} s, maximum 4,0 s)."
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
