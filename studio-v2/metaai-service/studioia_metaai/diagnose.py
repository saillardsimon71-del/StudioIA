"""Diagnostic de l'interface Meta AI, sans génération d'image.

Usage (depuis studio-v2\\metaai-service) :
    .\\.venv\\Scripts\\python.exe -m studioia_metaai diagnose
    .\\.venv\\Scripts\\python.exe -m studioia_metaai diagnose --headed

Le diagnostic ouvre Meta AI avec les cookies de l'environnement, vérifie la
conversation vierge, puis écrit une capture d'écran, le HTML et un bilan JSON
dans ``data/diagnose-<horodatage>/``. Aucun clic n'est effectué en dehors d'un
éventuel bandeau de consentement reconnu par libellé exact.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .browser import STATE_JS, VerifiedBrowser
from .server import SERVICE_ROOT, _cookies_from_env


def run_diagnose(*, headed: bool = False) -> dict:
    cookies = _cookies_from_env()
    if not cookies.get("datr") or not cookies.get("ecto_1_sess"):
        raise SystemExit("ERREUR : META_AI_DATR et META_AI_ECTO_1_SESS doivent être définis.")

    browser = VerifiedBrowser(cookies=cookies, headed=headed, page_ready_timeout_s=45)
    report: dict = {"cookies_configured": True, "headed": headed}
    try:
        browser.setup()
        url = browser.open_fresh_home()
        state = browser.read_state()
        report.update(
            {
                "ok": state.blank_conversation,
                "url": url,
                "path": state.path,
                "composer_ready": state.composer_ready,
                "assistant_count": state.assistant_count,
                "user_count": state.user_count,
                "dialogs": list(state.dialogs),
                "visible_buttons": list(state.visible_buttons)[:40],
                "media_before_send": len(state.media_snapshot),
                "raw": browser._page.evaluate(STATE_JS),
            }
        )
        out_dir = Path(os.getenv("META_AI_DATA", str(SERVICE_ROOT / "data"))) / time.strftime("diagnose-%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "page.html").write_text(browser._page.content(), encoding="utf-8")
        browser._page.screenshot(path=str(out_dir / "home.png"), full_page=True)
        report["artifacts"] = str(out_dir)
        return report
    finally:
        browser.close()


def run_selfcheck() -> dict:
    """Vérifie la logique de provenance hors ligne (aucun navigateur, aucun compte)."""
    import subprocess
    import sys

    tests_dir = Path(__file__).resolve().parents[1] / "tests"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(tests_dir)],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
    )
    return {"ok": proc.returncode == 0, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]}
