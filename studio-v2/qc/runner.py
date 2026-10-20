"""Exécution unifiée des contrôles automatiques sur une prise ou un export MP4.

Philosophie V2 :
- Erreurs techniques objectives (fichier absent/corrompu, dimensions/audio invalides) -> `errors`
- Cas ambigus (OCR incertain, texte suspect, divergence Whisper) -> `warnings` + revue humaine,
  jamais un rejet aveugle en boucle.
"""

from __future__ import annotations

from pathlib import Path

from core.config import V2Settings
from core.scenario import Shot
from qc.media import inspect_media, verify_export_delivery
from qc.ocr import scan_parasite_text
from qc.speech import assess_speech, transcribe_audio_cpu


def allowed_overlay_values(shot: Shot, include_overlay: bool) -> list[str]:
    if not include_overlay or shot.display_overlay is None:
        return []
    values = [shot.display_overlay.initial_text]
    values.extend(change.text for change in shot.display_overlay.changes)
    return values


def run_take_qc(
    video_path: Path,
    shot: Shot,
    settings: V2Settings,
    *,
    is_exported_overlay: bool = False,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        media = inspect_media(video_path)
    except ValueError as exc:
        return {
            "ok": False,
            "requires_human_review": True,
            "errors": [str(exc)],
            "warnings": [],
            "media": None,
            "ocr": None,
            "speech": None,
        }

    if abs(media["duration_s"] - shot.duration_s) > 1.0:
        warnings.append(
            f"Durée mesurée ({media['duration_s']:.2f}s) éloignée de la cible ({shot.duration_s:.2f}s)."
        )

    ocr = (
        scan_parasite_text(
            video_path,
            allowed_values=allowed_overlay_values(shot, include_overlay=is_exported_overlay),
        )
        if settings.ocr_enabled
        else {"status": "disabled", "reason": "OCR désactivé."}
    )
    if ocr["status"] == "unexpected_text":
        warnings.append(f"OCR : {ocr['reason']} ({ocr.get('hits')})")
    elif ocr["status"] == "unverified":
        warnings.append(f"OCR : {ocr['reason']}")

    expected_dialogue = " ".join(line.text for line in shot.dialogue)
    transcript = transcribe_audio_cpu(
        video_path,
        model_or_path=settings.whisper_model_or_path,
        language=settings.whisper_language,
    )
    speech_eval = assess_speech(
        expected_dialogue, transcript, language=settings.whisper_language
    )
    if speech_eval["status"] != "ok" and speech_eval.get("reason"):
        warnings.append(f"Whisper : {speech_eval['reason']}")

    return {
        "ok": len(errors) == 0,
        "requires_human_review": True,  # Validation humaine toujours requise avant publication
        "errors": errors,
        "warnings": warnings,
        "media": media,
        "ocr": ocr,
        "transcript": transcript,
        "speech": speech_eval,
    }


def run_export_qc(
    export_path: Path,
    shots: list[Shot],
    settings: V2Settings,
    expected_size: tuple[int, int] = (1080, 1920),
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        media = verify_export_delivery(export_path, expected_size=expected_size)
    except ValueError as exc:
        return {
            "ok": False,
            "requires_human_review": True,
            "errors": [str(exc)],
            "warnings": [],
            "media": None,
            "ocr": None,
            "speech": None,
        }

    expected_total = sum(s.duration_s for s in shots)
    if expected_total > 0 and abs(media["duration_s"] - expected_total) > max(1.5, len(shots) * 0.4):
        warnings.append(
            f"Durée assemblée ({media['duration_s']:.2f}s) distante de la somme théorique ({expected_total:.2f}s)."
        )

    allowed: list[str] = []
    for s in shots:
        allowed.extend(allowed_overlay_values(s, include_overlay=True))

    ocr = (
        scan_parasite_text(export_path, allowed_values=allowed)
        if settings.ocr_enabled
        else {"status": "disabled", "reason": "OCR désactivé."}
    )
    if ocr["status"] in ("unexpected_text", "unverified"):
        warnings.append(f"OCR export : {ocr['reason']}")

    full_dialogue = " ".join(line.text for s in shots for line in s.dialogue)
    transcript = transcribe_audio_cpu(
        export_path,
        model_or_path=settings.whisper_model_or_path,
        language=settings.whisper_language,
    )
    speech_eval = assess_speech(
        full_dialogue, transcript, language=settings.whisper_language
    )
    if speech_eval["status"] != "ok" and speech_eval.get("reason"):
        warnings.append(f"Whisper export : {speech_eval['reason']}")

    return {
        "ok": len(errors) == 0,
        "requires_human_review": True,
        "errors": errors,
        "warnings": warnings,
        "media": media,
        "ocr": ocr,
        "transcript": transcript,
        "speech": speech_eval,
    }
