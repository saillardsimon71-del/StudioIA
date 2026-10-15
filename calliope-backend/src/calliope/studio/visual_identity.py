import hashlib
import json
import re
from pathlib import Path


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contract_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def visual_description(entity):
    return str(
        (entity.get("identity") or {}).get("visual_description") or entity.get("description") or ""
    ).strip()


def scene_direction(shot, context):
    info = (context.get("shot") or {}) | shot
    bible = context.get("bible") or {}
    parts = []
    for field, label in (("world_rules", "World rules"), ("locations", "Fixed geography"),
                         ("continuity", "Continuity rules")):
        if bible.get(field):
            parts.append(f"{label}: {bible[field]}")
    for field, label in (("blocking", "Character positions and eyelines"),
                         ("framing", "Shot composition"), ("camera", "Camera")):
        value = info.get(field) or context.get(field)
        if value:
            parts.append(f"{label}: {value}")
    keys = info.get("character_keys") or []
    if keys:
        parts.append(
            "Visible cast: " + ", ".join(keys)
            + ". Do not add extra characters, duplicate a character or change their identity."
        )
    if len(keys) > 1 and info.get("purpose") != "midroll":
        parts.append(
            "Keep the listed conversation partners in the shared composition. "
            "They look at their interlocutors, not into the camera. "
            "A tighter framing must not relocate anyone or reverse the conversation axis."
        )
    if info.get("purpose") == "midroll":
        parts.append(
            "During this fourth-wall aside, only the speaking character turns their gaze "
            "toward the viewer. This temporary gaze overrides the narrative eyelines; "
            "the established positions, furniture and setting remain unchanged."
        )
    if info.get("blocking") or info.get("framing") or info.get("camera"):
        parts.append(
            "The explicit shot composition and positions take precedence over generic "
            "style preferences for close-ups or camera movement."
        )
    return parts


def ordered_entities(entities):
    by_key = {entity["key"]: entity for entity in entities}
    result, visiting, visited = [], set(), set()

    def visit(entity):
        key = entity["key"]
        if key in visited:
            return
        if key in visiting:
            raise ValueError("Le canon visuel contient une dépendance de variantes circulaire.")
        visiting.add(key)
        parent = (entity.get("identity") or {}).get("variant_of")
        if parent:
            if parent not in by_key or by_key[parent]["kind"] != entity["kind"]:
                raise ValueError(f"La référence de base de {key} est introuvable ou incompatible.")
            visit(by_key[parent])
        visiting.remove(key)
        visited.add(key)
        result.append(entity)

    for entity in entities:
        visit(entity)
    return result


def strip_generated_text(prompt, labels):
    text = str(prompt or "")
    if not labels:
        return text
    clauses = re.split(r"(?<=[.!?;])\s+|\n+", text)
    typography = re.compile(
        r"\b(?:badge|label|number|numeral|digit|text|caption|typography|inscription|sign|display|counter|"
        r"étiquette|chiffre|nombre|texte|inscription|affichage)\b",
        re.I,
    )
    visual_instruction = re.compile(
        r"\b(?:reads?|shows?|displays?|write|written|print|printed|render|visible|glowing|floating|"
        r"above|overhead|affiche|écrit|écrire|inscrit|visible|flottant)\b",
        re.I,
    )
    attached_number = re.compile(
        r"\d.*\b(?:forehead|overhead|above|head|front|tête)\b|"
        r"\b(?:glowing|floating|floats?|forehead|overhead|above|front|tête)\b.*\d",
        re.I,
    )
    numbered_badge = re.compile(
        r"\b(?:badge|label|étiquette|caption|inscription|display)\b.*\d", re.I
    )
    preserved = []
    for clause in clauses:
        visual_clause = re.sub(
            r"\b\d+[- ]?(?:year[- ]old|years? old|ans)\b", "", clause, flags=re.I
        )
        if (
            (typography.search(clause) and visual_instruction.search(clause))
            or attached_number.search(visual_clause)
            or numbered_badge.search(visual_clause)
        ):
            reaction = re.search(
                r"\b(?:as|while|and)\s+(.+\b(?:reacts?|gasps?|recoils?|stares?|smiles?|cries)\b.*)",
                clause,
                re.I,
            )
            if reaction:
                preserved.append(reaction[1])
            continue
        preserved.append(clause)
    return " ".join(preserved).strip()


