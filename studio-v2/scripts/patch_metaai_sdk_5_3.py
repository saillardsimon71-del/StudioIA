"""Apply the Studio IA isolation fix to an installed metaai-sdk 5.3.0 package.

This patcher is intentionally limited to four Python modules in the imported
metaai_api package. It never reads, writes, or prints Meta cookies, browser
profiles, Studio projects, or generated assets.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import importlib
import importlib.metadata
from pathlib import Path
import shutil
import sys
import textwrap

MARKER = "STUDIOIA_ISOLATED_GENERATION_V1"


AGENT_NEW_CHAT = r'''def new_chat(self, timeout: int = 20) -> str:
    """Open and confirm a blank Meta conversation; never swallow failure."""
    if not self._ready:
        self.setup()
    before_id = self._get_conversation_id()
    try:
        self._run("find", "role", "link", "click", "--name", "New chat", timeout=10)
    except Exception as exc:
        raise ConnectionError("Meta AI n'a pas confirmé l'ouverture d'une nouvelle conversation.") from exc

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = self._run(
                "eval",
                "(() => { const r=document.querySelector('main')||document.body;"
                "const a=r.querySelectorAll('[class*=assistant-message]').length;"
                "const u=r.querySelectorAll('[class*=user-message]').length;"
                "const i=r.querySelector('[role=textbox],textarea,[contenteditable=true]');"
                "return {a,u,ready:!!i}; })()",
                json_output=True, timeout=10,
            )
            state = result.get("data", {}).get("result", {}) if isinstance(result, dict) else {}
            current_id = self._get_conversation_id()
            id_changed = current_id != before_id
            first_blank_page = not before_id and not current_id
            if (isinstance(state, dict) and state.get("ready") and state.get("a") == 0
                    and state.get("u") == 0 and (id_changed or first_blank_page)):
                self._studioia_previous_conversation_id = before_id
                self._studioia_fresh_chat_armed = True
                return current_id
        except Exception:
            pass
        time.sleep(0.5)
    raise ConnectionError("Meta AI n'a pas confirmé une conversation vide dans le délai imparti.")'''


PLAYWRIGHT_NEW_CHAT = r'''def new_chat(self, timeout: int = 20) -> str:
    """Open and confirm a blank Meta conversation; never swallow failure."""
    if not self._ready:
        self.setup()
    before_id = self._get_conversation_id()
    try:
        link = self._page.query_selector('a:has-text("New chat")')
        if not link or not link.is_visible():
            raise RuntimeError("lien New chat absent")
        link.click(timeout=5000)
    except Exception as exc:
        raise ConnectionError("Meta AI n'a pas confirmé l'ouverture d'une nouvelle conversation.") from exc

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = self._response_dom_state()
            current_id = self._get_conversation_id()
            id_changed = current_id != before_id
            first_blank_page = not before_id and not current_id
            if (state.get("input_ready") and state.get("assistant_count") == 0
                    and state.get("user_count") == 0 and (id_changed or first_blank_page)):
                self._studioia_previous_conversation_id = before_id
                self._studioia_fresh_chat_armed = True
                return current_id
        except Exception:
            pass
        time.sleep(0.5)
    raise ConnectionError("Meta AI n'a pas confirmé une conversation vide dans le délai imparti.")'''


DOM_STATE_JS = r'''(() => {
  const root = document.querySelector('main') || document.body;
  const assistants = Array.from(root.querySelectorAll('[class*="assistant-message"]'));
  const users = Array.from(root.querySelectorAll('[class*="user-message"]'));
  const input = root.querySelector('[role="textbox"], textarea, [contenteditable="true"]');
  const last = assistants[assistants.length - 1] || null;
  let scope = last;
  if (scope) {
    while (scope.parentElement && scope.parentElement !== root) {
      const parent = scope.parentElement;
      if (parent.querySelectorAll('[class*="assistant-message"]').length > 1) break;
      scope = parent;
    }
  }
  const excluded = /(avatar|profile|logo|icon|account|user menu|profile picture|user-photo|user photo|brandmark|wordmark)/i;
  const collectMedia = boundary => Array.from(boundary.querySelectorAll('img[src], a[href]'))
    .filter(el => !el.closest('nav, header, aside, [role="navigation"], [data-testid*="avatar"]'))
    .filter(el => {
      const attrs = [];
      let current = el;
      while (current && current !== boundary) {
        attrs.push(current.alt, current.getAttribute?.('aria-label'), current.title,
          current.className, current.getAttribute?.('data-testid'));
        for (const childImage of current.querySelectorAll?.('img') || []) {
          attrs.push(childImage.alt, childImage.getAttribute('aria-label'), childImage.title,
            childImage.className, childImage.getAttribute('data-testid'));
        }
        current = current.parentElement;
      }
      return !excluded.test(attrs.join(' '));
    })
    .filter(el => {
      if (el.tagName !== 'IMG') return true;
      const w = el.naturalWidth || el.width || 0;
      const h = el.naturalHeight || el.height || 0;
      return el.complete === true && w > 256 && h > 256;
    })
    .filter(el => !el.getClientRects || el.getClientRects().length > 0)
    .map(el => el.currentSrc || el.src || el.href || el.getAttribute('src') || el.getAttribute('href'))
    .filter(Boolean);
  const urls = scope ? collectMedia(scope) : [];
  const existing_urls = collectMedia(root);
  const busy = !!root.querySelector('[aria-busy="true"], [role="progressbar"], [data-testid*="loading"], [class*="spinner"], [class*="typing"]') ||
    Array.from(root.querySelectorAll('button,[role="button"]')).some(b =>
      /stop generating|stop response|cancel generation/i.test(
        [b.getAttribute('aria-label'), b.title, b.textContent].join(' ')));
  return {urls, existing_urls, assistant_count: assistants.length, user_count: users.length,
    text: last?.textContent?.trim() || '', busy, input_ready: !!input,
    input_value: input?.value ?? input?.innerText ?? ''};
})()'''


AGENT_STATE_METHOD = f'''def _response_dom_state(self) -> Dict[str, Any]:
    """Return only media tied to the latest assistant response container."""
    try:
        result = self._run("eval", {DOM_STATE_JS!r}, json_output=True, timeout=10)
        data = result.get("data", {{}}).get("result", {{}}) if isinstance(result, dict) else {{}}
        if not isinstance(data, dict):
            raise ValueError("DOM response state has an unexpected shape")
        data["urls"] = [u for u in data.get("urls", [])
                        if isinstance(u, str) and is_media_url(u)]
        data["existing_urls"] = [u for u in data.get("existing_urls", [])
                                 if isinstance(u, str) and is_media_url(u)]
        return data
    except Exception as exc:
        raise ConnectionError("Impossible de vérifier la réponse Meta AI courante.") from exc'''


PLAYWRIGHT_STATE_METHOD = f'''def _response_dom_state(self) -> Dict[str, Any]:
    """Return only media tied to the latest assistant response container."""
    try:
        data = self._page.evaluate({DOM_STATE_JS!r})
        if not isinstance(data, dict):
            raise ValueError("DOM response state has an unexpected shape")
        data["urls"] = [u for u in data.get("urls", [])
                        if isinstance(u, str) and is_media_url(u)]
        data["existing_urls"] = [u for u in data.get("existing_urls", [])
                                 if isinstance(u, str) and is_media_url(u)]
        return data
    except Exception as exc:
        raise ConnectionError("Impossible de vérifier la réponse Meta AI courante.") from exc'''


AGENT_SEND_MESSAGE = r'''def send_message(self, prompt: str, timeout: int = 120,
                     thinking_mode: bool = False,
                     require_new_conversation: bool = False) -> Dict[str, Any]:
    """Send a prompt and accept only media from its fresh assistant turn."""
    if not self._ready:
        self.setup()
    if require_new_conversation and not getattr(self, "_studioia_fresh_chat_armed", False):
        raise ConnectionError("La conversation Meta AI n'a pas été confirmée comme nouvelle.")

    if thinking_mode:
        self._switch_mode("Thinking")
    else:
        self._switch_mode("Instant")

    baseline = self._response_dom_state()
    baseline_urls = set(baseline.get("urls", [])) | set(baseline.get("existing_urls", []))
    baseline_count = int(baseline.get("assistant_count", 0))
    snapshot = self._run("snapshot", "-i", json_output=True, timeout=15)
    input_ref = self._find_input(snapshot)
    if not input_ref:
        raise ConnectionError("Le champ de saisie Meta AI est introuvable.")
    self._run("click", input_ref)
    self._run("fill", input_ref, prompt, timeout=10)
    typed = self._run(
        "eval",
        "(() => {const e=document.activeElement; return e?.value ?? e?.innerText ?? '';})()",
        json_output=True, timeout=10,
    )
    typed_value = typed.get("data", {}).get("result", "") if isinstance(typed, dict) else ""
    if typed_value != prompt:
        raise ConnectionError("Meta AI n'a pas confirmé la saisie exacte du prompt courant.")
    self._run("press", "Enter")
    if require_new_conversation:
        self._studioia_fresh_chat_armed = False

    urls, text = self._wait_for_response(timeout, baseline_urls, baseline_count)
    conv_id = self._get_conversation_id()
    if not conv_id or conv_id == getattr(self, "_studioia_previous_conversation_id", None):
        raise ConnectionError("Meta AI n'a pas confirmé l'identifiant de la conversation courante.")
    return {"urls": urls, "text": text, "conversation_id": conv_id}'''


PLAYWRIGHT_SEND_MESSAGE = r'''def send_message(self, prompt: str, timeout: int = 120,
                     thinking_mode: bool = False,
                     require_new_conversation: bool = False) -> Dict[str, Any]:
    """Send a prompt and accept only media from its fresh assistant turn."""
    if not self._ready:
        self.setup()
    if require_new_conversation and not getattr(self, "_studioia_fresh_chat_armed", False):
        raise ConnectionError("La conversation Meta AI n'a pas été confirmée comme nouvelle.")
    if thinking_mode:
        self._switch_mode("Thinking")
    else:
        self._switch_mode("Instant")
    self._dismiss_overlays()

    baseline = self._response_dom_state()
    baseline_urls = set(baseline.get("urls", [])) | set(baseline.get("existing_urls", []))
    baseline_count = int(baseline.get("assistant_count", 0))
    selectors = ['textarea[data-testid="composer-input"]',
                 'div[data-testid="composer-input"] [contenteditable]',
                 'textarea[placeholder*="Ask Meta"]', '[role="textbox"]', 'textarea']
    composer = None
    for selector in selectors:
        try:
            candidate = self._page.query_selector(selector)
            if candidate and candidate.is_visible():
                composer = candidate
                break
        except Exception:
            continue
    if composer is None:
        raise ConnectionError("Le champ de saisie Meta AI est introuvable.")
    composer.scroll_into_view_if_needed(timeout=5000)
    composer.fill(prompt, timeout=5000)
    try:
        entered = composer.input_value()
    except Exception:
        entered = composer.inner_text()
    if entered != prompt:
        raise ConnectionError("Meta AI n'a pas confirmé la saisie exacte du prompt courant.")
    composer.press("Enter")
    if require_new_conversation:
        self._studioia_fresh_chat_armed = False

    urls, text = self._wait_for_response(timeout, baseline_urls, baseline_count)
    conv_id = self._get_conversation_id()
    if not conv_id or conv_id == getattr(self, "_studioia_previous_conversation_id", None):
        raise ConnectionError("Meta AI n'a pas confirmé l'identifiant de la conversation courante.")
    return {"urls": urls, "text": text, "conversation_id": conv_id}'''


WAIT_METHOD = r'''def _wait_for_response(self, timeout: int, baseline_urls: Optional[Set[str]] = None,
                          baseline_assistant_count: int = 0) -> Tuple[List[str], str]:
    """Wait for a stable, completed response and current-turn media only."""
    baseline = baseline_urls or set()
    stable_urls: Optional[Tuple[str, ...]] = None
    stable_ticks = 0
    last_text = ""
    text_ticks = 0
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(2)
        state = self._response_dom_state()
        if state.get("assistant_count", 0) <= baseline_assistant_count:
            continue
        text = str(state.get("text") or "").strip()
        if text and text == last_text:
            text_ticks += 1
        elif text:
            last_text = text
            text_ticks = 1
        else:
            last_text = ""
            text_ticks = 0
        candidates = tuple(sorted({u for u in state.get("urls", []) if u not in baseline}))
        if candidates and not state.get("busy"):
            if candidates == stable_urls:
                stable_ticks += 1
            else:
                stable_urls = candidates
                stable_ticks = 1
            if stable_ticks >= 3 and (not text or text_ticks >= 3):
                conversation_id = self._get_conversation_id()
                previous = getattr(self, "_studioia_previous_conversation_id", None)
                if not conversation_id or conversation_id == previous:
                    raise ConnectionError("Le média Meta AI n'est associé à aucune conversation fraîche confirmée.")
                return list(candidates), text
        else:
            stable_urls = None
            stable_ticks = 0

        if text and not state.get("busy") and text_ticks >= 3:
            return [], text
    raise ConnectionError("Meta AI n'a pas terminé une réponse image attribuable au prompt courant.")'''


GENERATION_METHOD = r'''def generate_image(self, prompt: str, timeout: int = 120,
                       new_conversation: bool = True) -> Dict[str, Any]:
        """Generate only after confirming a fresh chat and completed response."""
        if not self.browser:
            raise GenerationError("Browser backend is required for image generation")
        if not new_conversation:
            raise GenerationError("Chaque génération Studio IA exige une conversation Meta AI neuve.")
        self.browser.new_chat()
        result = self.browser.send_message(
            prompt, timeout=timeout, require_new_conversation=True,
        )
        urls = result.get("urls", [])
        text = result.get("text", "")
        conversation_id = result.get("conversation_id")
        if not urls or not conversation_id:
            raise GenerationError(
                "Aucune image attribuable à une réponse Meta AI fraîche n'a été confirmée.",
                detail=f"Prompt: {prompt!r}, text: {text[:200]}",
            )
        return {
            "success": True, "prompt": prompt, "image_urls": urls, "images": urls,
            "conversation_id": conversation_id, "raw": result,
        }'''


MAIN_GENERATE_METHOD = r'''def generate_image_new(
        self,
        prompt: str,
        orientation: str = "VERTICAL",
        num_images: int = 1,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate images using a newly confirmed conversation only."""
        timeout = kwargs.get("timeout", 120)
        new_conversation = kwargs.get("new_conversation", True)
        self._get_browser()
        return self.generation_api.generate_image(
            prompt, timeout=timeout, new_conversation=new_conversation,
        )'''


IMAGE_REQUEST_CLASS = r'''class ImageRequest(BaseModel):
    prompt: str
    new_conversation: bool = True
    request_id: Optional[str] = None
    media_ids: Optional[list] = None
    attachment_metadata: Optional[dict] = None
    orientation: Optional[str] = None
    num_images: int = Field(1, ge=1, le=4)'''


IMAGE_ROUTE = r'''@app.post("/image")
async def generate_image(req: ImageRequest, _=Depends(verify_api_key)):
    """Generate one prompt in a serialized, independently confirmed chat."""
    start = time.time()
    try:
        def _generate_serially():
            with _studioia_image_generation_lock:
                client = client_manager.get_client()
                return client.generate_image_new(
                    req.prompt,
                    orientation=req.orientation or "VERTICAL",
                    num_images=req.num_images,
                    media_ids=req.media_ids,
                    attachment_metadata=req.attachment_metadata,
                    timeout=120,
                    new_conversation=True,
                )
        result = await asyncio.to_thread(_generate_serially)
        conversation_id = result.get("conversation_id")
        image_urls = result.get("image_urls", [])
        if not isinstance(conversation_id, str) or not conversation_id.strip() or not image_urls:
            raise GenerationError("La réponse Meta AI n'est pas liée à une conversation et une image fraîche.")
        return {
            "success": True,
            "image_urls": image_urls,
            "conversation_id": conversation_id,
            "prompt": result.get("prompt", ""),
            "request_id": req.request_id,
            "elapsed_seconds": round(time.time()-start, 1),
        }
    except Exception as e:
        handle_error(e, 120)'''


def _find_node(tree: ast.Module, qualified: tuple[str, ...]) -> ast.AST:
    body = tree.body
    node: ast.AST | None = None
    for part in qualified:
        node = next((item for item in body if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                     and item.name == part), None)
        if node is None:
            raise RuntimeError(f"Expected source target not found: {'.'.join(qualified)}")
        body = getattr(node, "body", [])
    return node


def _replace_callable(path: Path, qualified: tuple[str, ...], replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    node = _find_node(tree, qualified)
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise RuntimeError(f"Target is not a function: {'.'.join(qualified)}")
    start_line = min([node.lineno, *(d.lineno for d in node.decorator_list)]) if node.decorator_list else node.lineno
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[:start_line - 1])
    end = sum(len(line) for line in lines[:node.end_lineno])
    indent = len(lines[start_line - 1]) - len(lines[start_line - 1].lstrip())
    block = textwrap.dedent(replacement).strip("\n") + "\n"
    block = textwrap.indent(block, " " * indent)
    updated = source[:start] + block + source[end:]
    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8", newline="")


def _insert_method(path: Path, class_name: str, before_method: str, method_source: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    cls = _find_node(tree, (class_name,))
    anchor = next((item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and item.name == before_method), None)
    if anchor is None:
        raise RuntimeError(f"Insertion point not found: {class_name}.{before_method}")
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[:anchor.lineno - 1])
    block = textwrap.indent(textwrap.dedent(method_source).strip("\n") + "\n", "    ") + "\n"
    updated = source[:start] + block + source[start:]
    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8", newline="")


def _replace_class(path: Path, class_name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    node = _find_node(tree, (class_name,))
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[:node.lineno - 1])
    end = sum(len(line) for line in lines[:node.end_lineno])
    indent = len(lines[node.lineno - 1]) - len(lines[node.lineno - 1].lstrip())
    block = textwrap.dedent(replacement).strip("\n") + "\n"
    block = textwrap.indent(block, " " * indent)
    updated = source[:start] + block + source[end:]
    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8", newline="")


def _replace_agent_browser(package: Path) -> None:
    path = package / "browser.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return
    _replace_callable(path, ("BrowserBackend", "new_chat"), AGENT_NEW_CHAT)
    _insert_method(path, "BrowserBackend", "_wait_for_response", AGENT_STATE_METHOD)
    _replace_callable(path, ("BrowserBackend", "send_message"), AGENT_SEND_MESSAGE)
    _replace_callable(path, ("BrowserBackend", "_wait_for_response"), WAIT_METHOD)
    source = path.read_text(encoding="utf-8")
    source = source.replace('class BrowserBackend:\n', f'class BrowserBackend:\n    """{MARKER}"""\n', 1)
    path.write_text(source, encoding="utf-8", newline="")


def _replace_playwright(package: Path) -> None:
    path = package / "playwright_backend.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return
    _replace_callable(path, ("PlaywrightBackend", "new_chat"), PLAYWRIGHT_NEW_CHAT)
    _insert_method(path, "PlaywrightBackend", "_wait_for_response", PLAYWRIGHT_STATE_METHOD)
    _replace_callable(path, ("PlaywrightBackend", "send_message"), PLAYWRIGHT_SEND_MESSAGE)
    _replace_callable(path, ("PlaywrightBackend", "_wait_for_response"), WAIT_METHOD)
    source = path.read_text(encoding="utf-8")
    source = source.replace('class PlaywrightBackend:\n', f'class PlaywrightBackend:\n    """{MARKER}"""\n', 1)
    path.write_text(source, encoding="utf-8", newline="")


def _ensure_asyncio_import(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if "import asyncio\n" not in source:
        source = source.replace("import logging\n", "import asyncio\nimport logging\n", 1)
    if "_studioia_image_generation_lock" not in source:
        source = source.replace("client_manager = ClientManager()\n", "client_manager = ClientManager()\n\n# Studio IA: serialize requests that share the persistent browser session.\n_studioia_image_generation_lock = threading.Lock()\n", 1)
    path.write_text(source, encoding="utf-8", newline="")


def _append_marker(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER not in source:
        path.write_text(source.rstrip() + f"\n\n# {MARKER}\n", encoding="utf-8", newline="")


def patch(package: Path, backup_root: Path) -> None:
    required = ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py")
    for name in required:
        if not (package / name).is_file():
            raise RuntimeError(f"Package module missing; refusing partial patch: {name}")
    if MARKER in (package / "browser.py").read_text(encoding="utf-8"):
        if all(MARKER in (package / name).read_text(encoding="utf-8")
               for name in ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py")):
            print("Studio IA MetaAI patch already installed; no files changed.")
            return
        raise RuntimeError("Partial Studio IA patch detected; restore backup before retrying.")

    backup = backup_root / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True, exist_ok=False)
    touched = list(required)
    for name in touched:
        shutil.copy2(package / name, backup / name)

    try:
        _replace_agent_browser(package)
        _replace_playwright(package)
        _replace_callable(package / "generation.py", ("GenerationAPI", "generate_image"), GENERATION_METHOD)
        _replace_callable(package / "main.py", ("MetaAI", "generate_image_new"), MAIN_GENERATE_METHOD)
        _replace_class(package / "api_server.py", "ImageRequest", IMAGE_REQUEST_CLASS)
        api = package / "api_server.py"
        _ensure_asyncio_import(api)
        _replace_callable(api, ("generate_image",), IMAGE_ROUTE)
        for name in touched:
            _append_marker(package / name)
            ast.parse((package / name).read_text(encoding="utf-8"), filename=str(package / name))
    except Exception:
        for name in touched:
            shutil.copy2(backup / name, package / name)
        raise
    print(f"Patched metaai-sdk 5.3.0 modules only. Backup: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable, help="Interpreter of the target MetaAIServiceV3 venv")
    parser.add_argument("--package-dir", help="Test/administrative override; defaults to imported metaai_api")
    args = parser.parse_args()
    try:
        if Path(args.python).resolve() != Path(sys.executable).resolve():
            raise RuntimeError("Run this script with the target venv's Python executable.")
        version = importlib.metadata.version("metaai-sdk")
        if version != "5.3.0":
            raise RuntimeError(f"Expected metaai-sdk==5.3.0, found {version}; no files changed.")
        if args.package_dir:
            package = Path(args.package_dir).resolve()
        else:
            module = importlib.import_module("metaai_api")
            package = Path(module.__file__).resolve().parent
        if not (package / "__init__.py").is_file():
            raise RuntimeError("Resolved package path is not metaai_api; no files changed.")
        backup_root = package / ".studioia-backup"
        patch(package, backup_root)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
