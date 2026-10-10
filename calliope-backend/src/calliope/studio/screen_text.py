import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValueChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_sec: float = Field(gt=0, allow_inf_nan=False)
    value: int


class AnchorBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    roi_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frame_index: int = Field(default=0, ge=0)
    entity_key: str = Field(min_length=1, max_length=80)
    take_id: int | None = Field(default=None, gt=0)
    approved: Literal[True]


def _number(value, number_format):
    text = str(value) if number_format == "plain" else f"{value:,}"
    return text.replace(",", " ") if number_format == "grouped_space" else text


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
    anchor_binding: AnchorBinding | None = None
    changes: list[ValueChange] = Field(default_factory=list, max_length=40)
    event: Literal["none", "appear", "change", "reveal"] = "none"

    @model_validator(mode="after")
    def check_anchor(self):
        if self.value is not None:
            self.text = _number(self.value, self.number_format)
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
        if self.changes:
            if self.value is None or self.event not in ("change", "reveal"):
                raise ValueError(
                    "Un changement de valeur nécessite une valeur initiale et un événement."
                )
            times = [change.at_sec for change in self.changes]
            if times != sorted(set(times)):
                raise ValueError(
                    "Les changements de valeur doivent rester dans l'ordre chronologique."
                )
        return self


def source_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _anchor_frame(path, frame_index):
    import cv2
    import numpy as np

    if Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame_index != 0 or frame is None:
            raise ValueError("Image d'ancrage illisible.")
        return frame
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok:
            raise ValueError("Frame d'ancrage introuvable dans la prise.")
        return frame
    finally:
        capture.release()


def _roi_pixels(frame, box):
    height, width = frame.shape[:2]
    x, y, bw, bh = box
    left, top, right, bottom = (
        round(x * width),
        round(y * height),
        round((x + bw) * width),
        round((y + bh) * height),
    )
    if right - left < 8 or bottom - top < 8:
        raise ValueError("Zone d'ancrage trop petite pour vérifier son identité.")
    return frame[top:bottom, left:right], (left, top, right, bottom)


def _roi_sha256(frame, box):
    roi, _ = _roi_pixels(frame, box)
    digest = hashlib.sha256(json.dumps(box).encode("ascii"))
    digest.update(roi.tobytes())
    return digest.hexdigest()


def bind_anchor(path, label, take_id=None, frame_index=0):
    label = ScreenText.model_validate(label).model_dump()
    if label["anchor"] != "character" or not label["entity_key"]:
        raise ValueError("Un ancrage approuvé nécessite une identité de personnage.")
    frame = _anchor_frame(path, frame_index)
    return AnchorBinding(
        source_sha256=source_sha256(path),
        roi_sha256=_roi_sha256(frame, label["anchor_box"]),
        frame_index=frame_index,
        entity_key=label["entity_key"],
        take_id=take_id,
        approved=True,
    ).model_dump()


