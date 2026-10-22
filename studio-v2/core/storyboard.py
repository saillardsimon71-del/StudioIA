from __future__ import annotations

import re

from core.scenario import Project, Shot


def visual_style(project: Project) -> str:
    if project.visual_style.strip():
        return project.visual_style.strip()
    if re.search(r"\b(?:réaliste|realistic|photorealistic|photorealism)\b", project.idea, re.I):
        return "dark realistic cinema"
    return "stylized 3D cinema"


def image_state_conflict(project: Project, shot: Shot) -> bool:
    initial, image = shot.initial_state.lower(), shot.image_prompt.lower()
    if not image:
        return False
    # Known physical transitions; never send a contradictory pose together with t=0.
    transitions = (
        (r"\b(?:asleep|sleeping|endormi)\b", r"\bsits? (?:up|upright)\b|\bsitting upright\b|\bredressé\b"),
        (r"\bear\b.*(?:pressed|close)|\boreille\b", r"\b(?:sits?|sitting)\b.*\b(?:floor|floorboards)\b|\bback against\b.*\bbed\b"),
        (r"loose corner|wallpaper hangs|strip of wallpaper", r"ear pressed.*glass"),
        (r"ear close|oreille.*proche", r"(?:ear|cheek).*pressed"),
        (r"hand on the frame|stands in his doorway", r"hand reaching|palm flat.*wall"),
    )
    if any(re.search(start, initial) and re.search(end, image) and not re.search(end, initial)
           for start, end in transitions):
        return True
    image_state = image
    for char in project.characters:
        image_state = image_state.replace(f"{char.name}: {char.description}".lower(), "")
    for result in (r"crack spreading", r"peels?\b|revealing\b|uncovering\b", r"jolts?\b|scrambles?\b"):
        if re.search(result, image_state) and not re.search(result, initial):
            return True
    for char in project.characters:
        name = r"\b(?:" + re.escape(char.name) + "|" + re.escape(char.id) + r")\b"
        arrival = name + r"\s+(?:enters|arrives|shuffles in|steps in|appears|emerges)\b"
        if not re.search(name, initial, re.I) and re.search(arrival, shot.action, re.I):
            appearance = " ".join(re.findall(r"\w+", char.description.lower())[:5])
            if re.search(name, image, re.I) or (appearance and appearance in " ".join(re.findall(r"\w+", image))):
                return True
    return False


def image_style_conflict(project: Project, shot: Shot) -> bool:
    return bool(re.search(r"realist", visual_style(project), re.I) and re.search(
        r"stylized 3d|pixar|cartoon|anime", shot.image_prompt, re.I,
    ))


def build_shot_image_prompt(project: Project, shot: Shot, decor: dict | None = None) -> str:
    if not shot.initial_state.strip():
        raise ValueError(f"Plan {shot.id} : renseignez l'etat initial a t=0.")
    if shot.image_prompt.strip() and not image_state_conflict(project, shot) and not image_style_conflict(project, shot):
        return shot.image_prompt.strip()
    if decor is None and shot.decor_id:
        item = next((d for d in project.decors if d.id == shot.decor_id), None)
        decor = item.model_dump() if item else None
    parts = [
        f"Vertical {project.aspect_ratio} cinematic still image, a single starting frame at t=0.",
        f"Visual style: {visual_style(project)}.",
        "Show only the initial physical state, before any subsequent action. No montage or sequence.",
        f"Initial state, setting and positions: {shot.initial_state.strip()}",
    ]
    if decor:
        parts.append(f"Recurring setting: {decor['name']}. {decor['description']}")
    if shot.camera_setup:
        parts.append(f"Fixed camera composition: {shot.camera_setup}.")
    cast_state = shot.initial_state
    if shot.continue_from_previous and not shot.camera_setup:
        index = next((i for i, s in enumerate(project.shots) if s.id == shot.id), 0)
        if index:
            previous = project.shots[index - 1]
            if previous.final_state == shot.initial_state and previous.decor_id == shot.decor_id:
                cast_state += " " + previous.initial_state
    for char in project.characters:
        if re.search(r"\b(?:" + re.escape(char.name) + "|" + re.escape(char.id) + r")\b", cast_state, re.I):
            parts.append(f"{char.name}: {char.description}")
    framing = None if shot.camera_setup else re.search(
        r"\b(?:extreme close-up|medium close-up|close-up|wide shot|medium shot|full shot|"
        r"over-the-shoulder|gros plan|plan large|plan moyen|plan rapproche|plan rapproché)\b",
        shot.action, re.I,
    )
    if framing:
        parts.append(f"Framing: {framing.group()}")
        names = [re.escape(value) for c in project.characters for value in (c.name, c.id)]
        location = re.split(
            r"[.;]|\b(?:as|while|then|before|after|when|puis|quand|lorsque|avant|après)\b"
            + (r"|\b(?:" + "|".join(names) + r")\b" if names else ""),
            shot.action[framing.end():], maxsplit=1, flags=re.I,
        )[0].strip(" ,:")
        if re.match(r"^(?:in|inside|at|on|dans|sur|en)\b", location, re.I):
            parts.append(f"Setting: {location}")
    if shot.display_overlay:
        parts.append("Keep any display screen blank; text will be added later.")
    parts.append("No text, letters, captions, subtitles or speech bubbles.")
    prompt = "\n".join(parts)
    if len(prompt) > 4000:
        raise ValueError(f"Plan {shot.id} : descriptions trop longues pour le prompt image (4000 caracteres maximum).")
    return prompt


