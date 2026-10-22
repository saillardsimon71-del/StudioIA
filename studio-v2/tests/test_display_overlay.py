import json
import subprocess
import sys
from pathlib import Path

import cv2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.app import create_app
from core.config import V2Settings
from core.export import write_fixed_overlay_ass
from core.scenario import DisplayOverlay, Project
from core.storage import StudioRepository
from qc.media import PROCESS_FLAGS, ffmpeg_exe


def test_twentieth_shot_with_delayed_text_is_saved_without_another_llm_call(tmp_path):
    calls = []
    draft = {
        "title": "Benny et Frida",
        "characters": [{"id": "benny", "name": "Benny", "description": "Yellow banana with expressive eyes."}],
        "shots": [{
            "id": f"shot_{index + 1}",
            "duration_s": 3,
            "action": "Benny watches the blank counter.",
            "initial_state": "Benny faces the counter.",
            "final_state": "Benny remains beside the counter.",
            "camera_setup": "Medium shot of Benny and counter",
        } for index in range(20)],
    }
    draft["shots"][19]["display_overlay"] = {
        "initial_text": "", "changes": [{"at_sec": 1, "text": "10"}],
    }

    def respond(request):
        calls.append(request)
        return {"choices": [{"message": {"content": json.dumps(draft)}}]}

    settings = V2Settings(data_dir=tmp_path / "data")
    app = create_app(settings=settings, llm_transport=respond)
    app.state.repo.save_project(Project(id="frigo", idea="Un compteur apparaît après une seconde.", target_duration_s=60))
    with TestClient(app) as client:
        response = client.post("/api/projects/frigo/generate-scenario", json={})
        assert response.status_code == 200, response.text
        assert len(calls) == 1
        saved = client.get("/api/projects/frigo").json()
    assert len(saved["shots"]) == 20
    assert sum(shot["duration_s"] for shot in saved["shots"]) == 60
    overlay = saved["shots"][19]["display_overlay"]
    assert overlay["initial_text"] == ""
    assert overlay["changes"] == [{"at_sec": 1, "text": "10"}]
    reloaded = StudioRepository(settings.db_path).get_project("frigo")
    assert reloaded.shots[19].display_overlay.initial_text == ""
    assert reloaded.shots[19].display_overlay.changes[0].text == "10"


def test_export_keeps_blank_intervals_before_and_after_timed_text(tmp_path):
    overlay = DisplayOverlay(
        initial_text="", x=0.5, y=0.5, font_size=0.15, color="FF0000",
        changes=[{"at_sec": 1, "text": "10"}, {"at_sec": 2, "text": ""}],
    )
    write_fixed_overlay_ass(overlay, 3, 640, 360, tmp_path / "overlay.ass")
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-f", "lavfi", "-i", "color=black:s=640x360:r=24:d=3",
         "-vf", "ass=filename=overlay.ass", "-an", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "timed.mp4"],
        cwd=tmp_path, capture_output=True, timeout=30, **PROCESS_FLAGS,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    cap = cv2.VideoCapture(str(tmp_path / "timed.mp4"))
    maxima = []
    try:
        for frame_number in (12, 36, 60):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = cap.read()
            assert ok
            maxima.append(int(frame.max()))
    finally:
        cap.release()
    assert maxima[0] < 5
    assert maxima[1] > 100
    assert maxima[2] < 5


@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
def test_export_renders_initial_and_changed_multiline_text(tmp_path, line_break):
    overlay = DisplayOverlay(
        initial_text=f"HAUT{line_break}BAS", x=0.5, y=0.5, font_size=0.12,
        color="FFFFFF", box_bg=None, changes=[{"at_sec": 1, "text": f"FIN{line_break}DEUX"}],
    )
    write_fixed_overlay_ass(overlay, 2, 640, 360, tmp_path / "overlay.ass")
    events = [line for line in (tmp_path / "overlay.ass").read_text(encoding="utf-8").splitlines()
              if line.startswith("Dialogue:")]
    assert len(events) == 2 and all(r"\N" in line for line in events)
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-f", "lavfi", "-i", "color=black:s=640x360:r=24:d=2",
         "-vf", "ass=filename=overlay.ass", "-an", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "multiline.mp4"],
        cwd=tmp_path, capture_output=True, timeout=30, **PROCESS_FLAGS,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    cap = cv2.VideoCapture(str(tmp_path / "multiline.mp4"))
    try:
        for frame_number in (12, 36):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = cap.read()
            assert ok
            rows = (frame.min(axis=2) > 180).any(axis=1)
            starts = sum(bool(row) and (i == 0 or not rows[i - 1]) for i, row in enumerate(rows))
            assert starts == 2
    finally:
        cap.release()


@pytest.mark.parametrize("overlay", [
    {"initial_text": None},
    {"initial_text": "x" * 81},
    {"initial_text": "", "x": 2},
    {"initial_text": "", "changes": [{"at_sec": 0, "text": "10"}]},
    {"initial_text": "", "changes": [{"at_sec": 2, "text": "10"}, {"at_sec": 1, "text": "9"}]},
])
def test_blank_text_does_not_relax_other_overlay_constraints(overlay):
    with pytest.raises(ValidationError):
        DisplayOverlay.model_validate(overlay)
