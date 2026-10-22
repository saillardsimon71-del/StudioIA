"""The offline patcher is scoped, parseable, reversible and idempotent."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
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
