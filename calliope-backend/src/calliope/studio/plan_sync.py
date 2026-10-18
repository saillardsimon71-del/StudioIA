"""Synchronisation du plan d'épisode avec les contrats Director.

Le Director peut réviser un contrat sans réécrire ``plan_json.scenes``. Ce
module remet les scènes du plan au niveau des prises live : il ne modifie que
les champs qui viennent des contrats, et conserve les métadonnées éditoriales
du plan (state_changes, source_beat_index, etc.).

``plan_json.editorial_script`` reste volontairement la **baseline** de la
revue éditoriale : elle sert à détecter les modifications de récit. La version
live est exposée séparément par ``series.editorial_script``.
"""

from __future__ import annotations

import json


def _updated_shot(conn, plan_shot, clip_id):
    from calliope.studio import service

    live = service.public_shot(conn, clip_id)
    context = service.shot_context(conn, clip_id).get("shot") or {}
    values = {
        "description": live.get("description") or "",
        "prompt": live.get("prompt") or "",
        "image_prompt": live.get("image_prompt") or "",
        "dialogue": live.get("dialogue") or "",
        "duration_sec": live.get("duration_sec") or plan_shot.get("duration_sec"),
        "audio_mode": live.get("audio_mode") or plan_shot.get("audio_mode", "native"),
        "dialogue_turns": live.get("dialogue_turns") or [],
    }
    for key in (
        "action", "reaction", "framing", "camera", "blocking", "soundscape",
        "character_keys", "prop_keys", "start_state", "end_state", "cut_reason",
        "continue_from_previous", "purpose", "screen_text",
    ):
        if key in live:
            values[key] = live[key]
    for key in ("speaker", "listener_keys", "source_beat_index", "reference_key"):
        if key in context:
            values[key] = context[key]
    plan_shot.update({key: value for key, value in values.items() if value is not None})
    return plan_shot


def sync_all(conn, project_id):
    """Réécrit les plans de scène à partir des prises/contrats live.

    Retourne True si le plan a été modifié. Les scènes ou plans dont le
    nombre de prises ne correspond pas sont laissés intacts : mieux vaut un
    plan partiellement ancien qu'un plan corrompu.
    """
    row = conn.execute(
        "SELECT plan_json FROM studio_episodes WHERE project_id=?", (project_id,)
    ).fetchone()
    if not row:
        return False
    plan = json.loads(row[0] or "{}")
    scenes = plan.get("scenes") or []
    if not scenes:
        return False
    db_scenes = conn.execute(
        "SELECT id FROM scenes WHERE project_id=? ORDER BY order_index,id", (project_id,)
    ).fetchall()
    if len(db_scenes) != len(scenes):
        return False
    changed = False
    for scene, db_scene in zip(scenes, db_scenes):
        clips = conn.execute(
            "SELECT id FROM clips WHERE scene_id=? ORDER BY order_index,id",
            (db_scene["id"],),
        ).fetchall()
        plan_shots = scene.get("shots") or []
        if len(clips) != len(plan_shots):
            continue
        for plan_shot, clip in zip(plan_shots, clips):
            before = json.dumps(plan_shot, sort_keys=True, ensure_ascii=False)
            _updated_shot(conn, plan_shot, clip["id"])
            if json.dumps(plan_shot, sort_keys=True, ensure_ascii=False) != before:
                changed = True

    if changed:
        conn.execute(
            "UPDATE studio_episodes SET plan_json=? WHERE project_id=?",
            (json.dumps(plan, ensure_ascii=False), project_id),
        )
    return changed


def sync_shot(conn, project_id, shot_id):
    """Synchronise tout le plan après une édition de plan (échelle petite)."""
    return sync_all(conn, project_id)
