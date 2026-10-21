"""Point d'entrée serveur autonome de Studio IA V2."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from core.config import V1_FORBIDDEN_PORTS, load_settings  # noqa: E402


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Serveur Studio IA V2 (isolé de V1)")
    parser.add_argument("--host", default=settings.host, help="Hôte d'écoute (défaut: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=settings.port, help="Port HTTP (défaut: 8270)")
    args = parser.parse_args()

    if args.port in V1_FORBIDDEN_PORTS:
        raise SystemExit(
            f"Erreur : le port {args.port} appartient à Studio IA V1 (8247/8260). "
            "Utilisez un port distinct tel que 8270."
        )

    with socket.socket() as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError as exc:
            raise SystemExit(
                f"Demarrage refuse : {args.host}:{args.port} indisponible ({exc}). "
                "Une ancienne instance peut etre active. Arretez-la avant de relancer Studio IA V2."
            ) from exc
    os.environ["STUDIO_V2_HOST"] = args.host
    os.environ["STUDIO_V2_PORT"] = str(args.port)
    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
