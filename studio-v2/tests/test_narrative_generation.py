"""Contrat de génération et transport HTTP, sans LLM ni GPU réels."""

import json
import sys
from pathlib import Path

import httpx
import pytest

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

import llm.deepseek as ds
from core.config import V2Settings
from core.ltx_prompt import validate_ltx_prompt
from core.scenario import Project


@pytest.fixture
def draft():
    return {
        "title": "La proposition",
        "characters": [
            {"id": "fraise", "name": "Fraise", "description": "A small strawberry in pajamas."},
            {"id": "el_diablo", "name": "El Diablo", "description": "A dark figure with red eyes."},
        ],
        "shots": [
            {
                "id": "shot_1", "duration_s": 3.0,
                "action": "El Diablo emerges from the mirror. Fraise recoils.",
                "initial_state": "Fraise sits beside a mirror.",
                "final_state": "Fraise leans back. El Diablo faces her.",
                "continue_from_previous": False,
                "camera_setup": "bedroom-fixed-medium",
                "dialogue": [{"speaker_id": "fraise", "text": "Vous êtes qui ?", "start_sec": 0.4}],
            },
            {
                "id": "shot_2", "duration_s": 6.0,
                "action": "El Diablo extends his hand. Fraise refuses it.",
                "initial_state": "Fraise leans back. El Diablo faces her.",
                "final_state": "His hand remains extended. Fraise folds her arms.",
                "continue_from_previous": True,
                "camera_setup": "bedroom-fixed-medium",
                "dialogue": [{"speaker_id": "el_diablo", "text": "Donne-moi ta main.", "start_sec": 0.3}],
            },
        ],
    }


def test_story_direction_precedes_shot_contract():
    prompt = ds.SYSTEM_PROMPT
    assert "CONSTRUIS L'HISTOIRE AVANT LES PLANS" in prompt
    assert prompt.index("CONSTRUIS L'HISTOIRE AVANT LES PLANS") < prompt.index("SCHÉMA JSON")
    assert "VÉRIFIE AVANT DE RÉPONDRE" in prompt
    assert "recopie à l'identique" in prompt
    assert "noms et identités" in prompt
    assert "initial_state" in prompt and "final_state" in prompt
    assert "cliffhanger net sur le dernier plan" not in prompt


