import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScreenText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80)
    text: str = Field(default="", max_length=200, pattern=r"^[^\\{}\r\n]*$")
    value: int | None = None
    number_format: Literal["plain", "grouped_comma", "grouped_space"] = "grouped_comma"
    entity_key: str = ""
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    size: float = Field(default=0.035, ge=0.015, le=0.15)
    color: str = Field(default="FFFFFF", pattern=r"^[0-9A-Fa-f]{6}$")
    anchor: Literal["screen", "character"] = "screen"
    anchor_box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    event: Literal["none", "appear", "change", "reveal"] = "none"

    @model_validator(mode="after")
    def check_anchor(self):
        if self.value is not None:
            self.text = str(self.value) if self.number_format == "plain" else f"{self.value:,}"
            if self.number_format == "grouped_space":
                self.text = self.text.replace(",", " ")
        if not self.text.strip():
            raise ValueError("Le texte ou la valeur à afficher est obligatoire.")
        if self.anchor == "character" and not self.anchor_box:
            raise ValueError("Un texte attaché au personnage nécessite une zone de suivi.")
        if self.anchor_box:
            x, y, w, h = self.anchor_box
            if not all(math.isfinite(v) for v in self.anchor_box) or (
                min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1
            ):
                raise ValueError("La zone de suivi doit rester dans l'image.")
        return self


def tracked_positions(path, label):
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        ok, frame = capture.read()
        if not ok or fps <= 0:
            raise ValueError("Vidéo illisible pour le suivi du texte.")
        height, width = frame.shape[:2]
        scale = min(1, 640 / max(height, width))

        def gray(image):
            return cv2.cvtColor(cv2.resize(image, None, fx=scale, fy=scale), cv2.COLOR_BGR2GRAY)

        previous = gray(frame)
        h, w = previous.shape
        x, y, bw, bh = label["anchor_box"]
        mask = np.zeros_like(previous)
        mask[round(y * h):round((y + bh) * h), round(x * w):round((x + bw) * w)] = 255
        points = cv2.goodFeaturesToTrack(
            previous, maxCorners=100, qualityLevel=0.02, minDistance=4, mask=mask
        )
        if points is None or len(points) < 6:
            raise ValueError(
                "Zone de texte non suivable: choisissez un ancrage fixe ou une autre zone."
            )
        position = np.array([label["x"] * w, label["y"] * h, 1.0])
        records = [(0.0, label["x"], label["y"], 1.0, 0.0)]
        frame_index = 1
        size, angle = 1.0, 0.0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            current = gray(frame)
            moved, status, _ = cv2.calcOpticalFlowPyrLK(
                previous, current, points, None, winSize=(21, 21), maxLevel=3
            )
            if moved is None:
                raise ValueError("Le suivi du texte a perdu son personnage.")
            old, new = points[status.ravel() == 1], moved[status.ravel() == 1]
            if len(old) < 6:
                raise ValueError("Le suivi du texte a perdu son personnage.")
            matrix, inliers = cv2.estimateAffinePartial2D(
                old, new, method=cv2.RANSAC, ransacReprojThreshold=2.0
            )
            if matrix is None or inliers.sum() < 6:
                raise ValueError("Le suivi du texte est incertain; vérifiez son ancrage.")
            position[:2] = matrix @ position
            factor = math.hypot(matrix[0, 0], matrix[0, 1])
            if not 0.85 < factor < 1.15:
                raise ValueError("Le personnage change trop rapidement pour le suivi du texte.")
            size *= factor
            angle += math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
            records.append((frame_index / fps, position[0] / w, position[1] / h, size, angle))
            points = new[inliers.ravel() == 1].reshape(-1, 1, 2)
            previous = current
            frame_index += 1
        return records
    finally:
        capture.release()


def write_overlay(path, labels, start, duration, source_size, target_size, destination):
    sw, sh = source_size
    width, height = target_size
    scale = min(width / sw, height / sh)
    padding = ((width - sw * scale) / 2, (height - sh * scale) / 2)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Label,Arial,40,&H00FFFFFF,&H00FFFFFF,&H70181818,&H90202020,"
        "1,0,0,0,100,100,0,0,3,3,2,5,0,0,0,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    details = []

    def timestamp(seconds):
        value = round(max(0, seconds) * 100)
        return f"{value // 360000}:{value // 6000 % 60:02}:{value // 100 % 60:02}.{value % 100:02}"

    for raw in labels:
        label = ScreenText.model_validate(raw).model_dump()
        tracked = label["anchor"] == "character"
        points = tracked_positions(path, label) if tracked else [
            (start, label["x"], label["y"], 1.0, 0.0)
        ]
        for index, (at, x, y, zoom, angle) in enumerate(points):
            until = points[index + 1][0] if index + 1 < len(points) else start + duration
            if until <= start or at >= start + duration:
                continue
            x, y = padding[0] + x * sw * scale, padding[1] + y * sh * scale
            color = label["color"]
            bgr = color[4:6] + color[2:4] + color[:2]
            tags = (
                f"\\pos({x:.2f},{y:.2f})\\fs{height * label['size'] * zoom:.2f}"
                f"\\frz{-angle:.2f}\\c&H{bgr}&\\blur0.6"
            )
            lines.append(
                f"Dialogue: 0,{timestamp(max(start, at) - start)},"
                f"{timestamp(min(start + duration, until) - start)},Label,,0,0,0,,"
                + "{" + tags + "}" + label["text"]
            )
        details.append(label)
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    escaped = re.sub(r"([\\:'\[\],])", r"\\\1", Path(destination).as_posix())
    return f"ass=filename='{escaped}'", {
        "labels": details, "tracking": "character" if any(
            label["anchor"] == "character" for label in details
        ) else "screen",
    }