def tracked_positions(path, label, take_id=None, start=0, duration=None):
    import cv2
    import numpy as np

    label = ScreenText.model_validate(label).model_dump()
    binding = label["anchor_binding"]
    if not binding:
        raise ValueError("L'ancrage du personnage doit être approuvé sur la prise réelle.")
    if binding["entity_key"] != label["entity_key"]:
        raise ValueError("L'identité du personnage diffère de l'ancrage approuvé.")
    if take_id is not None and binding["take_id"] != take_id:
        raise ValueError("L'ancrage approuvé appartient à une autre prise.")
    if source_sha256(path) != binding["source_sha256"]:
        raise ValueError("La source de l'ancrage approuvé a changé.")
    frame_index = binding["frame_index"]
    anchor = _anchor_frame(path, frame_index)
    if _roi_sha256(anchor, label["anchor_box"]) != binding["roi_sha256"]:
        raise ValueError("La zone de l'ancrage approuvé a changé.")
    height, width = anchor.shape[:2]
    scale = min(1, 640 / max(height, width))

    def scaled(image):
        return cv2.resize(image, None, fx=scale, fy=scale)

    anchor = scaled(anchor)
    template, (left, top, right, bottom) = _roi_pixels(anchor, label["anchor_box"])
    original_gray = cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    h, w = original_gray.shape
    mask = np.zeros_like(original_gray)
    mask[top:bottom, left:right] = 255
    initial_points = cv2.goodFeaturesToTrack(
        original_gray, maxCorners=100, qualityLevel=0.02, minDistance=4, mask=mask
    )
    if initial_points is None or len(initial_points) < 6:
        raise ValueError("Zone de texte non suivable: choisissez une autre zone d'ancrage.")
    minimum_points = max(6, math.ceil(len(initial_points) * 0.55))
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or count <= frame_index:
            raise ValueError("Vidéo illisible pour le suivi du texte.")
        position = np.array([label["x"] * w, label["y"] * h, 1.0])
        records = {frame_index: (frame_index / fps, label["x"], label["y"], 1.0, 0.0)}
        first = max(0, math.floor(start * fps))
        last = count if duration is None else min(count, math.ceil((start + duration) * fps))
        for direction in (1, -1):
            previous = original_gray
            points = initial_points.copy()
            transform = np.eye(3)
            indices = (
                range(frame_index + 1, last)
                if direction == 1
                else range(frame_index - 1, first - 1, -1)
            )
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index + 1)
            for index in indices:
                if direction == -1:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if not ok:
                    raise ValueError("Le suivi du texte rencontre une frame illisible.")
                current_bgr = scaled(frame)
                current = cv2.cvtColor(current_bgr, cv2.COLOR_BGR2GRAY)
                moved, status, _ = cv2.calcOpticalFlowPyrLK(
                    previous, current, points, None, winSize=(21, 21), maxLevel=3
                )
                if moved is None:
                    raise ValueError("Le suivi du texte a perdu son personnage.")
                returned, reverse_status, _ = cv2.calcOpticalFlowPyrLK(
                    current, previous, moved, None, winSize=(21, 21), maxLevel=3
                )
                if returned is None:
                    raise ValueError("Le suivi du texte ne confirme plus son identité.")
                valid = (
                    (status.ravel() == 1)
                    & (reverse_status.ravel() == 1)
                    & (np.linalg.norm(points - returned, axis=2).ravel() < 1.0)
                )
                old, new = points[valid], moved[valid]
                if len(old) < minimum_points:
                    raise ValueError("Le suivi du texte a perdu son personnage.")
                matrix, inliers = cv2.estimateAffinePartial2D(
                    old, new, method=cv2.RANSAC, ransacReprojThreshold=1.5
                )
                if matrix is None or inliers is None or inliers.sum() < minimum_points:
                    raise ValueError("Le suivi du texte est incertain; vérifiez son ancrage.")
                factor = math.hypot(matrix[0, 0], matrix[0, 1])
                if not 0.85 < factor < 1.15:
                    raise ValueError("Le personnage change trop rapidement pour le suivi du texte.")
                transform = np.vstack((matrix, [0, 0, 1])) @ transform
                sampling = transform[:2].copy()
                sampling[:, 2] += transform[:2, :2] @ np.array([left, top])
                candidate = cv2.warpAffine(
                    current_bgr,
                    sampling,
                    (right - left, bottom - top),
                    flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                )
                correlation = cv2.matchTemplate(
                    cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY),
                    template_gray,
                    cv2.TM_CCOEFF_NORMED,
                )[0, 0]
                color_error = (
                    np.mean(np.abs(candidate.astype(float) - template.astype(float))) / 255
                )
                if (
                    not math.isfinite(float(correlation))
                    or correlation < 0.65
                    or color_error > 0.18
                ):
                    raise ValueError(
                        "L'identité visuelle du suivi n'est plus vérifiée "
                        "(occlusion ou changement)."
                    )
                placed = transform[:2] @ position
                zoom = math.hypot(transform[0, 0], transform[0, 1])
                angle = math.degrees(math.atan2(transform[1, 0], transform[0, 0]))
                if not (0 <= placed[0] / w <= 1 and 0 <= placed[1] / h <= 1):
                    raise ValueError("L'ancrage suivi sort de l'image.")
                records[index] = (index / fps, placed[0] / w, placed[1] / h, zoom, angle)
                points = new[inliers.ravel() == 1].reshape(-1, 1, 2)
                previous = current
        return [records[index] for index in sorted(records)]
    finally:
        capture.release()


def _tracked_box(label, point, source_size):
    sw, sh = source_size
    _, px, py, zoom, angle = point
    x, y, bw, bh = label["anchor_box"]
    cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    corners = []
    for cx, cy in ((x, y), (x + bw, y), (x, y + bh), (x + bw, y + bh)):
        dx, dy = (cx - label["x"]) * sw, (cy - label["y"]) * sh
        corners.append(
            (px + zoom * (cosine * dx - sine * dy) / sw, py + zoom * (sine * dx + cosine * dy) / sh)
        )
    return (
        min(c[0] for c in corners),
        min(c[1] for c in corners),
        max(c[0] for c in corners),
        max(c[1] for c in corners),
    )


