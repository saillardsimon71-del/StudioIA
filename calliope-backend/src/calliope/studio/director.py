import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from calliope.config import settings
from calliope.studio import service
from calliope.studio.director_contracts import ROLE, Budget, ShotContract, validate_speech_timing
from calliope.studio.ltx_realization import action_sequence
from calliope.studio.series_planner import generate_structured
from calliope.studio.visual_identity import file_hash


def scrub(value):
    text = json.dumps(value, ensure_ascii=False)
    keys = [settings.llm_api_key, settings.studio_image_api_key]
    keys += [p.get("api_key") for p in settings.llm_profiles]
    for key in keys:
        if key:
            text = text.replace(key, "[secret masque]")
    return json.loads(text)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def enabled(conn, project_id):
    return (
        conn.execute(
            "SELECT 1 FROM studio_director_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        is not None
    )


def require_fixed_structure(conn, project_id):
    if enabled(conn, project_id):
        raise HTTPException(
            422,
            "Director: revisez les plans existants. Pour changer leur nombre, "
            "recommencez explicitement l'episode; ses prises seront archivees.",
        )


def validate_contract(value, entities, props=()):
    try:
        contract = ShotContract.model_validate(value).model_dump()
    except ValidationError as exc:
        raise ValueError(
            "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in exc.errors(include_input=False)
            )
        ) from None
    characters = {e["key"] for e in entities if e["kind"] == "character"}
    locations = {e["key"] for e in entities if e["kind"] == "location"}
    if not set(contract["casting"]) <= characters or contract["location"] not in locations:
        raise ValueError("Casting ou lieu inconnu du canon.")
    unknown = set(contract["references"]) - (characters | locations | set(props))
    if unknown:
        raise ValueError("Reference inconnue du canon: " + ", ".join(sorted(unknown)))
    return contract


def live_shots(conn, project_id):
    return [
        service.public_shot(conn, row[0])
        for row in conn.execute(
            "SELECT c.id FROM clips c JOIN scenes s ON s.id=c.scene_id WHERE c.project_id=? "
            "ORDER BY s.order_index,s.id,c.order_index,c.id",
            (project_id,),
        ).fetchall()
    ]


def shot_hash(conn, shot, contract):
    context = service.shot_context(conn, shot["id"])
    references = [
        {k: e.get(k) for k in ("key", "kind", "description", "identity")}
        | {
            "file_sha256": file_hash(e["reference_path"])
            if e.get("reference_path") and Path(e["reference_path"]).is_file()
            else None
        }
        for e in context["entities"]
        if e["key"] in contract.get("references", [])
    ]
    live = {
        k: v
        for k, v in shot.items()
        if k
        in (
            "id",
            "scene_id",
            "order_index",
            "description",
            "duration_sec",
            "prompt",
            "image_prompt",
            "dialogue",
            "dialogue_turns",
            "audio_mode",
            "continue_from_previous",
            "soundscape",
            "screen_text",
            "character_keys",
            "prop_keys",
            "start_state",
            "end_state",
            "framing",
            "camera",
            "blocking",
            "action",
            "reaction",
            "purpose",
        )
    }
    live["screen_text"] = [
        {k: v for k, v in label.items() if k != "anchor_binding"}
        for label in live.get("screen_text", [])
    ]
    payload = {
        "contract": contract,
        "live": live,
        "references": references,
        "canon": context.get("render_canon", {}),
        "bible": context.get("bible", {}),
    }
    if settings.studio_video_profile == "ltx":
        from calliope.studio.ltx_prompt import LTX_PROMPT_VERSION

        payload["prompt_compiler_version"] = LTX_PROMPT_VERSION
    return digest(payload)


def _state(conn, project_id):
    service.require_project(conn, project_id)
    row = conn.execute(
        "SELECT * FROM studio_director_projects WHERE project_id=?", (project_id,)
    ).fetchone()
    if not row:
        return {"enabled": False, "shots": []}
    result = []
    for shot in live_shots(conn, project_id):
        stored = conn.execute(
            "SELECT contract_json FROM studio_director_shots WHERE clip_id=?", (shot["id"],)
        ).fetchone()
        contract = json.loads(stored[0]) if stored else {}
        fingerprint = shot_hash(conn, shot, contract)
        image = next((t for t in shot["takes"] if t["id"] == shot["selected_image_id"]), None)
        review = (image or {}).get("metadata", {}).get("director_image_review", {})
        valid_image = bool(image and image["output_path"] and Path(image["output_path"]).is_file())
        result.append(
            {
                "id": shot["id"],
                "description": shot["description"],
                "contract": contract,
                "hash": fingerprint,
                "filename": f"ep-{project_id:04d}-shot-{shot['id']:06d}-{fingerprint[:12]}.png",
                "continue_from_previous": shot["continue_from_previous"],
                "purpose": shot.get("purpose", ""),
                "approved": shot["approved"],
                "image": {
                    "take_id": image["id"],
                    "url": image["url"],
                    "approved": bool(
                        valid_image
                        and review.get("approved")
                        and review.get("shot_hash") == fingerprint
                        and review.get("file_hash") == file_hash(image["output_path"])
                    ),
                }
                if image
                else None,
            }
        )
    storyboard_hash = digest([s["hash"] for s in result])
    return {
        "enabled": True,
        "storyboard_hash": storyboard_hash,
        "approved": bool(result and row["approved_hash"] == storyboard_hash),
        "shots": result,
        "budget": json.loads(row["budget_json"]),
        "vision": "a verifier",
        "speaker_identity": "a verifier",
    }


def state(project_id):
    with service.database() as conn:
        return _state(conn, project_id)


def require_hash(conn, project_id, expected_hash, approved=False):
    current = _state(conn, project_id)
    if not current["enabled"] or expected_hash != current["storyboard_hash"]:
        raise HTTPException(409, "Le storyboard a change. Actualisez avant de poursuivre.")
    if approved and not current["approved"]:
        raise HTTPException(409, "Validez le storyboard avant les images ou la video.")
    return current


def initialize(project_id, contracts):
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if (
            enabled(conn, project_id)
            and conn.execute(
                "SELECT 1 FROM studio_director_shots d JOIN clips c ON c.id=d.clip_id "
                "WHERE c.project_id=?",
                (project_id,),
            ).fetchone()
        ):
            raise HTTPException(409, "Le Director est deja prepare.")
        shots = live_shots(conn, project_id)
        if len(shots) != len(contracts) or not shots:
            raise ValueError("Un contrat complet est requis pour chaque plan.")
        values = []
        for shot, value in zip(shots, contracts):
            context = service.shot_context(conn, shot["id"])
            values.append(validate_contract(
                value, context["entities"], context.get("render_canon", {}).get("props", {}),
            ))
            validate_speech_timing(values[-1])
        conn.execute(
            "INSERT INTO studio_director_projects(project_id,budget_json) VALUES(?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET approved_hash=NULL",
            (project_id, json.dumps(Budget().model_dump())),
        )
        for shot, contract in zip(shots, values):
            conn.execute(
                "INSERT INTO studio_director_shots(clip_id,contract_json) VALUES(?,?)",
                (shot["id"], json.dumps(contract)),
            )
            apply_contract(conn, shot["id"], contract)
        from calliope.studio import plan_sync

        plan_sync.sync_all(conn, project_id)
        row = conn.execute(
            "SELECT context_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        context = json.loads(row[0]) | {
            "studio_director": True,
            "image_mode": "manual_chatgpt",
            "prepare_only": True,
        }
        conn.execute(
            "UPDATE studio_episodes SET context_json=? WHERE project_id=?",
            (json.dumps(context), project_id),
        )
        conn.execute(
            "DELETE FROM studio_director_drafts WHERE clip_id IN "
            "(SELECT id FROM clips WHERE project_id=?)", (project_id,),
        )
        conn.execute(
            "UPDATE studio_productions SET status='paused',stage='review',error=NULL,"
            "message=? WHERE project_id=? AND status NOT IN ('pending','running')",
            ("Storyboard préparé. Vérifiez et validez les plans avant les images.", project_id),
        )
        service.touch(conn, project_id)
        conn.commit()
    return state(project_id)


