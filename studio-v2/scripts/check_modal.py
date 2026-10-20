"""Vérification de disponibilité Modal SANS génération payante (hydrate uniquement).

Usage Windows :
    cd studio-v2
    .venv\\Scripts\\python.exe scripts\\check_modal.py
    .venv\\Scripts\\python.exe scripts\\check_modal.py --verbose
    .venv\\Scripts\\python.exe scripts\\check_modal.py --json

Sortie : OK (0) si `studio-ia-ltx.render_clip` est hydratable, sinon erreur (2).
Aucun GPU n'est alloué — coût 0,00 € (simple Function.from_name + hydrate).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import load_settings  # noqa: E402
from engines.video import LtxModalEngine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Vérifie la disponibilité Modal sans GPU (hydrate only).")
    parser.add_argument("--verbose", action="store_true", help="Affiche les détails complets.")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = parser.parse_args()

    settings = load_settings()
    engine = LtxModalEngine(settings)
    result = engine.check_modal_status()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2

    if result.get("ok"):
        print(f"[OK] {result['message']}")
        if args.verbose:
            print(f"  App: {settings.modal_app_name}  Function: {settings.modal_function_name}")
            print(f"  Profil GPU configuré: {settings.gpu_label} ({settings.gpu_usd_per_sec} $/s)")
        print("  → Prêt pour Fruit City. Aucun coût engagé (hydrate = 0,00 €).")
        return 0
    else:
        print(f"[ÉCHEC] {result['message']}", file=sys.stderr)
        if args.verbose:
            print(f"  App ciblée: {settings.modal_app_name}.{settings.modal_function_name}", file=sys.stderr)
            print("  Sur Windows, vérifiez: modal token info  et  modal app list", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