def write_overlay(
    path, labels, start, duration, source_size, target_size, destination, take_id=None,
    phone=False,
):
    sw, sh = source_size
    width, height = target_size
    scale = min(width / sw, height / sh)
    padding = ((width - sw * scale) / 2, (height - sh * scale) / 2)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Label,Arial,40,&H00FFFFFF,&H00FFFFFF,&H60181818,&H90202020,"
        "1,0,0,0,100,100,0,0,1,1.5,0,5,0,0,0,1",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    details, trajectories = [], []

    def timestamp(seconds):
        value = round(max(0, seconds) * 100)
        return f"{value // 360000}:{value // 6000 % 60:02}:{value // 100 % 60:02}.{value % 100:02}"

    if phone:
        cx, cy = padding[0] + sw * scale * 0.68, padding[1] + sh * scale * 0.67
        pw = min(sw * 0.42, sh * 0.25) * scale
        ph = pw * 1.8
        x, y = cx - pw / 2, cy - ph / 2
        radius = pw * 0.08
        drawing = (
            f"m {x + radius:.2f} {y:.2f} l {x + pw - radius:.2f} {y:.2f} "
            f"b {x + pw:.2f} {y:.2f} {x + pw:.2f} {y:.2f} {x + pw:.2f} {y + radius:.2f} "
            f"l {x + pw:.2f} {y + ph - radius:.2f} "
            f"b {x + pw:.2f} {y + ph:.2f} {x + pw:.2f} {y + ph:.2f} "
            f"{x + pw - radius:.2f} {y + ph:.2f} "
            f"l {x + radius:.2f} {y + ph:.2f} "
            f"b {x:.2f} {y + ph:.2f} {x:.2f} {y + ph:.2f} {x:.2f} {y + ph - radius:.2f} "
            f"l {x:.2f} {y + radius:.2f} b {x:.2f} {y:.2f} {x:.2f} {y:.2f} {x + radius:.2f} {y:.2f}"
        )
        lines.append(
            f"Dialogue: 0,0:00:00.00,{timestamp(duration)},Label,,0,0,0,,"
            + "{\\an7\\pos(0,0)\\p1\\shad0\\blur0\\bord3\\3c&HFFFFFF&\\c&H201710&}"
            + drawing
        )
        lines.append(
            f"Dialogue: 0,0:00:00.00,{timestamp(duration)},Label,,0,0,0,,"
            + "{\\an7\\pos(0,0)\\p1\\shad0\\blur0\\bord0\\c&HFFFFFF&}"
            + f"m {cx - pw * 0.12:.2f} {y + ph * 0.06:.2f} "
            + f"l {cx + pw * 0.12:.2f} {y + ph * 0.06:.2f} "
            + f"{cx + pw * 0.12:.2f} {y + ph * 0.075:.2f} "
            + f"{cx - pw * 0.12:.2f} {y + ph * 0.075:.2f}"
        )

    for raw in labels:
        label = ScreenText.model_validate(raw).model_dump()
        tracked = label["anchor"] == "character"
        if any(change["at_sec"] >= duration for change in label["changes"]):
            raise ValueError("Le changement de valeur dépasse la durée conservée du plan.")
        points = (
            tracked_positions(path, label, take_id, start, duration)
            if tracked
            else [(start, label["x"], label["y"], 1.0, 0.0)]
        )
        if tracked:
            for other, other_points in trajectories:
                if other["entity_key"] == label["entity_key"]:
                    continue
                for point, other_point in zip(points, other_points):
                    if not start <= point[0] < start + duration:
                        continue
                    a, b = (
                        _tracked_box(label, point, source_size),
                        _tracked_box(other, other_point, source_size),
                    )
                    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                        0, min(a[3], b[3]) - max(a[1], b[1])
                    )
                    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                    if overlap > smaller * 0.15:
                        raise ValueError(
                            "Les ancrages de deux identités se chevauchent; suivi ambigu."
                        )
            trajectories.append((label, points))
        for index, (at, x, y, zoom, angle) in enumerate(points):
            until = points[index + 1][0] if index + 1 < len(points) else start + duration
            if until <= start or at >= start + duration:
                continue
            x, y = padding[0] + x * sw * scale, padding[1] + y * sh * scale
            color = label["color"]
            bgr = color[4:6] + color[2:4] + color[:2]
            font_size = height * label["size"] * zoom
            if phone and label["key"].startswith("studio-midroll-"):
                font_size = min(font_size, pw * 0.82 * 1.5 / len(label["text"]))
            tags = (
                f"\\pos({x:.2f},{y:.2f})\\fs{font_size:.2f}"
                f"\\frz{-angle:.2f}\\c&H{bgr}&\\blur0.6"
            )
            begin, end = max(start, at) - start, min(start + duration, until) - start
            cuts = (
                [begin]
                + [
                    change["at_sec"]
                    for change in label["changes"]
                    if begin < change["at_sec"] < end
                ]
                + [end]
            )
            for begin, end in zip(cuts, cuts[1:]):
                text = label["text"]
                for change in label["changes"]:
                    if change["at_sec"] <= begin + 1e-9:
                        text = _number(change["value"], label["number_format"])
                lines.append(
                    f"Dialogue: {int(phone)},{timestamp(begin)},{timestamp(end)},Label,,0,0,0,,"
                    + "{"
                    + tags
                    + "\\bord3\\blur3\\alpha&H80&}"
                    + text
                )
                lines.append(
                    f"Dialogue: {1 + int(phone)},{timestamp(begin)},{timestamp(end)},Label,,0,0,0,,"
                    + "{"
                    + tags
                    + "}"
                    + text
                )
        details.append(label)
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    escaped = re.sub(r"([\\:'\[\],])", r"\\\1", Path(destination).as_posix())
    return f"ass=filename='{escaped}'", {
        "labels": details,
        "phone": phone,
        "tracking": "character"
        if any(label["anchor"] == "character" for label in details)
        else "screen",
    }
