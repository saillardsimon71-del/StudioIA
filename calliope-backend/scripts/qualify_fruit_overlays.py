import argparse
import asyncio
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from calliope.studio.export import assemble
from calliope.studio.screen_text import bind_anchor, source_sha256, tracked_positions


def fruit_layer(path, polygon):
    image = Image.open(path).convert("RGBA")
    mask = Image.new("L", image.size)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(round(x * image.width), round(y * image.height)) for x, y in polygon], fill=255)
    image.putalpha(mask.filter(ImageFilter.GaussianBlur(1)))
    return image.resize((380, 680), Image.Resampling.LANCZOS)


def build_fixture(evidence_path, output):
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    references = {entity["id"]: Path(entity["reference_path"]) for entity in evidence["entities"]}
    sources = [references[25], references[28]]
    original_hashes = {str(path): source_sha256(path) for path in sources}
    layers = [
        fruit_layer(sources[0], [(0.30, 0.17), (0.49, 0.14), (0.72, 0.17), (0.74, 0.33),
                                (0.64, 0.40), (0.86, 0.44), (0.95, 0.76), (0.86, 1),
                                (0.12, 1), (0.13, 0.70), (0.20, 0.48), (0.36, 0.39),
                                (0.28, 0.30)]),
        fruit_layer(sources[1], [(0.50, 0.025), (0.52, 0.026), (0.48, 0.075),
                                (0.65, 0.068), (0.73, 0.145), (0.72, 0.24), (0.65, 0.28),
                                (0.87, 0.34), (0.95, 0.62), (0.88, 1), (0.14, 1),
                                (0.12, 0.70), (0.04, 0.39), (0.07, 0.33), (0.23, 0.38),
                                (0.37, 0.29), (0.29, 0.20), (0.31, 0.095), (0.46, 0.067)]),
    ]
    background = Image.open(sources[0]).convert("RGB").resize((720, 1280))
    background = background.filter(ImageFilter.GaussianBlur(42))
    background = Image.blend(background, Image.new("RGB", background.size, "#121b24"), 0.72)
    output.mkdir(parents=True, exist_ok=True)
    records, clips = [], []
    for take_id, name in enumerate(("A-static", "B-movement", "C-transition"), 1):
        source = output / (name + "-source.mp4")
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 24, (720, 1280))
        positions = []
        for frame_index in range(72):
            phase = frame_index / 71 * 2 * math.pi
            dx = round(12 * math.sin(phase)) if name == "B-movement" else 0
            dy = round(5 * math.sin(phase)) if name == "B-movement" else 0
            origins = [(0 + dx, 360 + dy), (350 - dx, 426 - dy)]
            frame = background.copy()
            for layer, origin in zip(layers, origins):
                frame.paste(layer, origin, layer)
            writer.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))
            positions.append(origins)
        writer.release()
        labels = []
        for entity, value, origin, roi in [
            ("noah_fixture", 1, positions[0][0], (118, 151, 150, 108)),
            ("leo_fixture", 500, positions[0][1], (121, 76, 145, 100)),
        ]:
            ox, oy = origin
            rx, ry, rw, rh = roi
            label = {"key": entity + "-value", "entity_key": entity, "value": value,
                     "number_format": "plain", "anchor": "character", "size": 0.037,
                     "x": (ox + rx + rw / 2) / 720,
                     "y": (oy + (72 if value == 1 else 5)) / 1280,
                     "anchor_box": [(ox + rx) / 720, (oy + ry) / 1280, rw / 720, rh / 1280]}
            if name == "C-transition" and value == 1:
                label.update(event="change", changes=[{"at_sec": 1.0, "value": 2}])
            label["anchor_binding"] = bind_anchor(source, label, take_id=take_id)
            labels.append(label)
        tracks = [tracked_positions(source, label, take_id=take_id) for label in labels]
        errors = []
        for identity_index, points in enumerate(tracks):
            x0, y0 = labels[identity_index]["x"] * 720, labels[identity_index]["y"] * 1280
            origin0 = positions[0][identity_index]
            errors.append(max(math.hypot(
                point[1] * 720 - (x0 + origins[identity_index][0] - origin0[0]),
                point[2] * 1280 - (y0 + origins[identity_index][1] - origin0[1]),
            ) for point, origins in zip(points, positions)))
        clip = {"clip_id": take_id, "take_id": take_id, "path": str(source),
                "audio_mode": "silent", "screen_text": labels}
        metadata = asyncio.run(assemble({"aspect_ratio": "9:16", "clips": [clip]},
                                        output / (name + ".mp4")))
        clips.append(clip)
        records.append({"name": name, "metadata": metadata, "tracking_max_error_pixels": errors})
        capture = cv2.VideoCapture(str(output / (name + ".mp4")))
        for frame_index in ([0, 36, 71] if name == "B-movement" else [0, 23, 24, 48]):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                raise ValueError("Fixture frame extraction failed")
            cv2.imwrite(str(output / f"{name}-frame-{frame_index:03d}.png"), frame)
        capture.release()
    metadata = asyncio.run(assemble({"aspect_ratio": "9:16", "clips": clips},
                                    output / "fruit-overlay-proof.mp4"))
    if original_hashes != {str(path): source_sha256(path) for path in sources}:
        raise ValueError("Canonical source bytes changed")
    report = {
        "fixture_only": True,
        "identity_standins": {"noah_fixture": "series9 entity25 Maximilian fruit reference",
                              "leo_fixture": "series9 entity28 Leo fruit reference"},
        "source_hashes_unchanged": original_hashes,
        "clips": records, "combined": metadata,
        "limits": (
            "Rigid cropped fruit layers qualify static and slight motion. "
            "Occlusion or ambiguous overlap aborts export. "
            "This does not qualify arbitrary pose changes or a corrected episode."
        ),
    }
    (output / "qualification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    tiles = []
    for name, indices in [("A-static", [0]), ("B-movement", [0, 36, 71]),
                           ("C-transition", [23, 24])]:
        for index in indices:
            tile = Image.new("RGB", (270, 500), "#121b24")
            frame = Image.open(output / f"{name}-frame-{index:03d}.png").resize((270, 480))
            tile.paste(frame, (0, 20))
            ImageDraw.Draw(tile).text((8, 4), f"{name} frame {index}", fill="white")
            tiles.append(tile)
    sheet = Image.new("RGB", (270 * len(tiles), 500))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (270 * index, 0))
    sheet.save(output / "fruit-overlay-contact.png")
    print(json.dumps({"output": str(output), "tracking_errors": [r["tracking_max_error_pixels"]
                                                                    for r in records]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    build_fixture(arguments.evidence, arguments.output)
