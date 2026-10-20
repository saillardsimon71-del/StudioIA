"""Revue humaine d'une prise native : sous-titres parasites et synchro parole.

Outil de contrôle d'UNE prise MP4, sans aucun calcul GPU ni dépense. Il répond
au point 3.5 du dossier de reprise du 20 octobre 2026 : « reprendre seulement la
première prise conservée, lire son MP4, inspecter les sous-titres parasitaires,
le début de la première réplique, l'identité et l'état du compteur, puis passer
par un contrôle humain ».

Deux contrôles automatiques alimentent la décision humaine :

1. OCR (Tesseract) — distingue les sous-titres interdits des chiffres
   diégétiques déclarés du compteur de la fiction. Les chiffres passés dans
   ``--allowed`` forment une liste blanche ; tout autre texte nouveau vu dans au
   moins deux images est un sous-titre parasite.
2. Parole (Whisper local) — mesure le début de la première réplique et le compare
   au début déclaré par le contrat (``--contract-onset``) : une dérive supérieure
   à 1,0 s casse le montage et doit être recalée.

Le verdict global privilégie « À REVOIR » à un faux succès ou un faux rejet : un
contrôle indisponible (Tesseract/Whisper absent, checkpoint non configuré) n'est
jamais un succès. La décision finale reste humaine.

Usage :
    python scripts/qc_take.py prise.mp4 --allowed 000 --allowed 001 --allowed 002 \
        --contract-onset 0.5 --language fr
    python scripts/qc_take.py prise.mp4 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Verdicts humains, du plus grave au plus clément.
REJECTED = "REJETÉE — à corriger"
REVIEW = "À REVOIR — contrôle humain requis"
PROVISIONAL_OK = "CONTRÔLES AUTO OK — validation humaine requise"


def summarize(text_status, speech_status):
    """Verdict global à partir des deux statuts automatiques.

    ``text_status`` : not_detected | unverified | unexpected_text
    ``speech_status`` : ok | timing_mismatch | unchecked | environment_failure | unknown

    Un défaut caractérisé (sous-titre parasite, parole décalée) rejette la prise.
    Sinon, tout contrôle non concluant bascule sur « à revoir » — jamais un faux
    succès. Même « ok » exige une validation humaine (identité, raccord, texte à
    l'image que l'OCR ne garantit pas).
    """
    if text_status == "unexpected_text" or speech_status == "timing_mismatch":
        return REJECTED
    if text_status in ("unverified",) or speech_status in (
        "environment_failure", "unknown", "unchecked"
    ):
        return REVIEW
    if text_status == "not_detected" and speech_status == "ok":
        return PROVISIONAL_OK
    return REVIEW


def qc_take(path, allowed=(), language="fr", contract_onset=None, checkpoint=""):
    """Contrôle une prise et renvoie un rapport dict (aucun GPU, aucune dépense)."""
    from calliope.studio import generated_text, production, transcription

    path = str(path)
    report: dict = {
        "take": path,
        "language": language,
        "allowed_display": sorted({str(value) for value in allowed}),
    }

    # 1. Sous-titres parasites vs chiffres diégétiques.
    text_qc = generated_text.scan(path, allowed=list(allowed))
    report["text_qc"] = text_qc

    # 2. Début de parole et dérive par rapport au contrat.
    transcript = transcription.transcribe_dialogue(path, checkpoint, language)
    report["transcription"] = {
        "status": transcript.get("status"),
        "reason": transcript.get("reason"),
        "environment_failure": transcript.get("environment_failure"),
        "speech_intervals": transcript.get("speech_intervals") or [],
    }
    speech_status = "unchecked"
    if contract_onset is None:
        # Sans début déclaré, on ne peut pas mesurer la dérive : à revoir.
        speech_status = "unchecked" if transcript.get("status") == "ok" else (
            "environment_failure" if transcript.get("environment_failure")
            else transcript.get("status") or "unknown")
        report["speech"] = {"status": speech_status, "reason": (
            "contract-onset non fourni : dérive non mesurable."
            if speech_status == "unchecked" else transcript.get("reason"))}
    else:
        verdict = production.apply_speech_onset_check(
            {"status": "ok" if transcript.get("status") == "ok" else "unknown"},
            transcript, [{"start_sec": float(contract_onset)}], timing_check=False)
        speech_status = verdict.get("status") or "unknown"
        report["speech"] = {
            "status": speech_status,
            "speech_onset_sec": verdict.get("speech_onset_sec"),
            "contract_onset_sec": verdict.get("contract_onset_sec"),
            "reason": verdict.get("reason", ""),
        }

    report["verdict"] = summarize(text_qc.get("status"), speech_status)
    return report


def _print_human(report):
    text = report["text_qc"]
    speech = report.get("speech") or {}
    lines = [
        f"Prise : {report['take']}",
        f"Verdict : {report['verdict']}",
        "",
        "— Texte à l'image (OCR) —",
        f"  statut : {text.get('status')}",
        f"  motif  : {text.get('reason', '')}",
    ]
    if report["allowed_display"]:
        lines.append(f"  chiffres diégétiques déclarés : {', '.join(report['allowed_display'])}")
    for hit in text.get("hits") or []:
        lines.append(f"  image {hit.get('sample')} : texte nouveau « {' '.join(hit.get('words') or [])} »")
    if text.get("limits"):
        lines.append(f"  limites : {text['limits']}")
    lines += [
        "",
        "— Parole (Whisper) —",
        f"  statut : {speech.get('status')}",
    ]
    if speech.get("speech_onset_sec") is not None:
        lines.append(f"  début mesuré : {speech['speech_onset_sec']} s "
                     f"(contrat : {speech.get('contract_onset_sec')} s)")
    if speech.get("reason"):
        lines.append(f"  motif : {speech['reason']}")
    transcript = report.get("transcription") or {}
    if transcript.get("environment_failure"):
        lines.append("  environnement : checkpoint/Whisper indisponible — contrôle à refaire.")
    lines += [
        "",
        "Rappel : l'OCR ne vérifie ni l'identité, ni le lip-sync, ni un sous-titre",
        "incrusté dès la première image. La validation finale est humaine.",
    ]
    print("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Revue humaine d'une prise native : sous-titres parasites et synchro parole.")
    parser.add_argument("take", type=Path, help="Chemin du MP4 de la prise à contrôler.")
    parser.add_argument("--allowed", action="append", default=[],
                        help="Chiffre/valeur diégétique autorisée à l'image (répétable). "
                             "Ex. --allowed 000 --allowed 001 --allowed 002.")
    parser.add_argument("--language", default="fr", help="Langue de la prise (défaut : fr).")
    parser.add_argument("--contract-onset", type=float, default=None,
                        help="Début déclaré de la première réplique (s) pour mesurer la dérive.")
    parser.add_argument("--checkpoint", default=None,
                        help="Checkpoint ASR local (défaut : réglage studio_asr_checkpoint).")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    arguments = parser.parse_args(argv)

    if not arguments.take.is_file():
        print(json.dumps({"error": f"Prise introuvable : {arguments.take}"}), file=sys.stderr)
        return 2

    checkpoint = arguments.checkpoint
    if checkpoint is None:
        from calliope.config import settings
        checkpoint = settings.studio_asr_checkpoint

    report = qc_take(
        arguments.take, allowed=tuple(arguments.allowed), language=arguments.language,
        contract_onset=arguments.contract_onset, checkpoint=checkpoint)

    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    # Code de sortie : 0 = contrôles auto OK (validation humaine requise),
    # 1 = rejet ou à revoir (décision humaine à trancher), jamais un faux succès.
    return 0 if report["verdict"] == PROVISIONAL_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
