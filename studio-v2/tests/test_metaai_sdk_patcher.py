"""The offline patcher is scoped, parseable, reversible and idempotent."""
from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path
import re
import sys
import textwrap
import time
from typing import Any, List, Optional, Set, Tuple

import pytest

PATCHER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "patch_metaai_sdk_5_3.py"
spec = importlib.util.spec_from_file_location("studioia_metaai_patcher", PATCHER_PATH)
patcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = patcher
spec.loader.exec_module(patcher)


def _upstream_fixture(root: Path) -> Path:
    package = root / "metaai_api"
    package.mkdir(parents=True)
    files = {
        "__init__.py": "__version__ = '5.3.0'\n",
        "browser.py": """from typing import Any, Dict, List, Optional, Set, Tuple
import time
from .exceptions import ConnectionError
from .utils import is_media_url
class BrowserBackend:
    def new_chat(self): pass
    def send_message(self, prompt, timeout=120, thinking_mode=False): pass
    def _wait_for_response(self, timeout): pass
""",
        "playwright_backend.py": """from typing import Any, Dict, List, Optional, Set, Tuple
import time
from .exceptions import ConnectionError
from .utils import is_media_url
class PlaywrightBackend:
    def new_chat(self): pass
    def send_message(self, prompt, timeout=120, thinking_mode=False): pass
    def _wait_for_response(self, timeout, existing_urls=None): pass
""",
        "generation.py": """from typing import Any, Dict
from .exceptions import GenerationError
class GenerationAPI:
    def generate_image(self, prompt, timeout=120): pass
""",
        "main.py": """from typing import Any, Dict
class MetaAI:
    def generate_image_new(self, prompt, orientation='VERTICAL', num_images=1, **kwargs): pass
""",
        "api_server.py": """import logging
import os
import threading
import time
from typing import Optional
from pydantic import BaseModel, Field
class ImageRequest(BaseModel):
    prompt: str
    new_conversation: bool = False
    num_images: int = Field(1, ge=1, le=4)
class ClientManager: pass
client_manager = ClientManager()
def verify_api_key(): pass
app = object()
async def generate_image(req: ImageRequest, _=None):
    return {}
""",
    }
    for name, content in files.items():
        (package / name).write_text(content, encoding="utf-8")
    return package


def test_patch_is_limited_to_five_modules_and_idempotent(tmp_path, capsys):
    package = _upstream_fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in package.glob("*.py")}
    backup_root = tmp_path / "backups"

    patcher.patch(package, backup_root)
    patched = {path.name: path.read_text(encoding="utf-8") for path in package.glob("*.py")}
    assert set(patched) == set(before)
    for name in ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py"):
        ast.parse(patched[name])
        assert patcher.MARKER in patched[name]
    backups = list(backup_root.iterdir())
    assert len(backups) == 1
    expected = {name: data for name, data in before.items() if name != "__init__.py"}
    assert {path.name: path.read_bytes() for path in backups[0].glob("*.py")} == expected

    patcher.patch(package, backup_root)
    assert len(list(backup_root.iterdir())) == 1
    assert "already installed" in capsys.readouterr().out


