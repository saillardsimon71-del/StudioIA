"""Point d'entrée CLI du service Meta AI Studio IA.

    python -m studioia_metaai serve        # démarre le serveur (défaut)
    python -m studioia_metaai diagnose     # inspecte l'interface, sans générer
    python -m studioia_metaai selfcheck    # tests de provenance hors ligne
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studioia_metaai", description="Service Meta AI isolé de Studio IA V2")
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "diagnose", "selfcheck"))
    parser.add_argument("--headed", action="store_true", help="Affiche la fenêtre Chromium (diagnostic)")
    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import main as serve_main

        serve_main()
        return 0
    if args.command == "diagnose":
        from .diagnose import run_diagnose

        print(json.dumps(run_diagnose(headed=args.headed), ensure_ascii=False, indent=2))
        return 0
    from .diagnose import run_selfcheck

    result = run_selfcheck()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
