import argparse
import atexit
import os
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social.x_client import XError


class BrowserXClient:
    def __init__(self, data_dir, *, login=False):
        if os.environ.get("X_ALLOW_UNSUPPORTED_BROWSER") != "1":
            raise XError(
                "Transport navigateur désactivé : X interdit l'automation de son site. "
                "Utiliser l'API. Option expérimentale : X_ALLOW_UNSUPPORTED_BROWSER=1."
            )
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        self.context = self.runtime.chromium.launch_persistent_context(
            str(Path(data_dir) / "x_browser"), headless=not login, locale="en-US",
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        self.user_id = None
        self.username = None
        self.pending_media = None
        atexit.register(self.close)

    def close(self):
        if self.context is not None:
            self.context.close()
            self.runtime.stop()
            self.context = None

    def verify_account(self, username):
        self.page.goto("https://x.com/home", wait_until="domcontentloaded")
        try:
            profile = self.page.get_by_role("link", name="Profile", exact=True)
            profile.wait_for(state="visible")
            href = profile.get_attribute("href")
        except Exception:
            raise XError("Session X absente. Connexion interactive requise.") from None
        if href.rstrip("/").casefold() != "/" + username.lstrip("@").casefold():
            raise XError("La session navigateur ne correspond pas au compte configuré.")
        self.username = username.lstrip("@")
        return self.username

    def upload_video(self, path):
        path = Path(path).resolve()
        if not path.is_file():
            raise XError("Teaser vidéo introuvable.")
        self.pending_media = path
        return str(path)

    def create_post(self, text, *, made_with_ai=True, media_id=None):
        if not self.username:
            raise XError("Compte navigateur non vérifié.")
        selector = os.environ.get("X_BROWSER_AI_LABEL_SELECTOR", "").strip()
        if made_with_ai and not selector:
            raise XError("Le sélecteur du label IA doit être vérifié avant d'utiliser ce transport.")
        clicked = False
        try:
            self.page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
            editor = self.page.get_by_role("textbox", name="Post text", exact=True)
            editor.fill(text)
            if media_id:
                self.page.locator('input[type="file"]').set_input_files(str(self.pending_media))
            if made_with_ai:
                self.page.get_by_role("button", name="Content disclosure", exact=True).click()
                self.page.locator(selector).set_checked(True)
                self.page.get_by_role("button", name="Save", exact=True).click()
            button = self.page.get_by_role("button", name="Post", exact=True)
            button.wait_for(state="visible")
            if not button.is_enabled():
                raise XError("Le média n'est pas encore prêt dans le compositeur X.")
            clicked = True
            button.click()
            editor.wait_for(state="hidden", timeout=30000)
            self.page.goto(f"https://x.com/{self.username}", wait_until="domcontentloaded")
            self.page.get_by_role("article").first.wait_for(state="visible")
            normalized = " ".join(text.split())
            for article in self.page.get_by_role("article").all():
                if normalized not in " ".join(article.inner_text().split()):
                    continue
                for link in article.locator('a[href*="/status/"]').all():
                    match = re.fullmatch(rf"/{re.escape(self.username)}/status/(\d+)", link.get_attribute("href") or "")
                    if match:
                        return match.group(1)
            raise XError("Publication navigateur non confirmée.", uncertain=True)
        except XError:
            raise
        except Exception:
            raise XError("Interaction navigateur interrompue.", uncertain=clicked) from None


def main():
    parser = argparse.ArgumentParser(description="Connexion interactive au transport navigateur expérimental.")
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    client = BrowserXClient(args.data_dir, login=True)
    client.page.goto("https://x.com/login")
    input("Connecte-toi toi-même à X, puis appuie sur Entrée pour fermer le navigateur : ")
    client.close()


if __name__ == "__main__":
    main()
