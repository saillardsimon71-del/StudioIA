import copy
import importlib
import json

import pytest


def compiler():
    return importlib.import_module("calliope.studio.h3_prompt").compile_h3_prompt


def test_dialogue_is_verbatim_attributed_and_never_duplicated():
    shot = {
        "prompt": "A kitchen. <d>Old words</d>",
        "dialogue": "Tu as vendu notre canapé ? Et la table ?",
        "speaker": "lila",
        "action": "She points at the empty room.",
        "reaction": "She folds her arms.",
        "framing": "A full-body two-shot.",
        "camera": "Static camera.",
    }
    context = {
        "language": "fr",
        "entities": [
            {"key": "lila", "name": "Lila", "description": "A masked adult in a blue coat."}
        ],
    }
    original = copy.deepcopy(shot)
    result = compiler()(shot, context, first_frame=True)
    assert result.startswith("For the target video, at 0.00 seconds")
    assert "<Picture 1>" in result
    assert "integrated_multimodal_description: [Shot 1]" in result
    assert "Lila" in result and "A masked adult in a blue coat." in result and "(S1)" in result
    assert "<d>[French] Tu as vendu notre canapé ? Et la table ?</d>" in result
    assert result.count("<d>") == 1 and "Old words" not in result
    assert "A full-body two-shot." in result and "She folds her arms." in result
    assert "mouth visible" not in result and "lip movement" not in result
    assert "overall_soundscape:" in result and "non_diegetic_music: N/A" in result
    assert shot == original


def test_silent_text_generation_has_no_image_or_speech_claim():
    result = compiler()({"prompt": "A quiet garden.", "dialogue": ""})
    assert "Picture" not in result and "<d>" not in result and "(S1)" not in result
    assert "overall_soundscape: N/A" in result


def test_legacy_name_prefix_is_outside_spoken_text_without_mutating_shot():
    shot = {"prompt": "Lila stands in the room.", "dialogue": "Lila: Bonjour, toi !"}
    result = compiler()(shot, {"language": "French"})
    assert "Lila (S1)" in result
    assert "<d>[French] Bonjour, toi !</d>" in result
    assert shot["dialogue"] == "Lila: Bonjour, toi !"


@pytest.mark.parametrize(
    "language,label",
    [
        ("fr-FR", "French"),
        ("de", "German"),
        ("zh", "Chinese"),
        ("ar", "Arabic"),
        ("es", "Spanish"),
        ("ja", "Japanese"),
    ],
)
def test_supported_language_labels(language, label):
    assert f"<d>[{label}]" in compiler()({"dialogue": "Hello."}, {"language": language})


def test_unsupported_language_is_explicit():
    with pytest.raises(ValueError, match="Langue.*non configurée dans StudioIA"):
        compiler()({"dialogue": "Hello."}, {"language": "xx"})


def test_explicit_shot_language_overrides_the_series_language():
    prompt = compiler()(
        {"dialogue": "Want to make videos too?", "language": "en"}, {"language": "fr"}
    )
    assert "<d>[English] Want to make videos too?</d>" in prompt


def test_profile_is_validated_and_frozen_for_episode(client, monkeypatch):
    from calliope.queue.manager import queue_manager
    from calliope.studio import series, service

    monkeypatch.setattr(series, "require_services", lambda: None)
    monkeypatch.setattr(queue_manager, "paused", True)
    created = client.post("/api/studio/series", json={"title": "X", "concept": "Y"}).json()
    assert created["directing_profile"]["preset"] == "generalist"
    updated = client.patch(
        f"/api/studio/series/{created['id']}",
        json={"language": "fr", "directing_profile": {"preset": "fast_dialogue_social_fiction"}},
    )
    assert updated.status_code == 200
    assert updated.json()["directing_profile"]["dialogue_density"] == "brisk"
    project = client.post(
        f"/api/studio/series/{created['id']}/episodes",
        json={"idea": "A misunderstanding", "request_id": "one"},
    ).json()["project_id"]
    client.patch(
        f"/api/studio/series/{created['id']}",
        json={"language": "en", "directing_profile": {"preset": "generalist"}},
    ).raise_for_status()
    with service.database() as conn:
        frozen = json.loads(
            conn.execute(
                "SELECT context_json FROM studio_episodes WHERE project_id=?", (project,)
            ).fetchone()[0]
        )
    assert frozen["language"] == "fr"
    assert frozen["directing_profile"]["dialogue_density"] == "brisk"
    invalid = client.patch(
        f"/api/studio/series/{created['id']}",
        json={"directing_profile": {"camera": "teleport", "fruit": "banana"}},
    )
    assert invalid.status_code == 422