def continuation_issue(previous: Shot, shot: Shot, *, require_composition: bool = True) -> str | None:
    if shot.time_transition != "continuous":
        return "Une ellipse ou un flashback exige une nouvelle image initiale."
    if (previous.decor_id, previous.decor_asset_id) != (shot.decor_id, shot.decor_asset_id):
        return "Le decor change : une nouvelle image initiale est necessaire."
    if require_composition and previous.final_state.strip().casefold() != shot.initial_state.strip().casefold():
        return "L'etat initial ne reprend pas l'etat final precedent."
    if require_composition and (not shot.camera_setup or previous.camera_setup != shot.camera_setup):
        return "Composition de reference non confirmee : renseignez le meme camera_setup ou desactivez continue_from_previous."
    framing = r"\b(wide shot|full shot|medium shot|close-up|close view|extreme close-up)\b"
    before = re.search(framing, previous.image_prompt, re.I)
    after = re.search(framing, shot.image_prompt, re.I)
    if before and after and before.group().lower() != after.group().lower():
        return "Le cadrage change : une nouvelle image initiale est necessaire."
    return None


def storyboard_issues(project: Project) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}
    clock: str | None = None
    for idx, shot in enumerate(project.shots):
        errors = []
        if shot.continue_from_previous and idx:
            previous = project.shots[idx - 1]
            problem = continuation_issue(previous, shot, require_composition=bool(
                previous.camera_setup or shot.camera_setup,
            ))
            if problem:
                errors.append(problem)
        if shot.time_transition != "continuous":
            clock = None
        if shot.display_overlay:
            values = [shot.display_overlay.initial_text] + [c.text for c in shot.display_overlay.changes]
            for value in values:
                if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    if clock is not None and value < clock:
                        errors.append(f"Horloge {clock} -> {value} sans ellipse ou flashback explicite.")
                    clock = value
        available_at = 0.0
        for line in shot.dialogue:
            start = available_at if line.start_sec is None else line.start_sec
            end = start + len(line.text.split()) / 3.2
            if start < available_at - 0.01 or end > shot.duration_s + 0.01:
                errors.append("Temps de parole insuffisant ou repliques en chevauchement (maximum 3.2 mots/s).")
            available_at = end
        if errors:
            issues[shot.id] = errors
    return issues


def prepare_storyboard(project: Project) -> list[str]:
    warnings = []
    for idx, shot in enumerate(project.shots):
        if image_state_conflict(project, shot) or image_style_conflict(project, shot):
            shot.image_prompt = build_shot_image_prompt(project, shot)
            warnings.append(f"{shot.id}: image initiale reconstruite depuis initial_state avant l'action.")
        if idx and shot.continue_from_previous:
            problem = continuation_issue(project.shots[idx - 1], shot)
            if problem:
                shot.continue_from_previous = False
                warnings.append(f"{shot.id}: nouvelle image initiale requise. {problem}")
    for shot_id, problems in storyboard_issues(project).items():
        warnings.extend(f"{shot_id}: {problem}" for problem in problems)
    total = sum(s.duration_s for s in project.shots)
    if abs(total - project.target_duration_s) > max(1, project.target_duration_s * 0.05):
        warnings.append(f"Duree totale {total:g}s pour une cible de {project.target_duration_s:g}s.")
    if project.narration_mode == "voice_over" and not any(
        line.delivery == "voice_over" for s in project.shots for line in s.dialogue
    ):
        warnings.append("Mode voix off demande mais aucune ligne voice_over : narration a revoir.")
    return warnings