def apply_contract(conn, shot_id, value):
    context = service.shot_context(conn, shot_id)["shot"]
    context.setdefault('source_direction', {
        key: context[key] for key in (
            'action', 'reaction', 'dialogue_turns', 'start_state', 'end_state', 'source_beat_index')
        if key in context
    })
    context.update(
        character_keys=value["casting"],
        location_key=value["location"],
        dialogue_turns=[
            {"speaker": t["speaker"], "text": t["text"], "action": "", "reaction": ""}
            for t in value["dialogue"]
        ],
        action=value["action"],
        reaction="",
        start_state=value["initial_state"],
        end_state=value["final_state"],
        framing=value["framing"],
        camera=value["camera"],
        soundscape=value["sound"],
        blocking=json.dumps({"positions": value["positions"], "eyelines": value["eyelines"]}),
    )
    conn.execute(
        "UPDATE studio_shot_context SET context_json=? WHERE clip_id=?",
        (json.dumps(context), shot_id),
    )
    conn.execute(
        "UPDATE studio_shots SET prompt=?,image_prompt=?,dialogue=? WHERE clip_id=?",
        (
            value["action"],
            value["first_frame"],
            " ".join(t["text"] for t in value["dialogue"]),
            shot_id,
        ),
    )
    conn.execute(
        "UPDATE clips SET description=?,duration_sec=? WHERE id=?",
        (value["summary"], value["duration_sec"], shot_id),
    )
    episode = conn.execute(
        "SELECT e.project_id,e.plan_json FROM studio_episodes e JOIN clips c "
        "ON c.project_id=e.project_id WHERE c.id=?",
        (shot_id,),
    ).fetchone()
    plan = json.loads(episode["plan_json"])
    speakers = plan.setdefault("speaker_ids", {})
    for line in value["dialogue"]:
        if line["speaker"] not in speakers:
            used = set(speakers.values())
            index = 1
            while f"S{index}" in used:
                index += 1
            speakers[line["speaker"]] = f"S{index}"
    conn.execute(
        "UPDATE studio_episodes SET plan_json=? WHERE project_id=?",
        (json.dumps(plan), episode["project_id"]),
    )