def test_migration_adds_columns_to_existing_database_idempotently(tmp_path):
    import sqlite3

    from calliope.studio.schema import migrate_studio

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE studio_series (id INTEGER PRIMARY KEY); "
            "CREATE TABLE studio_takes (id INTEGER PRIMARY KEY, clip_id INTEGER);"
        )
    migrate_studio(path)
    migrate_studio(path)
    with sqlite3.connect(path) as conn:
        series_columns = {row[1]: row for row in conn.execute("PRAGMA table_info(studio_series)")}
        take_columns = {row[1]: row for row in conn.execute("PRAGMA table_info(studio_takes)")}
    assert series_columns["directing_profile_json"][3:5] == (1, "'{}'")
    assert take_columns["render_state_json"][3:5] == (1, "'{}'")


def test_planner_keeps_masked_speaker_and_optional_direction_without_lip_rewrite():
    from calliope.studio.series_planner import normalize_plan

    shot = {
        "prompt": "A masked figure gestures. <d>duplicated</d>",
        "image_prompt": "A masked figure in a full-body two-shot.",
        "dialogue": "Yes. And I kept the key.",
        "speaker": "figure",
        "character_keys": ["figure"],
        "duration_sec": 1.6,
        "action": "He hands over the key.",
        "reaction": "He nods.",
        "framing": "Full-body two-shot.",
        "camera": "Static.",
    }
    result = normalize_plan({"scenes": [{"shots": [shot]}]}, {"entities": [{"key": "figure"}]})
    normalized = result["scenes"][0]["shots"][0]
    assert normalized["dialogue"] == "Yes. And I kept the key."
    assert normalized["action"] == "He hands over the key."
    assert normalized["prompt"] == "A masked figure gestures."


def test_planner_rejects_direction_objects_before_the_renderer():
    from calliope.studio.series_planner import normalize_plan

    plan = {
        "scenes": [
            {
                "shots": [
                    {
                        "prompt": "A room.",
                        "image_prompt": "A room.",
                        "duration_sec": 3,
                        "camera": {"invalid": True},
                    }
                ]
            }
        ]
    }
    with pytest.raises(ValueError, match="réalisation"):
        normalize_plan(plan, {})


def test_profile_without_music_overrides_planned_music():
    from calliope.studio.series_planner import normalize_plan

    plan = {
        "scenes": [
            {"shots": [{"prompt": "A room.", "image_prompt": "A room.", "duration_sec": 3}]}
        ],
        "audio": {"music": "tension"},
    }
    result = normalize_plan(plan, {"directing_profile": {"music": "none"}})
    assert result["audio"]["music"] == "none"


def test_legacy_structured_prompt_does_not_duplicate_sections_or_claim_an_absent_image():
    shot = {
        "prompt": "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
        "integrated_multimodal_description: [Shot 1] A garden.\n\n"
        "overall_soundscape: Loud music.\n\nnon_diegetic_music: Piano.",
        "dialogue": "",
    }
    result = compiler()(shot)
    assert "Picture" not in result and "fully referenced" not in result
    assert result.count("integrated_multimodal_description:") == 1
    assert result.count("overall_soundscape:") == 1
    assert result.count("non_diegetic_music:") == 1
    assert result.count("[Shot 1]") == 1
    assert "Loud music" not in result and "Piano" not in result


def test_legacy_prefix_requires_identified_name_and_keeps_colon_inside_speech():
    result = compiler()(
        {"prompt": "A clock ticks.", "dialogue": "Attention: il est midi."}, {"language": "fr"}
    )
    assert "<d>[French] Attention: il est midi.</d>" in result


