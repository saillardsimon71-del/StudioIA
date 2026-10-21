"""Phase 3 — Script exécutable Windows/Linux pour la première prise réelle (Fruit 5s).

Modes d'exécution :
1. Préparation & préflight SANS GPU (aucune dépense, exécutable partout) :
     python scripts/phase3_fruit_take.py --prepare [--image chemin/vers/fruit.png]

2. Génération réelle d'UNE SEULE prise de 5s sur Modal APRÈS autorisation explicite :
     python scripts/phase3_fruit_take.py --image chemin/vers/fruit.png --max-eur 0.50 --authorize-gpu

Règle stricte : aucun appel GPU n'est jamais lancé sans `--authorize-gpu` ET confirmation
explicite, un seul plan est rendu sans retry automatique, et le MP4 obtenu passe par
l'inspection média, l'OCR Tesseract et la transcription Whisper CPU avant revue humaine.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from api.app import build_phase3_template_project  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.export import export_project_mp4  # noqa: E402
from core.ltx_prompt import compile_shot_prompt, validate_ltx_prompt  # noqa: E402
from core.storage import StudioRepository  # noqa: E402
from engines.video import LtxModalEngine  # noqa: E402
from qc.runner import run_take_qc  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Studio IA V2 — Phase 3 : Première prise réelle (Fruit anthropomorphe 5s)"
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Prépare et vérifie le projet Phase 3, l'estimation de coût et l'état Modal SANS lancer le GPU.",
    )
    parser.add_argument(
        "--image",
        type=str,
        default="",
        help="Chemin vers l'image ChatGPT 9:16 importée du fruit anthropomorphe (PNG/JPG/WEBP).",
    )
    parser.add_argument(
        "--max-eur",
        type=float,
        default=0.50,
        help="Plafond maximal autorisé en EUR pour cette prise unique (défaut : 0.50 €).",
    )
    parser.add_argument(
        "--authorize-gpu",
        action="store_true",
        help="Autorise explicitement UNE génération GPU Modal pour la prise Phase 3.",
    )
    parser.add_argument(
        "--yes-i-confirm",
        action="store_true",
        help="Confirme l'accord humain en mode scripté non interactif.",
    )
    args = parser.parse_args()

    settings = load_settings()
    settings.ensure_dirs()
    repo = StudioRepository(settings.db_path)
    engine = LtxModalEngine(settings, repo=repo)

    project = repo.get_project("phase3-fruit-5s")
    if project is None:
        project = build_phase3_template_project("phase3-fruit-5s")
        repo.save_project(project)

    shot = project.shots[0]

    if args.image:
        src_img = Path(args.image).expanduser().resolve()
        if not src_img.is_file():
            print(f"[ERREUR] Image introuvable : {src_img}", file=sys.stderr)
            return 2
        with Image.open(src_img) as im:
            im.verify()
        with Image.open(src_img) as im2:
            w, h = im2.size
        dest = settings.imports_dir / f"phase3_{src_img.name}"
        shutil.copy2(src_img, dest)
        repo.add_asset(
            kind="character",
            name="Inspecteur Zeste (Phase 3)",
            description=project.characters[0].description,
            file_path=dest,
            width=w,
            height=h,
        )
        project.characters[0].image_ref = str(dest.resolve())
        shot.image_ref = str(dest.resolve())
        shot.image_validated = True
        repo.save_project(project)
        print(f"[OK] Image importée et validée : {dest} ({w}x{h})")

    shot.ltx_prompt = compile_shot_prompt(project, shot)
    prompt_check = validate_ltx_prompt(shot.ltx_prompt, shot_id=shot.id)
    cost = engine.estimate_cost_breakdown(shot)
    modal_state = engine.check_modal_status()

    print("\n" + "=" * 72)
    print("STUDIO IA V2 — DIAGNOSTIC PRÉ-RENDU PHASE 3 (FRUIT ANTHROPOMORPHE 5s)")
    print("=" * 72)
    print(f"Projet          : {project.id} — {project.title}")
    print(f"Base SQLite V2  : {settings.db_path}")
    print(f"Personnage      : {project.characters[0].name} ({project.characters[0].id})")
    print(f"Dialogue FR     : « {shot.dialogue[0].text} »")
    print(f"Durée demandée  : {shot.duration_s:.1f} s -> {cost['frames']} frames @ 24 fps ({cost['engine_duration_s']} s)")
    print(f"Prompt LTX      : {prompt_check['word_count']} / {prompt_check['max_words']} mots (CONFORME)")
    missing_img = r"NON IMPORTÉE — fournissez --image chemin\image.png"
    print(f"Image initiale  : {shot.image_ref or missing_img}")
    print(f"État Modal      : {'CONNECTÉ' if modal_state['ok'] else 'NON CONNECTÉ'} — {modal_state['message']}")
    print("-" * 72)
    print("ESTIMATION DE COÛT MODAL (PARAMÈTRES RÉELS FOURNISSEUR) :")
    print(f"  Profil GPU    : {cost['gpu_label']} ({cost['usd_per_sec']} $/s | {cost['eur_per_sec']} €/s)")
    print(f"  Calibration   : {cost['calibration_source']}")
    if cost["estimated_cost_eur"] is not None:
        print(f"  Chaud (min)   : ~{cost['warm_min_eur']:.4f} € ({cost['warm_compute_s']} s)")
        print(f"  Attendu froid : ~{cost['cold_expected_eur']:.4f} € ({cost['expected_compute_s']} s)  [~{cost['estimated_cost_usd']:.4f} $]")
        print(f"  Pire cas      : ~{cost['worst_case_eur']:.4f} € ({cost['worst_compute_s']} s)")
    else:
        print("  Coût estimé   : None (tarif GPU non configuré — aucun chiffre inventé)")
    print(f"  Plafond actif : {args.max_eur:.2f} €")
    for unc in cost["uncertainties"]:
        print(f"  * Incertitude : {unc}")
    print("=" * 72)

    if not args.authorize_gpu or args.prepare:
        print(
            "\n[ARRÊT DE SÉCURITÉ OBLIGATOIRE AVANT GPU]\n"
            "Aucun appel GPU n'a été lancé (dépense GPU = 0,00 €).\n"
            "Pour exécuter la prise réelle de 5s sur Windows après votre accord :\n"
            "  studio-v2\\phase3_run_authorized.bat chemin\\vers\\fruit_chatgpt.png\n"
        )
        return 0

    if not shot.image_ref or not Path(shot.image_ref).is_file():
        print(
            "[BLOCAGE AVANT GPU] Importez d'abord votre image ChatGPT avec `--image chemin\\image.png`.",
            file=sys.stderr,
        )
        return 2

    if not args.yes_i_confirm:
        answer = input(
            f"\nConfirmez-vous le lancement payant d'UNE prise de 5s (plafond {args.max_eur:.2f} €) ? [tapez OUI] : "
        ).strip()
        if answer != "OUI":
            print("Génération annulée par l'opérateur (0,00 € dépensé).")
            return 0

    engine.arm_single_take(
        project_id=project.id,
        shot_id=shot.id,
        authorized_by_human=True,
        max_budget_eur=args.max_eur,
        aspect_ratio=project.aspect_ratio,
    )
    print("\n[GPU] Envoi de l'unique prise de 5s à Modal (`studio-ia-ltx.render_clip`)...")
    rendered_mp4 = engine.render(shot)
    shot.rendered_video_ref = rendered_mp4
    shot.human_validated = False
    repo.save_project(project)

    qc = run_take_qc(Path(rendered_mp4), shot, settings)
    takes = repo.list_takes(project.id, shot.id)
    if takes:
        repo.update_take_qc(takes[0]["id"], qc=qc)

    exported_mp4, export_qc = export_project_mp4(
        project, settings, output_path=settings.exports_dir / "phase3-fruit-5s-vertical.mp4"
    )
    repo.record_export(
        project_id=project.id,
        output_path=exported_mp4,
        duration_s=export_qc["media"]["duration_s"],
        width=export_qc["media"]["width"],
        height=export_qc["media"]["height"],
        qc=export_qc,
    )

    report_path = settings.exports_dir / "phase3-report.json"
    report = {
        "take_mp4": rendered_mp4,
        "vertical_export_mp4": str(exported_mp4),
        "take_record": repo.list_takes(project.id, shot.id)[0] if takes else None,
        "take_qc": qc,
        "export_qc": export_qc,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n[SUCCÈS TECHNIQUE — EN ATTENTE DE VISIONNAGE ET VALIDATION HUMAINE]")
    print(f"  Prise native LTX : {rendered_mp4}")
    print(f"  Export 1080x1920 : {exported_mp4}")
    print(f"  Rapport complet  : {report_path}")
    if qc["warnings"]:
        print("  Avertissements à contrôler visuellement :")
        for w in qc["warnings"]:
            print(f"    - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