async def prepare(project_id, internal=False):
    from calliope.studio import series

    if not internal:
        series.require_editable(project_id)
    with service.database() as conn:
        if enabled(conn, project_id):
            current = _state(conn, project_id)
            if current["shots"] and all(s["contract"] for s in current["shots"]):
                return current
        row = conn.execute(
            "SELECT series_id,plan_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Episode de serie introuvable.")
        memory = series.context(conn, row["series_id"])
        plan = json.loads(row["plan_json"])
        props = plan.get("render_canon", {}).get("props", {})
        shots = live_shots(conn, project_id)
        directions = {s["id"]: service.shot_context(conn, s["id"])["shot"] for s in shots}
        baseline = digest([shots, directions])
    if not shots:
        raise HTTPException(409, "Preparez le storyboard avant le Director.")
    contracts = []
    for index, shot in enumerate(shots):
        direction = directions[shot["id"]]
        expected = [{"speaker": t["speaker"], "text": t["text"]}
                    for t in shot.get("dialogue_turns", [])]
        if not expected and shot["dialogue"].strip():
            expected = [{"speaker": direction.get("speaker"), "text": shot["dialogue"].strip()}]
        casting = list(dict.fromkeys(
            shot["character_keys"] + (direction.get("listener_keys") or [])
            + [t["speaker"] for t in expected]
        ))
        previous = next((c for s, c in reversed(list(zip(shots[:index], contracts)))
                         if s.get("purpose") not in ("midroll", "outro")), None)
        requirements = {
            "casting": casting, "location": direction.get("location_key"),
            "duration_sec": shot["duration_sec"], "dialogue": expected,
            "required_state_keys": sorted(set(casting) | set(direction.get("prop_keys") or [])),
            "allowed_state_keys": sorted(
                {e["key"] for e in memory["entities"]}
                | set(props)
            ),
            "continuation_state": previous["final_state"]
            if previous and shot["continue_from_previous"] else None,
        }
        if plan.get('prompt_contract_version') == 2:
            actions = action_sequence(direction)
            if actions:
                requirements.update(action=' '.join(actions), action_sequence=actions)
            requirements['prop_states'] = {
                field: {key: value for key, value in direction.get(source_field, {}).items()
                        if key in props}
                for field, source_field in (
                    ('initial_state', 'start_state'), ('final_state', 'end_state'))
            }
        schema = ShotContract.model_json_schema()
        for field in ("casting", "location", "duration_sec"):
            schema["properties"][field]["const"] = requirements[field]
        for field in ("positions", "eyelines"):
            value_schema = next(iter(schema["properties"][field]["patternProperties"].values()))
            schema["properties"][field] = {
                "type": "object", "properties": {key: value_schema for key in casting},
                "required": casting, "additionalProperties": False,
            }
        schema["properties"]["dialogue"] = {
            "type": "array", "minItems": len(expected), "maxItems": len(expected),
            "items": False,
        }
        if expected:
            schema["properties"]["dialogue"]["prefixItems"] = [
                {"allOf": [{"$ref": "#/$defs/Line"}],
                 "properties": {key: {"const": line[key]} for key in ("speaker", "text")}}
                for line in expected
            ]
        if 'action_sequence' in requirements:
            schema['properties']['action']['const'] = requirements['action']
            schema['properties']['chronology'] = {
                'type': 'array', 'minItems': len(requirements['action_sequence']),
                'maxItems': len(requirements['action_sequence']), 'items': False,
                'prefixItems': [
                    {'allOf': [{'$ref': '#/$defs/Beat'}],
                     'properties': {'action': {'const': action}}}
                    for action in requirements['action_sequence']
                ],
            }
        for field in ('initial_state', 'final_state'):
            values = requirements.get('prop_states', {}).get(field, {})
            required = set(requirements['required_state_keys']) | values.keys()
            if field == 'initial_state' and requirements['continuation_state']:
                required -= requirements['continuation_state'].keys()
            value_schema = next(iter(schema['properties'][field]['patternProperties'].values()))
            schema['properties'][field]['properties'] = {
                key: {'const': values[key]} if key in values else value_schema
                for key in sorted(required)}
            schema['properties'][field]['required'] = sorted(required)
        schema = {
            "$defs": schema.pop("$defs", {}),
            "type": "object", "additionalProperties": False, "required": ["shots"],
            "properties": {"shots": {
                "type": "array", "minItems": 1, "maxItems": 1, "items": schema,
            }},
        }
        source = scrub({
            "memory": memory,
            "plan": {key: plan[key] for key in (
                "title", "summary", "events", "speaker_ids", "render_canon",
            ) if key in plan},
            "shots": [{k: v for k, v in shot.items() if k != "takes"}
                      | {"direction": directions[shot["id"]]}],
            "requirements": requirements,
            "previous_contract": previous,
            "next_direction": (
                directions[shots[index + 1]["id"]] if index + 1 < len(shots) else None
            ),
        })
        request_hash = digest(source)
        # Keep the cache tied to the full inputs, including existing validated drafts.
        scoped = ' '.join(action_sequence(direction))
        if direction.get("dialogue_turns") and scoped:
            source["shots"][0].update(description=scrub(scoped), prompt=scrub(scoped))
        if previous:
            source["previous_contract"] = {
                key: source["previous_contract"][key] for key in (
                    "location", "casting", "final_state", "framing", "positions", "eyelines",
                )
            }
        if source["next_direction"]:
            source["next_direction"] = {
                key: value for key, value in source["next_direction"].items()
                if key in ("location_key", "character_keys", "start_state", "framing", "blocking")
            }
        with service.database() as conn:
            draft = conn.execute(
                "SELECT contract_json FROM studio_director_drafts "
                "WHERE clip_id=? AND request_hash=?",
                (shot["id"], request_hash),
            ).fetchone()
        for attempt in range(3):
            if draft and attempt == 0:
                response = {"shots": [json.loads(draft[0])]}
            else:
                try:
                    response = await generate_structured(
                        [
                            {"role": "system", "content": ROLE
                             + '\nReturn ONLY one JSON object {"shots":[ShotContract]} '
                             "for the ONE supplied shot. Be concise. Never repeat the episode. "
                             "Follow requirements using canonical IDs, never display names. "
                             "positions and eyelines must use exactly the casting IDs as keys, "
                             "with nonempty string values. State maps also have string values. "
                             "Cover required_state_keys in initial_state and final_state, using "
                             "only allowed_state_keys. Include casting and location in references. "
                             "Preserve exact dialogue, speakers, casting, location and duration. "
                             "requirements.dialogue is the exhaustive ordered list for THIS take; "
                             "copy its speakers and text verbatim, adding only start/end times. "
                             "When action_sequence is supplied, copy requirements.action verbatim "
                             "and include each supplied action once in chronology, in order, "
                             "adding only at_sec times. Do not expand or rewrite these events. "
                             "For a continuation, software inherits continuation_state exactly. "
                             "initial_state need only add new characters or props. Frame composition, "
                             "positions and eyelines must honor the inherited starting state. "
                             "Copy prop_states exactly; choose consistent character poses. "
                             "All other characters stay silent. Previous/next data are spatial "
                             "continuity only, never permission to add a reply to this take. "
                             "Time dialogue without overlaps and keep chronological beats sorted "
                             "and within duration. Use previous_contract and next_direction for "
                             "continuity. If invalid_contract and validation_error are supplied, "
                             "correct only the invalid fields, preserving valid fields. "
                             "Schema: " + json.dumps(schema)},
                            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
                        ],
                        temperature=0.2, max_tokens=6000, json_mode=True, retry_invalid=False,
                        diagnostics_dir=(settings.assets_dir / str(project_id)
                                         / "studio" / "diagnostics"),
                    )
                except (ValueError, RuntimeError) as exc:
                    raise HTTPException(
                        422, f"Plan {index + 1}/{len(shots)} : {service.safe_error(exc)} "
                        "Les contrats déjà préparés sont conservés pour la prochaine tentative.",
                    ) from None
            if isinstance(response, dict) and "shots" not in response:
                try:
                    validate_contract(response, memory["entities"], props)
                except ValueError:
                    pass
                else:
                    response = {"shots": [response]}
            values = response.get("shots") if isinstance(response, dict) else None
            value = values[0] if isinstance(values, list) and len(values) == 1 else None
            try:
                if value is None:
                    raise ValueError("Un seul contrat est requis.")
                if isinstance(value, dict) and isinstance(value.get('initial_state'), dict) and requirements['continuation_state']:
                    value = value | {'initial_state': value['initial_state'] | requirements['continuation_state']}
                valid = validate_contract(value, memory["entities"], props)
                validate_speech_timing(valid)
                for field in ("casting", "location", "duration_sec"):
                    actual = set(valid[field]) if field == "casting" else valid[field]
                    wanted = set(requirements[field]) if field == "casting" else requirements[field]
                    if actual != wanted:
                        raise ValueError(f"{field}: respecter la valeur fournie dans requirements.")
                if 'action_sequence' in requirements and (
                        valid['action'] != requirements['action'] or
                        [beat['action'] for beat in valid['chronology']]
                        != requirements['action_sequence']):
                    raise ValueError(
                        'action_sequence: conserver les actions fournies, '
                        'une fois et dans leur ordre.')
                for field, values in requirements.get('prop_states', {}).items():
                    if any(valid[field].get(key) != value for key, value in values.items()):
                        raise ValueError(
                            f'{field}: conserver exactement les états des objets fournis.')
                if [{"speaker": t["speaker"], "text": t["text"]}
                        for t in valid["dialogue"]] != expected:
                    actual = [{"speaker": t["speaker"], "text": t["text"]}
                              for t in valid["dialogue"]]
                    raise ValueError(
                        "dialogue: conserver les locuteurs et paroles exacts. "
                        "Attendu: " + json.dumps(expected, ensure_ascii=False)
                        + "; reçu: " + json.dumps(actual, ensure_ascii=False)
                    )
                for field in ("initial_state", "final_state"):
                    if not set(requirements["required_state_keys"]) <= valid[field].keys():
                        raise ValueError(f"{field}: couvrir tous les required_state_keys.")
                    if not valid[field].keys() <= set(requirements["allowed_state_keys"]):
                        raise ValueError(f"{field}: utiliser uniquement les allowed_state_keys.")
                if requirements["continuation_state"] and any(
                    valid["initial_state"].get(k) != v
                    for k, v in requirements["continuation_state"].items()
                ):
                    raise ValueError("initial_state: reprendre exactement continuation_state.")
            except ValueError as exc:
                error = service.safe_error(exc)
                directory = settings.assets_dir / str(project_id) / "studio" / "diagnostics"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"director-{shot['id']}-{uuid.uuid4().hex}.json").write_text(
                    json.dumps(scrub({"response": response, "requirements": requirements,
                                      "validation_error": error}),
                               ensure_ascii=False, indent=2), encoding="utf-8",
                )
                if attempt == 2:
                    raise HTTPException(
                        422, f"Plan {index + 1}: {error} "
                        "Deux corrections automatiques ont échoué. "
                        "Les autres contrats sont conservés.",
                    ) from None
                source.update(invalid_contract=scrub(value), validation_error=error)
                continue
            break
        contracts.append(valid)
        if not internal:
            series.require_editable(project_id)
        with service.database() as conn:
            conn.execute(
                "INSERT INTO studio_director_drafts(clip_id,request_hash,contract_json) "
                "VALUES(?,?,?) "
                "ON CONFLICT(clip_id) DO UPDATE SET request_hash=excluded.request_hash,"
                "contract_json=excluded.contract_json",
                (shot["id"], request_hash, json.dumps(valid)),
            )
            conn.commit()
    if not internal:
        series.require_editable(project_id)
    with service.database() as conn:
        current = live_shots(conn, project_id)
        current_directions = {s["id"]: service.shot_context(conn, s["id"])["shot"] for s in current}
        if digest([current, current_directions]) != baseline:
            raise HTTPException(409, "Le storyboard a change pendant la preparation.")
    return initialize(project_id, contracts)


