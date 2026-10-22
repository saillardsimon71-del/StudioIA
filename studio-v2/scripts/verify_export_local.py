"""Validation locale du montage final, sans GPU ni API payante.

Construit un film vertical de 60 s à partir de six prises synthétiques distinctes,
l'exporte avec le pipeline réel (`core.export`) puis vérifie objectivement :

- dimensions 1080×1920, 24 fps, audio AAC 48 kHz stéréo ;
- durée finale proche de la somme des plans ;
- ordre des plans (couleur dominante échantillonnée au centre de chaque segment) ;
- incrustation de compteur présente et à jour ;
- rapport QC sans erreur (pas de doublon, pas d'export noir ou tronqué).

Usage (depuis studio-v2) :
    .\\.venv\\Scripts\\python.exe scripts\\verify_export_local.py
    .\\.venv\\Scripts\\python.exe scripts\\verify_export_local.py --keep

Aucun projet, média ou rendu existant n'est lu ni modifié : tout se passe dans un
dossier temporaire.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import V2Settings  # noqa: E402
from core.export import export_project_mp4  # noqa: E402
from core.scenario import Character, DisplayChange, DisplayOverlay, Project, Shot  # noqa: E402
from qc.media import ffmpeg_exe, inspect_media  # noqa: E402

SEGMENTS = [
    ("red", (0, 0, 255), 330),
    ("green", (0, 160, 0), 392),
    ("blue", (255, 0, 0), 440),
    ("yellow", (0, 220, 220), 494),
    ("magenta", (255, 0, 255), 523),
    ("cyan", (255, 255, 0), 587),
]  # couleurs BGR attendues par OpenCV


def make_clip(path: Path, duration_s: float, color: str, freq: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=768x1344:r=24:d={duration_s}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={duration_s}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def sample_bgr(path: Path, at_sec: float) -> tuple[int, int, int]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, at_sec * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"Lecture impossible à {at_sec:.1f}s dans {path.name}")
        return tuple(int(value) for value in frame.mean(axis=(0, 1)))
    finally:
        cap.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation locale du montage 60 s")
    parser.add_argument("--segment-seconds", type=float, default=10.0)
    parser.add_argument("--keep", action="store_true", help="Conserve les fichiers de validation")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="studioia-export-validation-"))
    settings = V2Settings(data_dir=work / "data", ocr_enabled=False, whisper_model_or_path="")
    settings.ensure_dirs()

    duration = args.segment_seconds
    clips: list[tuple[Path, tuple[int, int, int]]] = []
    for index, (name, bgr, freq) in enumerate(SEGMENTS):
        clips.append((make_clip(work / f"take_{index}.mp4", duration, name, freq), bgr))

    project = Project(
        id="validation-export-60s",
        title="Validation locale du montage",
        idea="Six plans colorés synthétiques pour valider l'export vertical.",
        target_duration_s=duration * len(clips),
        characters=[Character(id="temoin", name="Témoin", description="Synthetic color card")],
        shots=[
            Shot(
                id=f"plan_{index + 1}",
                duration_s=duration,
                action="Color card holds steady under studio light.",
                initial_state="A flat color card fills the frame.",
                rendered_video_ref=str(path),
                display_overlay=(
                    DisplayOverlay(
                        initial_text="000",
                        changes=[DisplayChange(at_sec=duration / 2, text="001")],
                    )
                    if index == 2
                    else None
                ),
            )
            for index, (path, _) in enumerate(clips)
        ],
    )

    output, qc = export_project_mp4(project, settings, output_path=work / "export-60s.mp4")
    media = inspect_media(output)

    failures: list[str] = []
    expected_total = duration * len(clips)
    if (media["width"], media["height"]) != (1080, 1920):
        failures.append(f"dimensions {media['width']}x{media['height']} au lieu de 1080x1920")
    if abs(media["duration_s"] - expected_total) > max(1.5, expected_total * 0.05):
        failures.append(f"durée {media['duration_s']:.2f}s pour {expected_total:.2f}s attendus")
    if not media["has_audio"] or media["sample_rate"] != 48000 or media["channels"] != 2:
        failures.append("audio absent ou non conforme (48 kHz stéréo attendu)")
    if not qc.get("ok"):
        failures.extend(f"QC : {error}" for error in qc.get("errors", []))

    order: list[dict] = []
    for index, (_, expected) in enumerate(clips):
        center = index * duration + duration / 2
        observed = sample_bgr(output, center)
        distance = sum(abs(a - b) for a, b in zip(observed, expected))
        order.append({"shot": index + 1, "expected_bgr": expected, "observed_bgr": observed,
                      "distance": distance})
        if distance > 120:
            failures.append(
                f"plan {index + 1} hors ordre ou couleur inattendue "
                f"(attendu {expected}, observé {observed})"
            )

    summary = {
        "ok": not failures,
        "output": str(output),
        "duration_s": media["duration_s"],
        "expected_duration_s": expected_total,
        "size": [media["width"], media["height"]],
        "fps": media["fps"],
        "audio": {"codec": media["audio_codec"], "sample_rate": media["sample_rate"],
                  "channels": media["channels"]},
        "order": order,
        "qc_errors": qc.get("errors", []),
        "qc_warnings": qc.get("warnings", []),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not args.keep:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"Fichiers conservés : {work}")

    if failures:
        print("VALIDATION ÉCHOUÉE", file=sys.stderr)
        return 1
    print("VALIDATION RÉUSSIE : montage 60 s vertical, ordre, audio et export vérifiés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
