import argparse
from pathlib import Path

import uvicorn
from calliope.main import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Studio IA généraliste")
    parser.add_argument("--port", type=int, default=8247)
    args = parser.parse_args()
    static = Path(__file__).resolve().parent / "calliope-web" / "build"
    if not (static / "index.html").is_file():
        raise SystemExit("Interface absente. Lancez launch.cmd pour la préparer.")
    uvicorn.run(create_app(static_dir=static), host="127.0.0.1", port=args.port)