def approve(project_id, expected_hash):
    from calliope.studio import series

    series.require_editable(project_id)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_hash(conn, project_id, expected_hash)
        validate_live(conn, project_id, current)
        conn.execute(
            "UPDATE studio_director_projects SET approved_hash=? WHERE project_id=?",
            (expected_hash, project_id),
        )
        conn.commit()
    return state(project_id)


def validate_live(conn, project_id, current, require_ready=True, speech_shot_ids=None):
    from calliope.studio.authored_script import authored_story

    shots = live_shots(conn, project_id)
    episode = conn.execute(
        'SELECT context_json,plan_json,idea FROM studio_episodes WHERE project_id=?',
        (project_id,),
    ).fetchone()
    frozen, plan = json.loads(episode['context_json']), json.loads(episode['plan_json'])
    if require_ready and plan.get('prompt_contract_version') == 2:
        seconds = sum(shot['duration_sec'] for shot in shots)
        target = float(frozen.get('target_seconds', 65))
        if abs(seconds - target) > max(2., target * .1) + 1e-6:
            raise HTTPException(
                422, f'Durée prévue {seconds:g} s, cible {target:g} s : '
                'repréparez le découpage sans ajouter de pauses de remplissage.')
        try:
            supplied = authored_story(episode['idea'], frozen.get('entities', []))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if supplied and not plan.get('creative_story', {}).get('source_locked'):
            raise HTTPException(
                422, 'Ce scénario fourni a été réécrit par une ancienne préparation. '
                "Recommencez cet épisode depuis son idée originale ; ses données seront archivées.")
    previous = None
    for plan_number, (entry, shot) in enumerate(zip(current["shots"], shots), 1):
        context = service.shot_context(conn, shot["id"])
        try:
            contract = validate_contract(
                entry["contract"], context["entities"],
                context.get("render_canon", {}).get("props", {}),
            )
        except ValueError as exc:
            raise HTTPException(422, f"Plan {plan_number}: {exc}") from None
        if contract["duration_sec"] != shot["duration_sec"]:
            raise HTTPException(422, f"Plan {plan_number}: duree differente du contrat.")
        if shot["prompt"] != contract["action"] or shot["image_prompt"] != contract["first_frame"]:
            raise HTTPException(
                422, f"Plan {plan_number}: synchronisez les prompts depuis le contrat expert."
            )
        if contract["dialogue"] and shot["audio_mode"] != "native":
            raise HTTPException(422, f"Plan {plan_number}: dialogue present mais audio desactive.")
        if speech_shot_ids is None or shot['id'] in speech_shot_ids:
            try:
                validate_speech_timing(contract)
            except ValueError as exc:
                raise HTTPException(422, f"Plan {plan_number}: {exc}") from None
        required_states = set(context["shot"].get("prop_keys") or [])
        known_states = {e["key"] for e in context["entities"]} | set(
            context.get("render_canon", {}).get("props", {})
        )
        if (
            not required_states <= contract["initial_state"].keys()
            or not required_states <= contract["final_state"].keys()
        ):
            raise HTTPException(422, f"Plan {plan_number}: etats des objets du canon incomplets.")
        if not (contract["initial_state"].keys() | contract["final_state"].keys()) <= known_states:
            raise HTTPException(
                422, f"Plan {plan_number}: etat rattache a une identite ou un objet inconnu."
            )
        if " ".join(t["text"] for t in contract["dialogue"]) != shot["dialogue"].strip():
            raise HTTPException(422, f"Plan {plan_number}: paroles differentes du contrat.")
        turns = shot.get("dialogue_turns") or []
        if turns and [(t["speaker"], t["text"]) for t in turns] != [
            (t["speaker"], t["text"]) for t in contract["dialogue"]
        ]:
            raise HTTPException(422, f"Plan {plan_number}: attribution des voix differente.")
        if shot["continue_from_previous"]:
            if previous is None or previous["location"] != contract["location"]:
                raise HTTPException(422, "Raccord sans plan precedent dans le meme lieu.")
            for key, value in previous["final_state"].items():
                if contract["initial_state"].get(key) != value:
                    raise HTTPException(422, f"Plan {plan_number}: raccord incompatible pour {key}.")
        if shot.get("purpose") not in ("midroll", "outro"):
            previous = contract


def build_image_prompt(entry, plan):
    c = entry["contract"]
    lines = [
        "Génère UNE image de départ (t=0), verticale 9:16, sans texte, bulles, filigranes ni interface.",
        "Scène INDÉPENDANTE : ne reprends pas la composition ni le décor des autres plans.",
        "Lieu : " + c["location"] + ". Personnages : " + (", ".join(c["casting"]) or "aucun") + ".",
        "Première image : " + c["first_frame"],
    ]
    if c.get("positions"):
        lines.append("Positions : " + "; ".join(k + " = " + v for k, v in c["positions"].items()))
    if c.get("eyelines"):
        lines.append("Regards : " + "; ".join(k + " = " + v for k, v in c["eyelines"].items()))
    lines.append("Cadrage : " + c["framing"])
    if c.get("initial_state"):
        lines.append("État initial : " + "; ".join(k + " = " + v for k, v in c["initial_state"].items()))
    if c.get("continuity"):
        lines.append("Continuité : " + " ".join(c["continuity"]))
    return "\n".join(lines)