def reference_bindings(entities, keys, strict=False):
    by_key = {entity["key"]: entity for entity in entities}
    result = []

    def require_base_approval(entity, seen=None):
        seen = seen or set()
        key = entity["key"]
        if key in seen:
            raise ValueError("Le canon visuel contient une dépendance de variantes circulaire.")
        seen.add(key)
        identity = entity.get("identity") or {}
        parent = identity.get("variant_of")
        if parent:
            base = by_key.get(parent)
            if not base:
                raise ValueError(f"La référence de base de {key} est introuvable.")
            base_identity = base.get("identity") or {}
            path = base.get("reference_path")
            if (
                not path
                or not Path(path).is_file()
                or not base_identity.get("reference_approved")
                or base_identity.get("reference_sha256") != file_hash(path)
            ):
                raise ValueError(f"Référence de base à approuver pour {key} avant génération.")
            require_base_approval(base, seen)

    for key in dict.fromkeys(keys):
        entity = by_key.get(key)
        if not entity:
            raise ValueError(f"Entité visuelle inconnue: {key}.")
        identity = entity.get("identity") or {}
        if strict:
            require_base_approval(entity)
        if strict and not str(identity.get("visual_description") or "").strip():
            raise ValueError(f"Identité visuelle explicite requise pour {key} avant génération.")
        path = entity.get("reference_path")
        if not path or not Path(path).is_file():
            if strict:
                raise ValueError(f"Référence à préparer et approuver pour {key} avant génération.")
            continue
        digest = file_hash(path)
        if strict and (
            not identity.get("reference_approved") or identity.get("reference_sha256") != digest
        ):
            raise ValueError(f"Référence à approuver pour {key} avant génération.")
        result.append(
            {
                "index": len(result) + 1,
                "entity_key": key,
                "kind": entity["kind"],
                "path": str(path),
                "sha256": digest,
            }
        )
    return result


def validate_value_manifest(shot, context, expected_values=None):
    from calliope.studio.screen_text import ScreenText

    info = context.get("shot") or {}
    keys = info.get("character_keys", shot.get("character_keys", []))
    labels = info.get("screen_text", shot.get("screen_text", [])) or []
    required = {}
    for entity in context.get("entities", []):
        identity = entity.get("identity") or {}
        value = identity.get("attached_value")
        if entity.get("kind") == "character" and value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Valeur entière requise pour {entity['key']}.")
            required[entity["key"]] = (identity.get("label_key") or entity["key"], value)
    if context.get("directing_profile", {}).get("attached_values") and not required:
        raise ValueError("Déclarez les valeurs entières structurées des personnages dans la série.")
    normalized = [ScreenText.model_validate(label).model_dump() for label in labels]
    if len({label["key"] for label in normalized}) != len(normalized):
        raise ValueError("Deux textes du même plan utilisent la même clé.")
    for label in normalized:
        if label["entity_key"] and label["entity_key"] not in keys:
            raise ValueError("Un texte référence un personnage absent du plan.")
        if any(
            change["at_sec"] >= shot.get("duration_sec", float("inf"))
            for change in label["changes"]
        ):
            raise ValueError("Un changement de valeur dépasse la durée utile prévue du plan.")
    for key in keys:
        if key not in required:
            continue
        label_key, initial = required[key]
        matches = [
            label
            for label in normalized
            if label["entity_key"] == key
            and label["key"] == label_key
            and label["anchor"] == "character"
            and label["value"] is not None
        ]
        if len(matches) != 1:
            raise ValueError(f"Ajoutez un ancrage de valeur explicite unique pour {key}.")
        label = matches[0]
        if expected_values is not None:
            expected = expected_values.get(label_key, initial)
            if label["value"] != expected and label["event"] not in ("change", "reveal"):
                raise ValueError(
                    f"La valeur de {key} doit rester {expected} sans événement structuré."
                )
            expected_values[label_key] = (
                label["changes"][-1]["value"] if label["changes"] else label["value"]
            )
    return normalized


