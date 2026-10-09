import asyncio

import pytest

from calliope.studio import export, series_planner
from tests.test_studio_export import media as media
from tests.test_studio_qualification import context, exchange


def test_exact_text_is_data_and_generated_prompts_request_a_blank_panel():
    shot = exchange()
    shot["screen_text"] = [
        {"key": "score", "text": "12,349", "x": 0.5, "y": 0.12, "entity_key": "mango"}
    ]
    plan = series_planner.normalize_plan({"scenes": [{"shots": [shot]}]}, context())
    normalized = plan["scenes"][0]["shots"][0]
    assert normalized["screen_text"][0]["text"] == "12,349"
    assert "blank" in normalized["image_prompt"].lower()
    assert "12,349" not in normalized["image_prompt"]


def test_text_overlay_survives_real_export_and_is_persisted_in_metadata(tmp_path, media):
    source = media[0]
    labels = [{"key": "score", "text": "12,349", "x": 0.5, "y": 0.2}]
    result = asyncio.run(
        export.assemble(
            {
                "aspect_ratio": "9:16",
                "clips": [{"path": str(source), "audio_mode": "native", "screen_text": labels}],
            },
            tmp_path / "labeled.mp4",
        )
    )
    assert result["screen_text"][0]["labels"][0]["text"] == "12,349"
    assert export.inspect_media(tmp_path / "labeled.mp4")["has_audio"]
    assert result["screen_text"][0]["tracking"] == "screen"
    import io
    import subprocess

    from PIL import Image

    frame = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(tmp_path / "labeled.mp4"),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
    )
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
        "12,349",
        "2,345",
    ]


def test_character_tracking_follows_a_moving_textured_subject(tmp_path):
    import cv2
    import numpy as np

    from calliope.studio.screen_text import bind_anchor, tracked_positions

    path = tmp_path / "move.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 24, (320, 240))
    rng = np.random.default_rng(7)
    texture = rng.integers(40, 240, (60, 60, 3), dtype=np.uint8)
    for index in range(24):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[80:140, 80 + index : 140 + index] = texture
        writer.write(frame)
    writer.release()
    label = {
        "key": "score",
        "entity_key": "fruit",
        "value": 1,
        "anchor": "character",
        "x": 110 / 320,
        "y": 50 / 240,
        "anchor_box": [80 / 320, 80 / 240, 60 / 320, 60 / 240],
    }
    label["anchor_binding"] = bind_anchor(path, label)
    points = tracked_positions(path, label)
    assert points[-1][1] - points[0][1] == pytest.approx(23 / 320, abs=0.01)
    assert points[-1][2] == pytest.approx(points[0][2], abs=0.01)


def moving_pair(tmp_path, *, swap=False, occlude=False, crossing=False):
    import cv2
    import numpy as np

    path = tmp_path / "pair.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 24, (320, 240))
    rng = np.random.default_rng(12)
    left = rng.integers(40, 240, (60, 60, 3), dtype=np.uint8)
    right = rng.integers(40, 240, (60, 60, 3), dtype=np.uint8)
    for index in range(24):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        distance = index * (4 if crossing else 1)
        frame[80:140, 50 + distance : 110 + distance] = right if swap and index >= 12 else left
        frame[80:140, 210 - distance : 270 - distance] = left if swap and index >= 12 else right
        if occlude and index >= 12:
            frame[75:145, 45 + index : 115 + index] = 0
        writer.write(frame)
    writer.release()
    return path


def pair_labels(path, *, frame_index=0):
    from calliope.studio.screen_text import bind_anchor

    labels = []
    for key, value, left in [("noah", 1, 50 + frame_index), ("leo", 500, 210 - frame_index)]:
        label = {
            "key": key + "-value",
            "entity_key": key,
            "value": value,
            "number_format": "plain",
            "anchor": "character",
            "x": (left + 30) / 320,
            "y": 50 / 240,
            "anchor_box": [left / 320, 80 / 240, 60 / 320, 60 / 240],
        }
        label["anchor_binding"] = bind_anchor(path, label, take_id=42, frame_index=frame_index)
        labels.append(label)
    return labels


def test_character_overlay_requires_actual_source_approval(tmp_path):
    from calliope.studio.screen_text import write_overlay

    path = moving_pair(tmp_path)
    label = {
        "key": "noah-value",
        "entity_key": "noah",
        "value": 1,
        "anchor": "character",
        "x": 0.25,
        "y": 0.2,
        "anchor_box": [0.15, 0.33, 0.2, 0.25],
    }
    with pytest.raises(ValueError, match="approuv"):
        write_overlay(path, [label], 0, 1, (320, 240), (720, 1280), tmp_path / "text.ass")


@pytest.mark.parametrize("mutation", ["entity", "roi", "source", "take"])
def test_anchor_binding_cannot_be_reused_for_another_identity_source_or_take(tmp_path, mutation):
    from calliope.studio.screen_text import write_overlay

    path = moving_pair(tmp_path)
    label = pair_labels(path)[0]
    take_id = 42
    if mutation == "entity":
        label["entity_key"] = "leo"
    elif mutation == "roi":
        label["anchor_box"][0] += 0.1
    elif mutation == "source":
        path.write_bytes(path.read_bytes() + b"different selected source")
    else:
        take_id = 43
    with pytest.raises(ValueError, match="ancrage|identité|prise"):
        write_overlay(
            path, [label], 0, 1, (320, 240), (720, 1280), tmp_path / "text.ass", take_id=take_id
        )