def package(project_id):
    with service.database() as conn:
        current = _state(conn, project_id)
        if not current.get("approved"):
            raise HTTPException(409, "Validez le storyboard avant le paquet ChatGPT.")
        validate_live(conn, project_id, current)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest = {
                "version": 1,
                "project_id": project_id,
                "storyboard_hash": current["storyboard_hash"],
                "shots": [],
            }
            archive.writestr(
                "README.txt",
                "Generation MANUELLE dans une conversation ChatGPT.\n"
                "Joins UNIQUEMENT ces fichiers a ChatGPT :\n"
                "- prompts-episode-" + str(project_id) + ".txt (tous les plans a generer)\n"
                "- les images du dossier references/ (4 images)\n"
                "ChatGPT produit une image de depart par plan indique. Enregistre chaque "
                "image sous le nom indique (ou tout autre nom), puis importe-les dans "
                "Studio IA et choisis le plan correspondant dans le menu.\n"
                "Les plans en continuation sont sans image : ils partent de la derniere "
                "image du plan precedent.\n"
                "Aucun appel automatique a ChatGPT.\n",
            )
            written = set()
            legend = {}
            guide = ["EPISODE " + str(project_id) + " - IMAGES A GENERER AVEC CHATGPT", ""]
            continuations = []
            for index, entry in enumerate(current["shots"]):
                context = service.shot_context(conn, entry["id"])
                refs = []
                for entity in context["entities"]:
                    if entity["key"] not in entry["contract"]["references"]:
                        continue
                    path = Path(entity.get("reference_path") or "")
                    name = (
                        f"references/{entity['key']}{path.suffix.lower()}"
                        if path.is_file()
                        else None
                    )
                    if name and name not in written:
                        archive.write(path, name)
                        written.add(name)
                    refs.append(
                        {
                            "key": entity["key"],
                            "kind": entity["kind"],
                            "description": entity["description"],
                            "file": name,
                            "available": bool(name),
                        }
                    )
                    if name:
                        legend.setdefault(name, (entity["key"], entity["description"]))
                entity_keys = {entity["key"] for entity in context["entities"]}
                for key, description in context.get("render_canon", {}).get("props", {}).items():
                    if key in entry["contract"]["references"] and key not in entity_keys:
                        refs.append({
                            "key": key, "kind": "prop", "description": description,
                            "file": None, "available": False,
                        })
                c = entry["contract"]
                prompt = (
                    "Create ONE starting still, t=0 only. No captions, speech bubbles, "
                    "watermarks or interface text. Leave screen_text zones blank for software "
                    "overlays. Preserve attached "
                    "character/location designs. Do not depict future action.\n"
                    + json.dumps(
                        scrub(
                            {
                                "first_frame": c["first_frame"],
                                "casting": c["casting"],
                                "location": c["location"],
                                "positions": c["positions"],
                                "eyelines": c["eyelines"],
                                "framing": c["framing"],
                                "initial_state": c["initial_state"],
                                "continuity": c["continuity"],
                                "references": refs,
                                "style": context.get("render_canon", {}).get("style", ""),
                                "screen_text": [
                                    {
                                        k: v
                                        for k, v in label.items()
                                        if k not in ("text", "value", "anchor_binding")
                                    }
                                    for label in context.get("shot", {}).get("screen_text", [])
                                ],
                                "aspect_ratio": context.get("aspect_ratio", "9:16"),
                            }
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                prompt_file = "prompts-episode-" + str(project_id) + ".txt"
                if entry["continue_from_previous"]:
                    continuations.append(index + 1)
                else:
                    title = "PLAN " + str(index + 1)
                    if entry.get("purpose") in ("midroll", "outro"):
                        title += " (CTA)"
                    guide.append(
                        "=" * 70
                        + "\n"
                        + title
                        + " - fichier attendu : "
                        + entry["filename"]
                        + "\n"
                        + "=" * 70
                        + "\n"
                        + "PROMPT À COLLER (envoie ce bloc seul, avec les références jointes) :\n"
                        + build_image_prompt(entry, index + 1)
                        + "\n\n"
                        + "Détails techniques (optionnel) :\n"
                        + prompt
                        + "\n"
                    )
                manifest["shots"].append(
                    {
                        "id": entry["id"],
                        "plan": index + 1,
                        "filename": entry["filename"],
                        "shot_hash": entry["hash"],
                        "prompt_file": prompt_file,
                        "references": refs,
                        "continuation": entry["continue_from_previous"],
                    }
                )



            guide.insert(2, "References jointes : " + ", ".join(Path(n).name for n in legend))
            guide.insert(3, "Consignes : une image t=0 par plan ci-dessous, dans cet ordre. Pas de texte, bulles, filigranes ni interface. Format 9:16. Aucune action future.")
            guide.insert(4, "")
            if continuations:
                guide.append("Plans sans image (continuations) : " + ", ".join("Plan " + str(n) for n in continuations) + ".")
            archive.writestr(
                "prompts-episode-" + str(project_id) + ".txt",
                "\n".join(guide),
            )
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        return buffer.getvalue()


def image_prompt(project_id, shot_id):
    with service.database() as conn:
        current = _state(conn, project_id)
        if not current.get("approved"):
            raise HTTPException(409, "Validez le storyboard avant le prompt image.")
        entry = next((s for s in current["shots"] if s["id"] == shot_id), None)
        if not entry:
            raise HTTPException(404, "Plan introuvable.")
        if entry["continue_from_previous"]:
            raise HTTPException(409, "Ce plan est une continuation: aucune image de depart necessaire.")
    plan = next(i + 1 for i, s in enumerate(current["shots"]) if s["id"] == shot_id)
    return {"shot_id": shot_id, "plan": plan, "prompt": build_image_prompt(entry, plan)}

def import_images(project_id, expected_hash, uploads, shot_ids=None):
    import uuid

    from calliope.studio import series

    series.require_editable(project_id)
    if not uploads or len(uploads) > 100:
        raise HTTPException(422, "Importez entre 1 et 100 images.")
    if shot_ids is not None and len(shot_ids) != len(uploads):
        raise HTTPException(422, "Indiquez un plan par image.")
    prepared, seen = [], set()
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_hash(conn, project_id, expected_hash, approved=True)
        by_stem = {Path(s["filename"]).stem: s for s in current["shots"]}
        by_id = {s["id"]: s for s in current["shots"]}
        for index, (filename, content) in enumerate(uploads):
            shot_id = shot_ids[index] if shot_ids is not None else None
            if shot_id is not None:
                entry = by_id.get(shot_id)
                if entry is None:
                    raise HTTPException(422, "Plan inconnu: " + str(shot_id))
            else:
                entry = by_stem.get(Path(filename).stem)
                if entry is None:
                    raise HTTPException(
                        422, "Nom inconnu, paquet obsolete ou plan en double: " + filename[:150]
                    )
            if entry["id"] in seen:
                raise HTTPException(422, "Plan en double: " + filename[:150])
            if Path(filename).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                raise HTTPException(422, "Format non pris en charge: " + filename[:150])
            if len(content) > 25 * 1024 * 1024:
                raise HTTPException(413, "Image superieure a 25 Mo.")
            try:
                normalized, metadata = service.normalized_image(content)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            seen.add(entry["id"])
            prepared.append((entry, normalized, metadata))
        directory = settings.assets_dir / str(project_id) / "studio" / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        for entry, content, metadata in prepared:
            token = uuid.uuid4().hex
            path = directory / (token + ".png")
            path.write_bytes(content)
            take_id = conn.execute(
                "INSERT INTO studio_takes(project_id,clip_id,kind,status,prompt,engine,request_id,"
                "request_hash,output_path,metadata_json) "
                "VALUES(?,?,'image','done',?,'import',?,?,?,?)",
                (
                    project_id,
                    entry["id"],
                    "ChatGPT - import manuel",
                    token,
                    token,
                    str(path),
                    json.dumps(metadata),
                ),
            ).lastrowid
            conn.execute(
                "UPDATE studio_shots SET selected_image_id=?,selected_video_id=NULL,approved=0,"
                "trim_start_sec=NULL,trim_end_sec=NULL "
                "WHERE clip_id=?",
                (take_id, entry["id"]),
            )
            conn.execute("UPDATE clips SET clip_path=NULL WHERE id=?", (entry["id"],))
        invalidate_dependents(conn, project_id, seen)
        service.touch(conn, project_id)
        conn.commit()
    return state(project_id)


def review_image(project_id, take_id, expected_hash, approved):
    from calliope.studio import series

    series.require_editable(project_id)
    with service.database() as conn:
        current = require_hash(conn, project_id, expected_hash, approved=True)
        if not any(s["image"] and s["image"]["take_id"] == take_id for s in current["shots"]):
            raise HTTPException(404, "Image selectionnee introuvable.")
    service.review_take_visuals(take_id, approved)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_hash(conn, project_id, expected_hash, approved=True)
        entry = next(
            (s for s in current["shots"] if s["image"] and s["image"]["take_id"] == take_id), None
        )
        if not entry:
            raise HTTPException(404, "Image selectionnee introuvable.")
        take = conn.execute("SELECT * FROM studio_takes WHERE id=?", (take_id,)).fetchone()
        if not Path(take["output_path"]).is_file():
            raise HTTPException(422, "Image absente.")
        metadata = json.loads(take["metadata_json"])
        metadata["director_image_review"] = {
            "approved": approved,
            "shot_hash": entry["hash"],
            "file_hash": file_hash(take["output_path"]),
        }
        conn.execute(
            "UPDATE studio_takes SET metadata_json=? WHERE id=?", (json.dumps(metadata), take_id)
        )
        conn.commit()
    return state(project_id)


def preflight(project_id, shot_ids=None):
    from calliope.engines.h3_graph import H3_FPS, frames_for_duration
    from calliope.studio.h3_prompt import compile_video_prompt

    errors, warnings = (
        [],
        ["Identite, cadrage, texte et raccord visuel: a verifier par une personne."],
    )
    with service.database() as conn:
        current = _state(conn, project_id)
        if not current["enabled"]:
            raise HTTPException(409, "Activez le Director pour ce projet.")
        if not current["approved"]:
            errors.append("Storyboard non valide ou modifie depuis sa validation.")
        try:
            validate_live(conn, project_id, current)
        except HTTPException as exc:
            errors.append(str(exc.detail))
        budget = Budget.model_validate(current["budget"])
        spent = conn.execute(
            "SELECT COALESCE(SUM(estimated_eur),0),COALESCE(SUM(video_seconds),0),"
            "COUNT(*) FROM studio_director_reservations WHERE project_id=?",
            (project_id,),
        ).fetchone()
        seconds, count = 0.0, 0
        ids = set(shot_ids) if shot_ids is not None else None
        for plan_number, (entry, shot) in enumerate(zip(current["shots"], live_shots(conn, project_id)), 1):
            if ids is not None and shot["id"] not in ids:
                continue
            if shot["approved"] and shot["selected_video_id"]:
                take = conn.execute(
                    "SELECT * FROM studio_takes WHERE id=?", (shot["selected_video_id"],)
                ).fetchone()
                try:
                    if not take or not Path(take["output_path"] or "").is_file():
                        raise HTTPException(422, "QC obsolete: video absente.")
                    guard_approval(conn, shot, take)
                except HTTPException as exc:
                    errors.append(f"Plan {plan_number}: QC a refaire. {exc.detail}")
                continue
            if not shot["continue_from_previous"] and not (
                entry["image"] and entry["image"]["approved"]
            ):
                errors.append(f"Plan {plan_number}: image premiere frame absente ou non validee.")
            if shot["continue_from_previous"]:
                try:
                    source = service._previous_video(conn, shot["id"])
                except HTTPException:
                    warnings.append(
                        f"Plan {plan_number}: derniere frame a verifier apres la prise precedente."
                    )
                else:
                    previous = service.public_shot(conn, source["clip_id"])
                    if previous["approved"]:
                        try:
                            service.require_source_visual_review(
                                source,
                                shot,
                                service.shot_context(conn, shot["id"]),
                                previous.get("trim_end_sec"),
                            )
                        except HTTPException as exc:
                            errors.append(f"Plan {plan_number}: {exc.detail}")
            try:
                context = service.shot_context(conn, shot["id"])
                prompt = compile_video_prompt(
                    shot | context["shot"],
                    context,
                    first_frame=True,
                    video_profile=settings.studio_video_profile,
                )
                from calliope.engines.modal_h3 import build_request

                build_request(
                    prompt=prompt,
                    duration_sec=shot["duration_sec"],
                    aspect_ratio=context.get("aspect_ratio", "9:16"),
                    seed=0,
                    audio_mode=shot["audio_mode"],
                    video_profile=settings.studio_video_profile,
                )
                duration = (
                    frames_for_duration(shot["duration_sec"], settings.studio_video_profile)
                    / H3_FPS
                )
            except (ValueError, KeyError) as exc:
                errors.append(f"Plan {plan_number}: {service.safe_error(exc)}")
                continue
            if not shot["selected_video_id"]:
                seconds += duration
                count += 1
        rate = budget.estimated_eur_per_video_second
        estimate = round(seconds * rate, 6) if rate is not None else None
        if rate is None:
            errors.append("Renseignez le cout estimatif par seconde video dans le panneau expert.")
        if count + spent[2] > budget.max_new_takes or seconds + spent[1] > budget.max_video_seconds:
            errors.append("Plafond de prises ou de secondes video depasse.")
        if estimate is not None and estimate + spent[0] > budget.max_cost_eur:
            errors.append("Budget estimatif depasse avant allocation GPU.")
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "storyboard_hash": current["storyboard_hash"],
            "cost": {
                "estimated_eur": estimate,
                "reserved_eur": spent[0],
                "new_takes": count,
                "video_seconds": round(seconds, 3),
                "notice": "Estimation operateur, pas une garantie de facturation.",
            },
        }


def guard_generation(conn, shot, kind):
    if not enabled(conn, shot["project_id"]):
        return
    if kind == "image":
        raise HTTPException(422, "Images manuelles uniquement: exportez le paquet pour ChatGPT.")
    current = _state(conn, shot["project_id"])
    if not current["approved"]:
        raise HTTPException(422, "Validez le storyboard avant tout GPU.")
    validate_live(conn, shot["project_id"], current)
    entry = next(s for s in current["shots"] if s["id"] == shot["id"])
    if not shot["continue_from_previous"] and not (entry["image"] and entry["image"]["approved"]):
        raise HTTPException(422, "Validez la premiere image avant tout GPU.")


def invalidate_dependents(conn, project_id, selected):
    if not enabled(conn, project_id):
        return
    for shot_id in dependent_ids(live_shots(conn, project_id), set(selected)) - set(selected):
        conn.execute(
            "UPDATE studio_shots SET selected_video_id=NULL,approved=0,trim_start_sec=NULL,"
            "trim_end_sec=NULL WHERE clip_id=?",
            (shot_id,),
        )
        conn.execute("UPDATE clips SET clip_path=NULL WHERE id=?", (shot_id,))


def reserve(conn, shot, take_id):
    if not enabled(conn, shot["project_id"]):
        return
    from calliope.engines.h3_graph import H3_FPS, frames_for_duration

    row = conn.execute(
        "SELECT budget_json FROM studio_director_projects WHERE project_id=?", (shot["project_id"],)
    ).fetchone()
    budget = Budget.model_validate_json(row[0])
    rate = budget.estimated_eur_per_video_second
    if rate is None:
        raise HTTPException(422, "Cout estimatif requis avant GPU.")
    duration = frames_for_duration(shot["duration_sec"], settings.studio_video_profile) / H3_FPS
    spent = conn.execute(
        "SELECT COALESCE(SUM(estimated_eur),0),COALESCE(SUM(video_seconds),0),"
        "COUNT(*) FROM studio_director_reservations WHERE project_id=?",
        (shot["project_id"],),
    ).fetchone()
    cost = duration * rate
    if (
        spent[0] + cost > budget.max_cost_eur
        or spent[1] + duration > budget.max_video_seconds
        or spent[2] + 1 > budget.max_new_takes
    ):
        raise HTTPException(422, "Plafond de production depasse avant GPU.")
    conn.execute(
        "INSERT INTO studio_director_reservations(take_id,project_id,estimated_eur,video_seconds) "
        "VALUES(?,?,?,?)",
        (take_id, shot["project_id"], cost, duration),
    )


def guard_budget(conn, project_id):
    row = conn.execute(
        "SELECT budget_json FROM studio_director_projects WHERE project_id=?", (project_id,)
    ).fetchone()
    budget = Budget.model_validate_json(row[0])
    if budget.estimated_eur_per_video_second is None:
        raise HTTPException(422, "Cout estimatif requis avant GPU.")
    for pending in conn.execute(
        "SELECT r.take_id,r.video_seconds FROM studio_director_reservations r "
        "JOIN studio_takes t ON t.id=r.take_id WHERE r.project_id=? AND t.submission_state='new' "
        "AND t.modal_call_id IS NULL AND t.status IN ('pending','running')",
        (project_id,),
    ).fetchall():
        conn.execute(
            "UPDATE studio_director_reservations SET estimated_eur=? WHERE take_id=?",
            (pending["video_seconds"] * budget.estimated_eur_per_video_second, pending["take_id"]),
        )
    spent = conn.execute(
        "SELECT COALESCE(SUM(estimated_eur),0),COALESCE(SUM(video_seconds),0),"
        "COUNT(*) FROM studio_director_reservations WHERE project_id=?",
        (project_id,),
    ).fetchone()
    if (
        spent[0] > budget.max_cost_eur
        or spent[1] > budget.max_video_seconds
        or spent[2] > budget.max_new_takes
    ):
        raise HTTPException(
            422, "Le budget courant est inferieur aux demandes reservees. GPU bloque."
        )


def dependent_ids(shots, selected):
    affected, connected = set(selected), False
    for shot in shots:
        if shot["id"] in selected:
            connected = shot.get("purpose") not in ("midroll", "outro")
        elif shot.get("purpose") in ("midroll", "outro"):
            continue
        elif connected and shot["continue_from_previous"]:
            affected.add(shot["id"])
        else:
            connected = False
    return affected


async def revise(project_id, expected_hash, shot_ids, instruction, contracts=None):
    from calliope.studio import series

    series.require_editable(project_id)
    with service.database() as conn:
        before = require_hash(conn, project_id, expected_hash)
        wanted = set(shot_ids)
        if (
            not wanted
            or len(wanted) != len(shot_ids)
            or not wanted <= {s["id"] for s in before["shots"]}
        ):
            raise HTTPException(422, "Selection de plans invalide.")
        episode = conn.execute(
            "SELECT series_id,plan_json FROM studio_episodes WHERE project_id=?", (project_id,)
        ).fetchone()
        memory = series.context(conn, episode[0])
        props = json.loads(episode["plan_json"]).get("render_canon", {}).get("props", {})
    if contracts is None:
        contract_schema = ShotContract.model_json_schema()
        schema = {
            "$defs": contract_schema.pop("$defs", {}),
            "type": "object", "additionalProperties": False, "required": ["shots"],
            "properties": {"shots": {
                "type": "array", "minItems": len(wanted), "maxItems": len(wanted),
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "contract"],
                    "properties": {
                        "id": {"type": "integer", "enum": sorted(wanted)},
                        "contract": contract_schema,
                    },
                },
            }},
        }
        messages = [
                {
                    "role": "system",
                    "content": ROLE
                    + '\nReturn ONLY one JSON object {"shots":[{"id":integer,"contract":ShotContract}]}. '
                    "Return exactly the requested IDs, no others. "
                    "Do not change casting identity or the CTA dialogue. "
                    "Schema: " + json.dumps(schema),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        scrub(
                            {
                                "memory": memory,
                                "storyboard": before["shots"],
                                "shot_ids": shot_ids,
                                "revision": instruction,
                            }
                        )
                    ),
                },
            ]
        proposed_dialogue = {}
        for attempt in range(3):
            response = await generate_structured(
                messages, temperature=0.2, max_tokens=16000, json_mode=True,
                required_keys=("shots",), retry_invalid=False,
                diagnostics_dir=settings.assets_dir / str(project_id) / "studio" / "diagnostics",
            )
            contracts = response.get("shots") if isinstance(response, dict) else None
            if not isinstance(contracts, list) or any(
                not isinstance(item, dict) or item.get('id') not in wanted or 'contract' not in item
                for item in contracts
            ):
                break
            try:
                timing_errors = []
                for item in contracts:
                    valid = validate_contract(item['contract'], memory['entities'], props)
                    dialogue = [(line['speaker'], line['text']) for line in valid['dialogue']]
                    if item['id'] in proposed_dialogue and dialogue != proposed_dialogue[item['id']]:
                        raise ValueError('Conserver les paroles et locuteurs de la première proposition.')
                    proposed_dialogue[item['id']] = dialogue
                    try:
                        validate_speech_timing(valid)
                    except ValueError as exc:
                        number = next(i + 1 for i, shot in enumerate(before['shots']) if shot['id'] == item['id'])
                        timing_errors.append(f'Plan {number}: {exc}')
                if timing_errors:
                    raise ValueError('\n'.join(timing_errors))
            except ValueError as exc:
                if attempt == 2:
                    raise HTTPException(422, f'Révision non enregistrée après trois tentatives : {exc}') from None
                messages.extend([
                    {'role': 'assistant', 'content': json.dumps(scrub(response), ensure_ascii=False)},
                    {'role': 'user', 'content': 'Corrige cette erreur avant de renvoyer les contrats : '
                     + str(exc) + ' Conserve les paroles et locuteurs de la proposition. '
                     'Utilise le temps disponible ; si nécessaire, adapte la durée des seuls plans sélectionnés.'},
                ])
                continue
            break
    if (
        not isinstance(contracts, list)
        or any(
            not isinstance(s, dict) or type(s.get("id")) is not int or "contract" not in s
            for s in contracts
        )
        or len(contracts) != len(wanted)
        or {s.get("id") for s in contracts} != wanted
    ):
        raise HTTPException(
            422, "DeepSeek a modifie un plan hors selection ou omis un plan demande."
        )
    updates = {}
    story_changed = False
    for item in contracts:
        try:
            updates[item["id"]] = validate_contract(item["contract"], memory["entities"], props)
            validate_speech_timing(updates[item["id"]])
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, f"Contrat de revision invalide: {exc}") from None
        original = next(s for s in before["shots"] if s["id"] == item["id"])
        if original["purpose"] not in ("midroll", "outro") and any(
            updates[item["id"]][key] != original["contract"][key]
            for key in ("summary", "action", "dialogue", "initial_state", "final_state")
        ):
            story_changed = True
        if (
            original["purpose"] in ("midroll", "outro")
            and [(line["speaker"], line["text"]) for line in updates[item["id"]]["dialogue"]]
            != [(line["speaker"], line["text"]) for line in original["contract"]["dialogue"]]
        ):
            raise HTTPException(422, "Les paroles du CTA sont conservees.")
    series.require_editable(project_id)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_hash(conn, project_id, expected_hash)
        shots = live_shots(conn, project_id)
        affected = dependent_ids(shots, wanted)
        for shot_id, value in updates.items():
            conn.execute(
                "UPDATE studio_director_shots SET contract_json=? WHERE clip_id=?",
                (json.dumps(value), shot_id),
            )
            apply_contract(conn, shot_id, value)
        from calliope.studio import plan_sync

        plan_sync.sync_all(conn, project_id)
        for shot_id in affected:
            conn.execute(
                "UPDATE studio_shots SET selected_video_id=NULL,approved=0,trim_start_sec=NULL,"
                "trim_end_sec=NULL WHERE clip_id=?",
                (shot_id,),
            )
            conn.execute("UPDATE clips SET clip_path=NULL WHERE id=?", (shot_id,))
        validate_live(conn, project_id, _state(conn, project_id), require_ready=False,
                      speech_shot_ids=wanted)
        if story_changed:
            events = [
                shot["description"]
                for shot in live_shots(conn, project_id)
                if shot.get("purpose") not in ("midroll", "outro")
            ]
            row = conn.execute(
                "SELECT plan_json FROM studio_episodes WHERE project_id=?", (project_id,)
            ).fetchone()
            plan = json.loads(row[0]) | {
                "summary": " ".join(events),
                "events": events,
                "ongoing_plot": "",
            }
            conn.execute(
                "UPDATE studio_episodes SET plan_json=?,summary=?,events_json=?,ongoing_plot=? "
                "WHERE project_id=?",
                (json.dumps(plan), plan["summary"], json.dumps(events), "", project_id),
            )
        service.touch(conn, project_id)
        conn.commit()
    return state(project_id)