def test_waiter_ignores_baseline_and_accepts_stable_current_response(monkeypatch):
    namespace = {
        "Any": Any, "List": List, "Optional": Optional, "Set": Set, "Tuple": Tuple, "time": time,
        "is_media_url": lambda url: "scontent" in url,
        "logger": logging.getLogger("patcher-test"),
    }
    exec("class Waiter:\n" + "\n".join("    " + line for line in patcher.WAIT_METHOD.splitlines()), namespace)
    clock = [0.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    class Fake( namespace["Waiter"] ):
        def __init__(self, states):
            self.states = iter(states)

        def _response_dom_state(self):
            return next(self.states)

        def _get_conversation_id(self):
            return "fresh-thread"

    stale = "https://scontent.xx.fbcdn.net/old.png"
    current = "https://scontent.xx.fbcdn.net/current.png"
    state = {"assistant_count": 1, "text": "generated", "urls": [stale], "busy": False}
    with pytest.raises(Exception, match="terminé"):
        Fake([state] * 5)._wait_for_response(4, {stale}, 0)

    fresh = {"assistant_count": 1, "text": "generated", "urls": [current], "busy": False}
    urls, text = Fake([fresh] * 3)._wait_for_response(8, {stale}, 0)
    assert urls == [current]
    assert text == "generated"


def test_v1_patch_upgrades_in_place_and_remains_idempotent(tmp_path, capsys):
    package = _upstream_fixture(tmp_path)
    backup_root = tmp_path / "backups"
    patcher.patch(package, backup_root)
    for name in ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py"):
        path = package / name
        path.write_text(path.read_text(encoding="utf-8").replace(patcher.MARKER, patcher.PREVIOUS_MARKER),
                        encoding="utf-8")

    patcher.patch(package, backup_root)
    output = capsys.readouterr().out
    assert "Upgraded v1 to v2" in output
    assert len(list(backup_root.iterdir())) == 2
    for name in ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py"):
        source = (package / name).read_text(encoding="utf-8")
        ast.parse(source)
        assert patcher.MARKER in source
        assert patcher.PREVIOUS_MARKER not in source
    patcher.patch(package, backup_root)
    assert len(list(backup_root.iterdir())) == 2
    assert "already installed" in capsys.readouterr().out


def test_french_new_chat_is_exact_and_connecter_is_never_clicked():
    class Locator:
        def __init__(self, page, role, label):
            self.page, self.role, self.label = page, role, label

        def count(self):
            return int(self.role == "button" and self.label == "Nouvelle discussion")

        def is_visible(self):
            return True

        def click(self, timeout=None):
            self.page.clicked.append((self.role, self.label))
            self.page.conversation_id = "fresh-thread"

    class Page:
        def __init__(self):
            self.clicked = []
            self.conversation_id = "old-thread"

        def get_by_role(self, role, name, exact=False):
            assert exact is True
            return Locator(self, role, name)

        def evaluate(self, script):
            # A French sign-in control is present, but inspection is read-only.
            return {"path": "/", "controls": ["Connecter"], "dialogs": 1}

    namespace = {"re": re, "time": time, "logger": logging.getLogger("patcher-test"),
                 "ConnectionError": RuntimeError}
    source = "class Patched:\n" + textwrap.indent(patcher.PLAYWRIGHT_CURRENT_PATH, "    ") + "\n" + \
        textwrap.indent(patcher.PLAYWRIGHT_DISMISS_OVERLAYS, "    ") + "\n" + \
        textwrap.indent(patcher.PLAYWRIGHT_NEW_CHAT, "    ")
    exec(source, namespace)
    backend = namespace["Patched"]()
    backend._ready = True
    backend._page = Page()
    backend._studioia_current_path = lambda: "/"
    backend._get_conversation_id = lambda: backend._page.conversation_id
    backend._response_dom_state = lambda: {
        "assistant_count": 0, "user_count": 0, "input_ready": True,
    }

    conversation_id = backend.new_chat()
    assert conversation_id == "fresh-thread"
    assert backend._studioia_fresh_chat_armed is True
    assert backend._page.clicked == [("button", "Nouvelle discussion")]
    assert "Connecter" not in [label for _, label in backend._page.clicked]


def test_patch_has_no_generic_connect_click_and_image_send_does_not_force_mode():
    assert ".click(" not in patcher.PLAYWRIGHT_DISMISS_OVERLAYS
    assert ".get_by_text" not in patcher.PLAYWRIGHT_DISMISS_OVERLAYS
    assert "self._switch_mode(\"Instant\")" not in patcher.PLAYWRIGHT_SEND_MESSAGE
    assert "get_by_role(role, name=label, exact=True)" in patcher.PLAYWRIGHT_NEW_CHAT


def test_settings_applications_route_fails_explicitly_without_clicking_controls():
    namespace = {"re": re, "logger": logging.getLogger("patcher-test"),
                 "ConnectionError": RuntimeError}
    source = "class Patched:\n" + textwrap.indent(patcher.PLAYWRIGHT_DISMISS_OVERLAYS, "    ")
    exec(source, namespace)

    class Page:
        clicked = False

        def evaluate(self, script):
            return {"path": "/settings/apps", "controls": ["Connecter"], "dialogs": 1}

    backend = namespace["Patched"]()
    backend._page = Page()
    backend._studioia_current_path = lambda: "/settings/apps"
    with pytest.raises(RuntimeError, match="paramètres"):
        backend._dismiss_overlays(stage="test_french_overlay")
    assert backend._page.clicked is False
