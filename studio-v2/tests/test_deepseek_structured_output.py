import json
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.app import create_app
from core.config import V2Settings
from core.scenario import Project
from core.storage import StudioRepository
from llm import deepseek


def test_official_request_enforces_the_director_schema_before_local_validation(monkeypatch):
    draft = {
        "title": "Frigo", "characters": [{"id": "benny", "name": "Benny", "description": "Yellow banana."}],
        "decors": [{"id": "frigo", "name": "Frigo", "description": "White shelves in blue light."}],
        "shots": [{"id": "shot_1", "duration_s": 3, "action": "Benny recoils.",
                   "initial_state": "Benny faces the fridge.", "final_state": "Benny steps back.",
                   "decor_id": "frigo"}],
    }
    calls = []

    def respond(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/responses"
        body = json.loads(request.content)
        assert body["reasoning"] == {"effort": "high"}
        assert body["max_output_tokens"] == 65536
        assert "MISE EN SCÈNE" in body["input"][0]["content"]
        assert body["text"]["format"]["type"] == "json_schema"
        schema = body["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        decor = schema["$defs"]["Decor"]
        assert decor["additionalProperties"] is False
        assert set(decor["properties"]) == {"id", "name", "description"}
        assert not {"character_ids", "no_characters", "style", "lighting"} & decor["properties"].keys()
        assert "image_ref" not in schema["$defs"]["Character"]["properties"]
        assert "rendered_video_ref" not in schema["$defs"]["Shot"]["properties"]
        return httpx.Response(200, json={
            "object": "response", "status": "completed", "model": "deepseek-flash",
            "output": [
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "Internal preparation."}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(draft)}]},
            ],
            "usage": {"input_tokens": 123, "output_tokens": 456},
        })

    client_class = httpx.Client
    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kw: client_class(transport=httpx.MockTransport(respond), **kw))
    project, meta = deepseek.generate_scenario_once(
        settings=V2Settings(deepseek_api_key="test"), idea="Benny fuit un humain devant le frigo.",
    )
    assert len(calls) == 1
    assert project.decors[0].description == "White shelves in blue light."
    assert project.shots[0].decor_id == "frigo"
    assert project.shots[0].action == "Benny recoils."
    assert "Internal preparation" not in project.model_dump_json()
    assert meta["prompt_tokens"] == 123 and meta["completion_tokens"] == 456


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
    {"status": "failed", "error": {"code": "failure", "message": "Provider failure"}, "output": []},
    {"status": "completed", "output": [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": "Not a storyboard"}]}]},
])
def test_incomplete_failed_or_missing_output_never_retries(monkeypatch, response):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/responses"
        return httpx.Response(200, json=response)

    client_class = httpx.Client
    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kw: client_class(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(deepseek.ScenarioGenerationError):
        deepseek.generate_scenario_once(settings=V2Settings(deepseek_api_key="test"), idea="Un frigo.")
    assert len(requests) == 1


def test_failed_paid_response_is_kept_in_full_for_local_diagnosis(tmp_path):
    raw = json.dumps({"characters": [], "shots": [], "diagnostic": "x" * 3000})
    app = create_app(
        settings=V2Settings(data_dir=tmp_path / "data"),
        llm_transport=lambda request: {"choices": [{"message": {"content": raw}}]},
    )
    original = Project(id="frigo", idea="Benny fuit le frigo.")
    app.state.repo.save_project(original)
    with TestClient(app) as client:
        response = client.post("/api/projects/frigo/generate-scenario", json={})
    assert response.status_code == 422
    with sqlite3.connect(app.state.settings.db_path) as conn:
        saved = conn.execute("select raw_content from llm_runs where project_id='frigo'").fetchone()[0]
    assert saved == raw and len(saved) > 2000
    assert app.state.repo.get_project("frigo") == original


def test_existing_llm_history_survives_raw_response_column_migration(tmp_path):
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as conn:
        conn.execute("""create table llm_runs (
            id text primary key, project_id text, model text, status text,
            prompt_tokens integer, completion_tokens integer, estimated_cost_eur real,
            error_message text, created_at text)""")
        conn.execute("insert into llm_runs values ('old', 'frigo', 'deepseek-flash', 'failed', 0, 0, null, 'old error', '2026-10-22')")
    StudioRepository(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("select id,error_message,raw_content from llm_runs").fetchall() == [("old", "old error", None)]