async def verify_take(project_id, take_id):
    import asyncio

    from calliope.studio import production, transcription

    with service.database() as conn:
        take = conn.execute(
            "SELECT * FROM studio_takes WHERE id=? AND project_id=? AND kind=?",
            (take_id, project_id, "video"),
        ).fetchone()
        if not take:
            raise HTTPException(404, "Prise video introuvable.")
        shot = service.public_shot(conn, take["clip_id"])
        if shot["selected_video_id"] != take_id or take["status"] != "done":
            raise HTTPException(409, "Selectionnez une prise terminee pour la verifier.")
    verdict = await production.assess_take(shot)
    if verdict is None and shot["audio_mode"] == "native":
        # Nonverbal sound can contain hallucinated speech too.
        transcript = await asyncio.to_thread(
            transcription.transcribe_dialogue,
            take["output_path"],
            settings.studio_asr_checkpoint,
            "fr" if not shot.get("language") else shot["language"],
        )
        verdict = {"status": "unknown", "reason": "Transcription indisponible."}
        if transcript.get("status") == "ok":
            verdict = (
                {"status": "extra", "reason": "Paroles imprevues dans un plan sans dialogue."}
                if transcript.get("text", "").strip()
                else {"status": "ok", "reason": ""}
            )
    verdict = verdict or {"status": "unknown", "reason": "Parole non verifiee."}
    with service.database() as conn:
        row = conn.execute(
            "SELECT metadata_json FROM studio_takes WHERE id=?", (take_id,)
        ).fetchone()
        metadata = json.loads(row[0])
        metadata["dialogue_qc"] = verdict
        metadata.pop("director_qc_review", None)
        conn.execute(
            "UPDATE studio_takes SET metadata_json=? WHERE id=?", (json.dumps(metadata), take_id)
        )
        conn.execute("UPDATE studio_shots SET approved=0 WHERE clip_id=?", (shot["id"],))
        if shot["approved"]:
            service.touch(conn, project_id)
        conn.commit()
    return {
        "take_id": take_id,
        "dialogue": verdict,
        "audio_label": "vérifié"
        if verdict["status"] == "ok"
        else "non vérifié"
        if verdict["status"] == "unknown"
        else "à corriger",
        "vision": "a verifier",
        "speaker_identity": "a verifier",
    }