def test_partial_profile_edit_preserves_other_choices(client):
    created = client.post(
        "/api/studio/series",
        json={
            "title": "X",
            "concept": "Y",
            "directing_profile": {"preset": "fast_dialogue_social_fiction", "camera": "static"},
        },
    ).json()
    updated = client.patch(
        f"/api/studio/series/{created['id']}", json={"directing_profile": {"music": "none"}}
    ).json()["directing_profile"]
    assert updated["preset"] == "fast_dialogue_social_fiction"
    assert updated["camera"] == "static" and updated["music"] == "none"


def test_frozen_canon_and_optional_voice_are_used_without_rewriting_speech():
    context = {
        "language": "fr",
        "entities": [
            {
                "key": "lila",
                "name": "Lila",
                "description": "An adult in a helmet.",
                "voice_description": "A low warm voice.",
            }
        ],
    }
    result = compiler()({"prompt": "She nods.", "speaker": "lila", "dialogue": "Oui."}, context)
    assert "An adult in a helmet." in result and "A low warm voice." in result
    assert "<d>[French] Oui.</d>" in result


def test_shot_and_scene_counts_follow_useful_story_duration():
    from calliope.studio.series_planner import normalize_plan

    shot = {"prompt": "She reacts.", "image_prompt": "A listener.", "duration_sec": 1.5}
    result = normalize_plan(
        {"scenes": [{"shots": [copy.deepcopy(shot) for _ in range(45)]}]}, {"target_seconds": 65}
    )
    assert len(result["scenes"][0]["shots"]) == 45
    result = normalize_plan(
        {"scenes": [{"shots": [copy.deepcopy(shot)]} for _ in range(25)]}, {"target_seconds": 38}
    )
    assert len(result["scenes"]) == 25


@pytest.mark.parametrize("field", ["action", "reaction", "framing", "camera"])
@pytest.mark.parametrize("dialogue", ["Oui.", ""])
def test_direction_speech_tags_are_rejected_before_native_render(field, dialogue):
    from calliope.studio.series_planner import normalize_plan

    shot = {
        "prompt": "She gestures.",
        "image_prompt": "A room.",
        "duration_sec": 3,
        "dialogue": dialogue,
        "speaker": "lila" if dialogue else "",
        "character_keys": ["lila"],
        field: "She says <d>[French] Encore.</d>",
    }
    context = {"language": "fr", "entities": [{"key": "lila", "name": "Lila"}]}
    original = copy.deepcopy(shot)
    with pytest.raises(ValueError, match="parole"):
        compiler()(shot, context)
    with pytest.raises(ValueError, match="parole"):
        normalize_plan({"scenes": [{"shots": [copy.deepcopy(shot)]}]}, context)
    assert shot == original


@pytest.mark.parametrize("field", ["name", "description", "voice_description", "visual_style"])
def test_canonical_prose_cannot_insert_speech_tags(field):
    context = {"language": "fr", "entities": [{"key": "lila", "name": "Lila"}], "bible": {}}
    target = context["bible"] if field == "visual_style" else context["entities"][0]
    target[field] = "<D>[French] Encore.</D>"
    with pytest.raises(ValueError, match="parole"):
        compiler()({"prompt": "She nods.", "speaker": "lila", "dialogue": "Oui."}, context)


@pytest.mark.parametrize(
    "context",
    [{"language": "fr", "entities": [{"key": "lila", "name": "Lila"}]}, {"language": "fr"}],
)
def test_legacy_uppercase_prefix_matches_identified_name_for_compiler_and_qc(context):
    from calliope.studio import h3_prompt

    shot = {"prompt": "Lila nods.", "dialogue": " LILA : Bonjour: à toi !"}
    if context.get("entities"):
        shot["speaker"] = "lila"
    spoken = h3_prompt.spoken_dialogue(shot, context)
    result = compiler()(shot, context)
    assert spoken == "Bonjour: à toi !"
    assert f"<d>[French] {spoken}</d>" in result
    assert shot["dialogue"] == " LILA : Bonjour: à toi !"


@pytest.mark.parametrize("mode", ["post", "silent"])
def test_non_native_modes_send_no_dialogue_and_qc_expects_no_speech(mode):
    from calliope.studio import h3_prompt

    shot = {"prompt": "Lila listens.", "dialogue": "Lila: Bonjour.", "audio_mode": mode}
    assert h3_prompt.spoken_dialogue(shot, {"language": "fr"}) == ""
    assert "<d>" not in compiler()(shot, {"language": "fr"})