def compile_image_request(shot, context, *, canonical_entity=None):
    info = context.get("shot") or {}
    native = context.get("prompt_contract_version") == 2
    if native:
        info = info | shot
    labels = (
        (info.get("screen_text") or shot.get("screen_text") or [])
        if canonical_entity
        else validate_value_manifest(shot, context)
    )
    deterministic = context.get("directing_profile", {}).get("attached_values") or any(
        (entity.get("identity") or {}).get("attached_value") is not None
        for entity in context.get("entities", [])
    )
    if deterministic and not labels:
        labels = [{"key": "deterministic-values"}]
    strict = context.get("directing_profile", {}).get("require_reference_approval", False)
    all_entities = context.get("entities") or []
    ordered_entities(all_entities)
    by_key = {entity["key"]: entity for entity in all_entities}
    keys = list(info.get("character_keys") or [])
    if info.get("location_key"):
        keys.append(info["location_key"])
    if canonical_entity:
        keys = [canonical_entity["key"]]
        parent = (canonical_entity.get("identity") or {}).get("variant_of")
        ref_keys = [parent] if parent else []
    else:
        ref_keys = keys
    bindings = reference_bindings(all_entities, ref_keys, strict)
    if canonical_entity and ref_keys and not bindings:
        raise ValueError("Préparez la référence de base avant de générer sa variante.")
    canon = []
    for key in dict.fromkeys(keys):
        entity = by_key.get(key)
        if not entity:
            raise ValueError(f"Entité visuelle inconnue: {key}.")
        identity = entity.get("identity") or {}
        if strict and not str(identity.get("visual_description") or "").strip():
            raise ValueError(f"Identité visuelle explicite requise pour {key} avant génération.")
        item = {
            "key": key,
            "name": entity.get("name", key),
            "kind": entity["kind"],
            "visual_description": visual_description(entity),
            "variant_of": identity.get("variant_of", ""),
        }
        if item["variant_of"]:
            item["base_visual_description"] = visual_description(by_key[item["variant_of"]])
        canon.append(item)
    if native:
        projection = context["render_canon"]
        for item in canon:
            entry = projection["characters" if item["kind"] == "character" else "locations"][item["key"]]
            item["visual_description"] = entry["appearance"] if isinstance(entry, dict) else entry
            item.pop("base_visual_description", None)
    parts = []
    if canonical_entity:
        parts.append(
            f"Canonical visual reference for {canonical_entity['kind']}: {canonical_entity['key']}."
        )
    else:
        parts.append(strip_generated_text(shot.get("image_prompt", ""), labels))
    for entity in canon:
        parts.append(
            f"Immutable visual identity {entity['key']} ({entity['name']}): "
            + strip_generated_text(entity["visual_description"], labels)
            + "."
        )
        if entity.get("base_visual_description"):
            parts.append(
                f"Variant of {entity['variant_of']}; "
                "preserve the base species, facial traits and identity: "
                + strip_generated_text(entity["base_visual_description"], labels)
                + "."
            )
    style = strip_generated_text(
        context["render_canon"]["style"] if native else context.get("bible", {}).get("visual_style", ""), labels
    )
    if style:
        parts.append(f"Visual style: {style}.")
    if native:
        if not canonical_entity:
            for key in info.get("prop_keys", []):
                parts.append(strip_generated_text(context["render_canon"]["props"][key], labels))
    elif canonical_entity:
        if context.get("bible", {}).get("world_rules"):
            parts.append(f"World rules: {context['bible']['world_rules']}")
    else:
        parts.extend(strip_generated_text(part, labels) for part in scene_direction(shot, context))
    for binding in bindings:
        parts.append(
            f"Reference image {binding['index']}: {binding['entity_key']} ({binding['kind']}). "
            + ("Preserve this environment's architecture and recurring landmarks; "
               "use the initial composition's camera angle, depth and set dressing."
               if binding["kind"] == "location"
               else "Use this image only for this entity's visual identity.")
        )
    if canonical_entity:
        parts.append(
            "One single image with one consistent view, clean composition, "
            "no panels, no contact sheet, no collage, no watermark."
        )
        parts.append(
            "The character's natural eyes and mouth are visible. Preserve costume and identity."
            if canonical_entity["kind"] == "character"
            else "An empty establishing view, no characters."
        )
    if labels:
        parts.append(
            "Generate no text, letters, numerals, badges, value displays, subtitles or typography. "
            "Exact labels are added separately in postproduction; leave their regions blank."
        )
    prompt = "\n".join(part for part in parts if part)
    return {
        "require_reference_approval": bool(strict),
        "prompt": prompt,
        "reference_paths": [binding["path"] for binding in bindings],
        "reference_bindings": bindings,
        "input_hashes": {
            "identity_contract": contract_hash({"canon": canon, "style": style}),
            "raw_prompt": contract_hash(shot.get("image_prompt", "")),
            "compiled_prompt": contract_hash(prompt),
        },
        "visual_identity": canon,
    }


def verify_reference_inputs(request):
    bindings = request.get("reference_bindings") or []
    if bindings and [binding["path"] for binding in bindings] != request.get("reference_paths", []):
        raise ValueError("L'ordre des références ne correspond plus au prompt compilé.")
    for binding in bindings:
        if not Path(binding["path"]).is_file() or file_hash(binding["path"]) != binding["sha256"]:
            raise ValueError(
                "Une référence a changé depuis la préparation. Créez une nouvelle demande."
            )