def guard_approval(conn, shot, take):
    if not enabled(conn, shot["project_id"]):
        return
    metadata = json.loads(take["metadata_json"] or "{}")
    verdict = metadata.get("dialogue_qc") or {}
    if verdict.get("status") in (
        "extra", "missing", "inaudible", "duplicate", "repeated",
        "timing_mismatch", "audio_level",
    ):
        raise HTTPException(422, "Paroles incorrectes ou audio absent: corrigez cette prise.")
    current = _state(conn, shot["project_id"])
    entry = next(s for s in current["shots"] if s["id"] == shot["id"])
    review = metadata.get("director_qc_review") or {}
    if (
        not current["approved"]
        or not review.get("approved")
        or review.get("shot_hash") != entry["hash"]
        or review.get("file_hash") != file_hash(take["output_path"])
        or review.get("trim") != [shot.get("trim_start_sec"), shot.get("trim_end_sec")]
    ):
        raise HTTPException(422, "QC humain requis: paroles, locuteurs, image, texte et raccords.")


def review_take(project_id, take_id, payload):
    from calliope.studio import series

    series.require_editable(project_id)
    with service.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = require_hash(conn, project_id, payload["expected_hash"], approved=True)
        take = conn.execute(
            "SELECT * FROM studio_takes WHERE id=? AND project_id=? AND kind=?",
            (take_id, project_id, "video"),
        ).fetchone()
        if not take or take["status"] != "done" or not Path(take["output_path"] or "").is_file():
            raise HTTPException(404, "Prise terminee introuvable.")
        shot = service.public_shot(conn, take["clip_id"])
        if shot["selected_video_id"] != take_id:
            raise HTTPException(409, "La prise selectionnee a change.")
        if payload["approved"] and not all(
            payload[k] for k in ("visual_ok", "continuity_ok", "speakers_ok", "audio_ok")
        ):
            raise HTTPException(
                422, "Ecoutez et verifiez toutes les dimensions du QC avant validation."
            )
        metadata = json.loads(take["metadata_json"])
        if payload["approved"] and (metadata.get("dialogue_qc") or {}).get("status") in (
            "extra",
            "missing",
            "inaudible",
            "duplicate",
            "repeated",
            "timing_mismatch",
            "audio_level",
        ):
            raise HTTPException(
                422, "Cette prise contient un defaut de parole detecte. Reprenez ce plan."
            )
        entry = next(s for s in current["shots"] if s["id"] == shot["id"])
        metadata["director_qc_review"] = payload | {
            "shot_hash": entry["hash"],
            "file_hash": file_hash(take["output_path"]),
            "trim": [shot.get("trim_start_sec"), shot.get("trim_end_sec")],
            "at": service.utc_now(),
        }
        conn.execute(
            "UPDATE studio_takes SET metadata_json=? WHERE id=?", (json.dumps(metadata), take_id)
        )
        conn.execute("UPDATE studio_shots SET approved=0 WHERE clip_id=?", (shot["id"],))
        conn.commit()
    if payload["approved"]:
        service.review_take_visuals(take_id, True)
        service.approve_shot(shot["id"], True)
    return service.board(project_id)