@pytest.mark.parametrize("name,genre", [("El Diablo", "fantastique"), ("Madame Prune", "comédie")])
def test_http_single_call_preserves_brief_and_json_roundtrip(monkeypatch, draft, name, genre):
    draft = json.loads(json.dumps(draft).replace("El Diablo", name))
    idea = f"Genre : {genre}. Fraise rencontre {name}. Conserver ces noms et identités."
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {
                "reasoning_content": "Préparation interne distincte du scénario.",
                "content": json.dumps(draft),
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        })

    client_class = httpx.Client
    monkeypatch.setattr(ds.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    project, meta = ds.generate_scenario_once(
        settings=V2Settings(deepseek_api_key="test"), idea=idea,
        target_duration_s=9, project_id="identity-test",
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    body = json.loads(request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 16384
    assert len(body["messages"]) == 2
    assert idea in body["messages"][1]["content"]
    if name != "El Diablo":
        assert "El Diablo" not in body["messages"][0]["content"]
    guides, revision = ds.load_creative_guides()
    system = body["messages"][0]["content"]
    assert guides in system
    assert system.index(guides) < system.index(ds.SYSTEM_PROMPT)
    assert system.endswith(ds.SYSTEM_PROMPT)
    assert meta["creative_guides_version"] == revision
    assert project.id == "identity-test" and project.idea == idea
    assert [c.name for c in project.characters] == ["Fraise", name]
    assert [s.duration_s for s in project.shots] == [3.0, 6.0]
    assert project.shots[0].final_state == project.shots[1].initial_state
    assert Project.model_validate_json(project.model_dump_json()) == project
    assert "Préparation interne" not in project.model_dump_json()
    for shot, expected in zip(project.shots, draft["shots"]):
        assert shot.dialogue[0].model_dump() == {**expected["dialogue"][0], "delivery": "on_screen"}
        assert validate_ltx_prompt(shot.ltx_prompt)["ok"]
    assert meta["warnings"] == []


def test_custom_endpoint_keeps_existing_request_contract(draft):
    requests = []

    def respond(body):
        requests.append(body)
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    ds.generate_scenario_once(
        settings=V2Settings(deepseek_base_url="https://llm.example.test/v1"),
        idea="Une rencontre.", transport_fn=respond,
    )
    assert len(requests) == 1
    assert "thinking" not in requests[0]
    assert "reasoning_effort" not in requests[0]
    assert requests[0]["max_tokens"] == 3000


def test_same_call_prepares_recurring_decors_and_shot_associations(draft):
    draft["decors"] = [{"id": "chambre", "name": "Chambre", "description": "Stylized bedroom, oak vanity, round mirror, blue walls"}]
    for shot in draft["shots"]:
        shot["decor_id"] = "chambre"
    requests = []

    def respond(body):
        requests.append(body)
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    project, _ = ds.generate_scenario_once(
        settings=V2Settings(), idea="Un pacte dans une chambre.", transport_fn=respond,
    )
    assert len(requests) == 1
    assert project.decors[0].name == "Chambre"
    assert project.decors[0].asset_id is None
    assert [shot.decor_id for shot in project.shots] == ["chambre", "chambre"]
    assert '"decors"' in requests[0]["messages"][0]["content"]
    assert '"decor_id"' in requests[0]["messages"][0]["content"]
    assert Project.model_validate_json(project.model_dump_json()) == project


@pytest.mark.parametrize("failure", ["json", "schema", "speaker", "timing", "length", "http", "timeout"])
def test_http_errors_never_trigger_rewrite_or_retry(monkeypatch, draft, failure):
    requests = []
    if failure == "schema":
        draft["shots"][0]["duration_s"] = 20
    elif failure == "speaker":
        draft["shots"][0]["dialogue"][0]["speaker_id"] = "unknown"
    elif failure == "timing":
        draft["shots"][0]["dialogue"][0]["start_sec"] = 3.0

    def respond(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        if failure == "http":
            return httpx.Response(503, text="Unavailable")
        content = "{" if failure == "json" else json.dumps(draft)
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "length" if failure == "length" else "stop",
            "message": {"content": content},
        }]})

    client_class = httpx.Client
    monkeypatch.setattr(ds.httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    with pytest.raises(ds.ScenarioGenerationError, match="tronquée" if failure == "length" else None):
        ds.generate_scenario_once(settings=V2Settings(deepseek_api_key="test"), idea="Un pacte.")
    assert len(requests) == 1


@pytest.mark.parametrize("missing", [False, True])
def test_unavailable_guide_stops_before_transport(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(ds, "_CREATIVE_GUIDES_DIR", tmp_path)
    for name in ds._CREATIVE_GUIDE_FILES:
        (tmp_path / name).write_text("Guide local", encoding="utf-8")
    path = tmp_path / ds._CREATIVE_GUIDE_FILES[1]
    if missing:
        path.unlink()
    else:
        path.write_text("  ", encoding="utf-8")

    def forbidden_transport(request):
        pytest.fail("Un guide absent ou vide doit bloquer avant l'appel DeepSeek")

    with pytest.raises(ds.ScenarioGenerationError, match="Bible créative"):
        ds.generate_scenario_once(
            settings=V2Settings(), idea="Un pacte.", transport_fn=forbidden_transport,
        )


def test_same_call_prepares_independent_image_prompts_without_changing_video_story(draft):
    from core.ltx_prompt import compile_shot_prompt

    image_prompt = "Vertical 9:16 still, t=0. A red strawberry in pajamas waits by a bedroom mirror. No text."
    draft["shots"][0]["image_prompt"] = image_prompt
    calls = []

    def respond(body):
        calls.append(body)
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    project, _ = ds.generate_scenario_once(settings=V2Settings(), idea="Une rencontre.", transport_fn=respond)
    assert len(calls) == 1
    assert project.shots[0].image_prompt == image_prompt
    assert project.shots[1].image_prompt == ""
    assert '"image_prompt"' in calls[0]["messages"][0]["content"]
    original_video_prompt = compile_shot_prompt(project, project.shots[0])
    project.shots[0].image_prompt = "Another non-graphic still."
    assert compile_shot_prompt(project, project.shots[0]) == original_video_prompt
    for shot, expected in zip(project.shots, draft["shots"]):
        assert shot.action == expected["action"]
        assert shot.initial_state == expected["initial_state"]
        assert shot.final_state == expected["final_state"]
        assert [line.model_dump() for line in shot.dialogue] == [
            {**line, "delivery": "on_screen"} for line in expected["dialogue"]
        ]
    assert Project.model_validate_json(project.model_dump_json()) == project
