import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.app import create_app
from core.config import V2Settings
from core.scenario import Project


@pytest.mark.parametrize("change,keep_portrait,keep_decor", [
    (None, True, True),
    ("appearance", False, True),
    ("decor", True, False),
    ("style", False, False),
    ("voice", True, True),
    ("late_selection", True, True),
])
def test_regeneration_keeps_only_unchanged_visual_references(tmp_path, change, keep_portrait, keep_decor):
    original = Project.model_validate({
        "id": "cafe", "idea": "Maya décide de rester.", "target_duration_s": 4,
        "visual_style": "Naturalistic cinematic realism",
        "characters": [{"id": "maya", "name": "Maya", "description": "Woman with dark curls and a blue coat.",
                        "voice_hint": "warm low", "image_ref": "selected-portrait.png"}],
        "decors": [{"id": "cafe", "name": "Café", "description": "Wooden table beside a rain-streaked window.",
                    "asset_id": "selected-decor"}],
        "shots": [{"id": "shot_1", "duration_s": 4, "decor_id": "cafe",
                   "camera_setup": "Medium shot facing the cafe window",
                   "initial_state": "Maya holds an upright cup at the table.",
                   "action": "Maya lifts the cup and looks outside.",
                   "final_state": "Maya holds the cup near her lips.",
                   "image_ref": "old-shot.png", "rendered_video_ref": "old-take.mp4"}],
    })
    draft = {
        "title": "La décision", "narration_mode": "dialogue", "visual_style": original.visual_style,
        "characters": [c.model_dump(include={"id", "name", "description", "voice_hint"}) for c in original.characters],
        "decors": [d.model_dump(include={"id", "name", "description"}) for d in original.decors],
        "shots": [{"id": "shot_1", "duration_s": 4, "decor_id": "cafe",
                   "camera_setup": "Medium shot facing the cafe window",
                   "initial_state": "Maya holds an upright cup at the table.",
                   "action": "Maya puts down the cup and rests her empty hands on the table.",
                   "final_state": "Maya rests her empty hands beside the cup.", "dialogue": []}],
    }
    if change == "appearance":
        draft["characters"][0]["description"] = "Woman with dark curls and a yellow coat."
    elif change == "decor":
        draft["decors"][0]["description"] = "Metal table on a sunlit terrace."
    elif change == "style":
        draft["visual_style"] = "Hand-drawn watercolor animation"
    elif change == "voice":
        draft["characters"][0]["voice_hint"] = "bright high"
    calls = []

    def transport(body):
        calls.append(body)
        if change == "late_selection":
            current = app.state.repo.get_project("cafe")
            current.characters[0].image_ref = "latest-portrait.png"
            current.decors[0].asset_id = "latest-decor"
            app.state.repo.save_project(current)
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    app = create_app(settings=V2Settings(data_dir=tmp_path / "data"), llm_transport=transport)
    app.state.repo.save_project(original)
    with TestClient(app) as client:
        response = client.post("/api/projects/cafe/generate-scenario", json={})
        assert response.status_code == 200, response.text
        project = response.json()["project"]
        assert len(calls) == 1
        context = json.loads(calls[0]["messages"][1]["content"].splitlines()[-1])
        assert context["visual_style"] == original.visual_style
        assert context["characters"][0]["description"] == original.characters[0].description
        assert context["decors"][0]["description"] == original.decors[0].description
        assert "shots" not in context
        assert "selected-portrait.png" not in json.dumps(calls)
        assert "selected-decor" not in json.dumps(calls)
        portrait = "latest-portrait.png" if change == "late_selection" else "selected-portrait.png"
        decor = "latest-decor" if change == "late_selection" else "selected-decor"
        assert project["characters"][0]["image_ref"] == (portrait if keep_portrait else None)
        assert project["decors"][0]["asset_id"] == (decor if keep_decor else None)
        assert project["visual_style"] == draft["visual_style"]
        assert project["characters"][0]["description"] == draft["characters"][0]["description"]
        assert project["characters"][0]["voice_hint"] == draft["characters"][0]["voice_hint"]
        assert project["shots"][0]["action"] == draft["shots"][0]["action"]
        assert project["shots"][0]["image_ref"] is None
        assert project["shots"][0]["rendered_video_ref"] is None
        assert client.get("/api/projects/cafe").json() == project
