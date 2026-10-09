import asyncio

import pytest

from calliope.studio import export, series_planner
from tests.test_studio_export import media as media
from tests.test_studio_qualification import context, exchange


def test_exact_text_is_data_and_generated_prompts_request_a_blank_panel():
    shot = exchange()
    shot["screen_text"] = [{"key": "score", "text": "12,349", "x": 0.5, "y": 0.12,
                            "entity_key": "mango"}]
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    normalized = plan["scenes"][0]["shots"][0]
    assert normalized["screen_text"][0]["text"] == "12,349"
    assert "blank" in normalized["image_prompt"].lower()
    assert "12,349" not in normalized["image_prompt"]


def test_text_overlay_survives_real_export_and_is_persisted_in_metadata(tmp_path, media):
    source = media[0]
    labels = [{"key": "score", "text": "12,349", "x": 0.5, "y": 0.2}]
    result = asyncio.run(export.assemble(
        {"aspect_ratio": "9:16",
         "clips": [{"path": str(source), "audio_mode": "native", "screen_text": labels}]},
        tmp_path / "labeled.mp4",
    ))
    assert result["screen_text"][0]["labels"][0]["text"] == "12,349"
    assert export.inspect_media(tmp_path / "labeled.mp4")["has_audio"]
    assert result["screen_text"][0]["tracking"] == "screen"
    import io
    import subprocess

    from PIL import Image

    frame = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(tmp_path / "labeled.mp4"),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"
    ])
    image = Image.open(io.BytesIO(frame))
    pixels = image.crop((250, 485, 470, 555)).convert("RGB").get_flattened_data()
    assert sum(min(pixel) > 180 for pixel in pixels) > 100


def test_unmotivated_score_change_is_not_accepted():
    first = exchange()
    first["screen_text"] = [{"key": "score", "text": "0", "x": 0.5, "y": 0.1}]
    second = exchange()
    second["screen_text"] = [{"key": "score", "text": "100", "x": 0.5, "y": 0.1}]
    with pytest.raises(ValueError, match="sans événement"):
        series_planner.normalize_plan({"scenes": [{"shots": [first, second]}]}, context())


def test_numeric_values_share_the_series_format_instead_of_llm_punctuation():
    shot = exchange()
    shot["screen_text"] = [
        {"key": "a", "value": 12349, "text": "12:3:49", "x": 0.25, "y": 0.1},
        {"key": "b", "value": 2345, "text": "2;3;45", "x": 0.75, "y": 0.1},
    ]
    config = context() | {"directing_profile": {"number_format": "grouped_comma"}}
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, config)
    assert [label["text"] for label in plan["scenes"][0]["shots"][0]["screen_text"]] == [
        "12,349", "2,345"
    ]


def test_character_tracking_follows_a_moving_textured_subject(tmp_path):
    import cv2
    import numpy as np

    from calliope.studio.screen_text import tracked_positions

    path = tmp_path / "move.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 24, (320, 240))
    rng = np.random.default_rng(7)
    texture = rng.integers(40, 240, (60, 60, 3), dtype=np.uint8)
    for index in range(24):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[80:140, 80 + index:140 + index] = texture
        writer.write(frame)
    writer.release()
    points = tracked_positions(path, {"x": 110 / 320, "y": 50 / 240,
                                     "anchor_box": [80 / 320, 80 / 240, 60 / 320, 60 / 240]})
    assert points[-1][1] - points[0][1] == pytest.approx(23 / 320, abs=0.01)
    assert points[-1][2] == pytest.approx(points[0][2], abs=0.01)