def test_tracking_starts_at_the_explicitly_verified_frame(tmp_path):
    from calliope.studio.screen_text import tracked_positions

    path = moving_pair(tmp_path)
    label = pair_labels(path, frame_index=12)[0]
    records = tracked_positions(path, label, take_id=42)
    assert records[0][1] == pytest.approx(80 / 320, abs=0.006)
    assert records[12][1] == pytest.approx(92 / 320, abs=0.006)
    assert records[-1][1] == pytest.approx(103 / 320, abs=0.006)


def test_tracking_fails_instead_of_following_another_fruit_after_a_swap(tmp_path):
    from calliope.studio.screen_text import tracked_positions

    path = moving_pair(tmp_path, swap=True)
    with pytest.raises(ValueError, match="suivi|identité"):
        tracked_positions(path, pair_labels(path)[0], take_id=42)


def test_occlusion_blocks_export_and_preserves_the_previous_completed_file(tmp_path):
    path = moving_pair(tmp_path, occlude=True)
    destination = tmp_path / "completed.mp4"
    destination.write_bytes(b"previous completed export")
    with pytest.raises(ValueError, match="suivi|identité"):
        asyncio.run(
            export.assemble(
                {
                    "aspect_ratio": "9:16",
                    "clips": [
                        {
                            "path": str(path),
                            "audio_mode": "silent",
                            "take_id": 42,
                            "screen_text": pair_labels(path),
                        }
                    ],
                },
                destination,
            )
        )
    assert destination.read_bytes() == b"previous completed export"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["completed.mp4", "pair.avi"]


def test_occlusion_in_discarded_tail_does_not_reject_a_clean_useful_window(tmp_path):
    from calliope.studio.screen_text import write_overlay

    path = moving_pair(tmp_path, occlude=True)
    _, metadata = write_overlay(
        path,
        pair_labels(path),
        0,
        0.4,
        (320, 240),
        (720, 1280),
        tmp_path / "trimmed.ass",
        take_id=42,
    )
    assert [label["text"] for label in metadata["labels"]] == ["1", "500"]


def test_crossing_two_identities_blocks_instead_of_exchanging_their_values(tmp_path):
    from calliope.studio.screen_text import write_overlay

    path = moving_pair(tmp_path, crossing=True)
    with pytest.raises(ValueError, match="suivi|identité|chevauch"):
        write_overlay(
            path,
            pair_labels(path),
            0,
            1,
            (320, 240),
            (720, 1280),
            tmp_path / "text.ass",
            take_id=42,
        )


def test_still_image_anchor_approval_cannot_authorize_a_video(tmp_path):
    import cv2

    from calliope.studio.screen_text import bind_anchor, tracked_positions

    path = moving_pair(tmp_path)
    capture = cv2.VideoCapture(str(path))
    ok, frame = capture.read()
    capture.release()
    assert ok
    still = tmp_path / "reference.png"
    cv2.imwrite(str(still), frame)
    label = pair_labels(path)[0]
    label["anchor_binding"] = bind_anchor(still, label, take_id=42)
    with pytest.raises(ValueError, match="source"):
        tracked_positions(path, label, take_id=42)


def test_two_scores_keep_identity_and_change_only_at_the_local_trimmed_time(tmp_path):
    from calliope.studio.screen_text import write_overlay

    path = moving_pair(tmp_path)
    labels = pair_labels(path)
    labels[0]["event"] = "change"
    labels[0]["changes"] = [{"at_sec": 0.25, "value": 2}]
    destination = tmp_path / "text.ass"
    _, metadata = write_overlay(
        path, labels, 0.25, 0.5, (320, 240), (720, 1280), destination, take_id=42
    )
    events = [line for line in destination.read_text().splitlines() if line.startswith("Dialogue")]
    noah = [line for line in events if line.endswith("}1") or line.endswith("}2")]
    assert noah[0].split(",")[1] == "0:00:00.00"
    assert next(line for line in noah if line.endswith("}2")).split(",")[1] == "0:00:00.25"
    assert all(line.endswith("}500") for line in events if line not in noah)
    assert metadata["labels"][0]["changes"] == [{"at_sec": 0.25, "value": 2}]


def test_timed_changes_reject_ambiguous_order_and_out_of_clip_times(tmp_path):
    from calliope.studio.screen_text import ScreenText, write_overlay

    label = {
        "key": "score",
        "value": 1,
        "x": 0.5,
        "y": 0.1,
        "event": "change",
        "changes": [{"at_sec": 0.5, "value": 2}, {"at_sec": 0.2, "value": 3}],
    }
    with pytest.raises(ValueError, match="ordre"):
        ScreenText.model_validate(label)
    label["changes"] = [{"at_sec": 0.5, "value": 2}]
    with pytest.raises(ValueError, match="durée"):
        write_overlay("unused", [label], 0, 0.5, (320, 240), (720, 1280), tmp_path / "text.ass")


def test_verified_rois_of_two_identities_cannot_overlap(tmp_path):
    from calliope.studio.screen_text import bind_anchor, write_overlay

    path = moving_pair(tmp_path)
    labels = pair_labels(path)
    labels[1].update(x=labels[0]["x"], anchor_box=labels[0]["anchor_box"][:])
    labels[1]["anchor_binding"] = bind_anchor(path, labels[1], take_id=42)
    with pytest.raises(ValueError, match="chevauch"):
        write_overlay(
            path, labels, 0, 1, (320, 240), (720, 1280), tmp_path / "text.ass", take_id=42
        )
