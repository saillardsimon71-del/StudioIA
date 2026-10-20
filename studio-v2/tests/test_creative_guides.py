"""Vérification locale de la bible créative, sans appel DeepSeek ni GPU."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

import llm.deepseek as ds  # noqa: E402
from core.config import V2Settings  # noqa: E402


def test_three_creative_guides_loaded_in_order():
    content, revision = ds.load_creative_guides()
    assert len(revision) == 12
    assert content.count("### 01_attention.md") == 1
    assert content.count("### 02_retention.md") == 1
    assert content.count("### 03_narration.md") == 1
    assert content.index("### 01_attention.md") < content.index("### 02_retention.md")
    assert content.index("### 02_retention.md") < content.index("### 03_narration.md")


def test_single_deepseek_call_includes_all_guides_and_revision():
    requests: list[dict] = []

    def fake_transport(req: dict) -> dict:
        requests.append(req)
        return {
            "model": "deepseek-chat",
            "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "title": "Le test",
                        "characters": [{
                            "id": "fraise",
                            "name": "Fraise",
                            "description": "A shy anthropomorphic strawberry wearing a dark hoodie.",
                        }],
                        "shots": [{
                            "id": "shot_1",
                            "duration_s": 5,
                            "action": "Fraise discovers a mysterious red glow.",
                            "initial_state": "Fraise stands near a dim mirror.",
                            "final_state": "Fraise steps back, frightened.",
                            "continue_from_previous": False,
                            "dialogue": [{
                                "speaker_id": "fraise",
                                "text": "C'est quoi, ça ?",
                                "start_sec": 0.5,
                            }],
                        }],
                    }, ensure_ascii=False)
                }
            }],
        }

    project, meta = ds.generate_scenario_once(
        settings=V2Settings(deepseek_api_key="unused"),
        idea="Une fraise découvre un miroir maudit.",
        target_duration_s=5,
        transport_fn=fake_transport,
    )
    assert project.shots[0].id == "shot_1"
    assert len(requests) == 1
    system_message = requests[0]["messages"][0]["content"]
    assert "01_attention.md" in system_message
    assert "02_retention.md" in system_message
    assert "03_narration.md" in system_message
    assert meta["creative_guides_version"] == ds.load_creative_guides()[1]


def test_edits_are_reloaded_and_missing_guide_fails_clearly(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ds, "_CREATIVE_GUIDES_DIR", tmp_path)
    for name in ds._CREATIVE_GUIDE_FILES:
        (tmp_path / name).write_text(f"Texte de {name}", encoding="utf-8")

    before, version_before = ds.load_creative_guides()
    (tmp_path / ds._CREATIVE_GUIDE_FILES[0]).write_text("Nouveau hook", encoding="utf-8")
    after, version_after = ds.load_creative_guides()
    assert before != after
    assert version_before != version_after

    (tmp_path / ds._CREATIVE_GUIDE_FILES[2]).unlink()
    with pytest.raises(ds.ScenarioGenerationError, match="Bible créative indisponible"):
        ds.load_creative_guides()
