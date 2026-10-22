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
from typing import Any, Dict, List, Optional, Set, Tuple

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


def _v2_fixture(root: Path) -> Path:
    package = _upstream_fixture(root)
    for name in ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py"):
        path = package / name
        source = path.read_text(encoding="utf-8")
        if name == "browser.py":
            source = source.replace("class BrowserBackend:\n", f'class BrowserBackend:\n    """{patcher.PREVIOUS_MARKER}"""\n', 1)
        elif name == "playwright_backend.py":
            source = source.replace("class PlaywrightBackend:\n", f'class PlaywrightBackend:\n    """{patcher.PREVIOUS_MARKER}"""\n', 1)
        path.write_text(source.rstrip() + f"\n\n# {patcher.PREVIOUS_MARKER}\n", encoding="utf-8")
    return package


def test_patch_v2_changes_only_playwright_and_is_reversible_idempotent(tmp_path, capsys):
    package = _v2_fixture(tmp_path)
    before = {name: (package / name).read_bytes() for name in (
        "browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py",
    )}
    backup_root = tmp_path / "backups"

    patcher.patch(package, backup_root)
    patched = {name: (package / name).read_text(encoding="utf-8") for name in before}
    assert patched["playwright_backend.py"] != before["playwright_backend.py"].decode("utf-8")
    assert patcher.MARKER in patched["playwright_backend.py"]
    assert patcher.PREVIOUS_MARKER not in patched["playwright_backend.py"]
    assert "_studioia_used_conversation_ids" in patched["playwright_backend.py"]
    for name in set(before) - {"playwright_backend.py"}:
        assert (package / name).read_bytes() == before[name]
    ast.parse(patched["playwright_backend.py"])

    backups = list(backup_root.iterdir())
    assert len(backups) == 1
    assert {path.name: path.read_bytes() for path in backups[0].glob("*.py")} == before
    after_once = (package / "playwright_backend.py").read_bytes()
    patcher.patch(package, backup_root)
    assert (package / "playwright_backend.py").read_bytes() == after_once
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


def test_french_home_already_blank_is_accepted_without_clicking():
    class Page:
        clicked = []

        def evaluate(self, script):
            return {"path": "/", "controls": ["Connecter"], "dialogs": 1}

        def get_by_role(self, *args, **kwargs):
            raise AssertionError("A blank home page should not search or click New chat")

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
    backend._get_conversation_id = lambda: "blank-home-thread"
    backend._response_dom_state = lambda: {
        "assistant_count": 0, "user_count": 0, "input_ready": True,
    }

    assert backend.new_chat() == "blank-home-thread"
    assert backend._studioia_previous_conversation_id is None
    assert backend._studioia_fresh_chat_armed is True
    assert backend._page.clicked == []


def test_new_chat_finds_sidebar_control_with_keyboard_shortcut_in_accessible_name():
    class Locator:
        def __init__(self, page):
            self.page = page

        def is_visible(self):
            return True

        def evaluate(self, script):
            self.page.structure_checked = True
            return True

        def click(self, timeout=None):
            self.page.clicked += 1
            self.page.conversation_id = "fresh-thread"
            self.page.state = {"assistant_count": 0, "user_count": 0, "input_ready": True}

    class Matches:
        def __init__(self, locator):
            self.locator = locator

        def count(self):
            return int(self.locator is not None)

        def nth(self, index):
            assert index == 0 and self.locator is not None
            return self.locator

    class Page:
        def __init__(self):
            self.clicked = 0
            self.structure_checked = False
            self.conversation_id = "old-thread"
            self.state = {"assistant_count": 1, "user_count": 1, "input_ready": True}
            self.accessible_name = "Nouvelle discussion (Ctrl+Maj+N)"

        def evaluate(self, script):
            return {"path": "/", "controls": ["Connecter"], "dialogs": 1}

        def get_by_role(self, role, name):
            matched = role == "button" and name.search(self.accessible_name)
            return Matches(Locator(self) if matched else None)

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
    backend._response_dom_state = lambda: backend._page.state

    assert backend.new_chat() == "fresh-thread"
    assert backend._studioia_fresh_chat_armed is True
    assert backend._page.clicked == 1
    assert backend._page.structure_checked is True


def test_post_generation_conversation_id_must_never_have_been_used():
    class Composer:
        def __init__(self, page):
            self.page = page

        def count(self):
            return 1

        def is_visible(self):
            return True

        def scroll_into_view_if_needed(self, timeout=None):
            pass

        def click(self, timeout=None):
            pass

        def fill(self, prompt, timeout=None):
            self.page.prompt = prompt

        def input_value(self):
            return self.page.prompt

        def press(self, key, timeout=None):
            assert key == "Enter"
            self.page.submitted = True

    class Page:
        def __init__(self):
            self.prompt = ""
            self.submitted = False

        def locator(self, selector):
            return Composer(self)

    namespace = {
        "Any": Any, "Dict": Dict, "re": re, "time": time,
        "logger": logging.getLogger("patcher-test"), "ConnectionError": RuntimeError,
    }
    source = "class Patched:\n" + textwrap.indent(patcher.PLAYWRIGHT_SEND_MESSAGE, "    ")
    exec(source, namespace)

    def backend_for(conversation_id, used_ids):
        backend = namespace["Patched"]()
        backend._ready = True
        backend._page = Page()
        backend._studioia_fresh_chat_armed = True
        backend._studioia_previous_conversation_id = "previous-thread"
        backend._studioia_used_conversation_ids = set(used_ids)
        backend._studioia_current_path = lambda: "/"
        backend._get_conversation_id = lambda: conversation_id
        backend._dismiss_overlays = lambda stage: None
        backend._response_dom_state = lambda: {
            "assistant_count": 0, "user_count": int(backend._page.submitted),
            "urls": [], "existing_urls": [],
        }
        backend._wait_for_response = lambda *args: (["https://scontent.xx.fbcdn.net/new.png"], "done")
        return backend

    used = backend_for("previously-generated-thread", {"previously-generated-thread"})
    with pytest.raises(RuntimeError, match="déjà utilisé"):
        used.send_message("un prompt", require_new_conversation=True)

    fresh = backend_for("brand-new-thread", {"previously-generated-thread"})
    result = fresh.send_message("un prompt", require_new_conversation=True)
    assert result["conversation_id"] == "brand-new-thread"
    assert fresh._studioia_used_conversation_ids == {"previously-generated-thread", "brand-new-thread"}


def test_overlay_inspection_stays_read_only_and_settings_route_fails_explicitly():
    assert ".click(" not in patcher.PLAYWRIGHT_DISMISS_OVERLAYS
    namespace = {"re": re, "logger": logging.getLogger("patcher-test"),
                 "ConnectionError": RuntimeError}
    source = "class Patched:\n" + textwrap.indent(patcher.PLAYWRIGHT_DISMISS_OVERLAYS, "    ")
    exec(source, namespace)

    class Page:
        def evaluate(self, script):
            return {"path": "/settings/apps", "controls": ["Connecter"], "dialogs": 1}

    backend = namespace["Patched"]()
    backend._page = Page()
    backend._studioia_current_path = lambda: "/settings/apps"
    with pytest.raises(RuntimeError, match="paramètres"):
        backend._dismiss_overlays(stage="test_french_overlay")
