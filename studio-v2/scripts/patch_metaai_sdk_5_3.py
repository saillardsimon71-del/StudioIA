"""Apply the Studio IA isolation fix to an installed metaai-sdk 5.3.0 package.

This patcher is intentionally limited to five Python modules in the imported
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

PREVIOUS_MARKER = "STUDIOIA_ISOLATED_GENERATION_V1"
MARKER = "STUDIOIA_ISOLATED_GENERATION_V2"
NEW_CHAT_LABELS = (
    "New chat", "New conversation", "Nouvelle discussion",
    "Nouvelle conversation", "Nouveau chat",
)


AGENT_NEW_CHAT = r'''def new_chat(self, timeout: int = 10) -> str:
    """Open and confirm a blank Meta conversation using exact localized labels."""
    if not self._ready:
        self.setup()
    before_id = self._get_conversation_id()
    clicked = False
    for role in ("button", "link"):
        for label in ("New chat", "New conversation", "Nouvelle discussion", "Nouvelle conversation", "Nouveau chat"):
            try:
                self._run("find", "role", role, "click", "--name", label, timeout=3)
                clicked = True
                break
            except Exception:
                continue
        if clicked:
            break
    if not clicked:
        raise ConnectionError("Contrôle exact de nouvelle conversation Meta AI introuvable (anglais/français).")

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
                json_output=True, timeout=5,
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
        except Exception as exc:
            raise ConnectionError("Impossible de vérifier la nouvelle conversation Meta AI.") from exc
        time.sleep(0.4)
    raise ConnectionError("Meta AI n'a pas confirmé une conversation vierge en 10 secondes.")'''


PLAYWRIGHT_SETUP = r'''def setup(self) -> None:
    """Start Meta AI browser without clicking page controls implicitly."""
    stage = "playwright_start"
    logger.info("[studioia-metaai] stage=%s outcome=start", stage)
    try:
        self._check_installed()
        self._playwright = sync_playwright().start()
        stage = "chromium_launch"
        logger.info("[studioia-metaai] stage=%s outcome=start", stage)
        self._browser = self._playwright.chromium.launch(
            headless=not self.headed,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        stage = "browser_context"
        context = self._browser.new_context(
            user_agent=self.user_agent, viewport={"width": 1280, "height": 720},
        )
        cookie_list = [{"name": name, "value": value, "domain": ".meta.ai", "path": "/"}
                       for name, value in self.cookies.items()]
        context.add_cookies(cookie_list)
        self._page = context.new_page()
        stage = "meta_navigation"
        logger.info("[studioia-metaai] stage=%s outcome=start", stage)
        self._page.goto("https://www.meta.ai/", wait_until="domcontentloaded", timeout=60000)
        logger.info("[studioia-metaai] stage=%s outcome=complete path=%s", stage, self._studioia_current_path())
        time.sleep(5)
        self._dismiss_overlays(stage="after_initial_navigation")
        stage = "cookie_reload"
        self._page.reload(wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._dismiss_overlays(stage="after_cookie_reload")
        self._ready = True
        logger.info("[studioia-metaai] stage=setup outcome=ready")
    except Exception as exc:
        self._ready = False
        logger.error("[studioia-metaai] stage=%s outcome=error error_type=%s", stage, type(exc).__name__)
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError(f"Échec Meta AI à l'étape {stage} ({type(exc).__name__}).") from exc'''


PLAYWRIGHT_CURRENT_PATH = r'''def _studioia_current_path(self) -> str:
    try:
        path = self._page.evaluate("location.pathname + location.hash")
        return str(path or "")
    except Exception:
        return ""'''


PLAYWRIGHT_DISMISS_OVERLAYS = r'''def _dismiss_overlays(self, stage: str = "runtime") -> dict:
    """Inspect overlays and report risky controls; never click a page button."""
    logger.info("[studioia-metaai] stage=overlay_inspection point=%s outcome=start", stage)
    try:
        state = self._page.evaluate("""(() => {
          const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
          const controls = Array.from(document.querySelectorAll('button,[role=button]'))
            .filter(visible).map(e => String(e.getAttribute('aria-label') || e.title || e.innerText || '').trim())
            .filter(Boolean).slice(0, 80);
          const dialogs = Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true]')).filter(visible).length;
          return {path: location.pathname + location.hash, controls, dialogs};
        })()""") or {}
    except Exception as exc:
        logger.error("[studioia-metaai] stage=overlay_inspection point=%s outcome=error error_type=%s",
                     stage, type(exc).__name__)
        raise ConnectionError(f"Impossible d'inspecter l'interface Meta AI à l'étape {stage}.") from exc

    path = str(state.get("path") or self._studioia_current_path())
    if re.search(r"settings|applications|/apps(?:/|$|[?#])", path, re.IGNORECASE):
        logger.error("[studioia-metaai] stage=overlay_inspection point=%s outcome=unexpected_route path=%s",
                     stage, path[:160])
        raise ConnectionError(f"Meta AI a ouvert une page de paramètres au lieu du chat (étape {stage}, {path[:120]}).")
    dangerous = [label for label in state.get("controls", [])
                 if str(label).casefold() in {"connect", "connecter", "se connecter", "sign in", "log in"}]
    if dangerous:
        logger.warning("[studioia-metaai] stage=overlay_inspection point=%s outcome=no_click labels=%s",
                       stage, ",".join(str(label)[:40] for label in dangerous))
    logger.info("[studioia-metaai] stage=overlay_inspection point=%s outcome=inspected dialogs=%s",
                stage, state.get("dialogs", 0))
    return state'''


PLAYWRIGHT_SWITCH_MODE = r'''def _switch_mode(self, mode_name: str) -> None:
    """Change mode only on an explicit request and by exact accessible name."""
    logger.info("[studioia-metaai] stage=mode_switch outcome=start mode=%s", mode_name)
    try:
        button = self._page.get_by_role("button", name=mode_name, exact=True)
        if button.count() != 1 or not button.is_visible():
            raise ConnectionError(f"Le contrôle de mode exact « {mode_name} » est absent ou ambigu.")
        button.click(timeout=4000)
        menu = self._page.get_by_role("menuitemcheckbox", name=mode_name, exact=True)
        if menu.count() != 1 or not menu.is_visible():
            raise ConnectionError(f"L'option de mode « {mode_name} » n'a pas été confirmée.")
        menu.click(timeout=4000)
        logger.info("[studioia-metaai] stage=mode_switch outcome=complete mode=%s", mode_name)
    except Exception as exc:
        logger.error("[studioia-metaai] stage=mode_switch outcome=error error_type=%s", type(exc).__name__)
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError(f"Échec du changement explicite vers le mode {mode_name}.") from exc'''


PLAYWRIGHT_NEW_CHAT = f'''def new_chat(self, timeout: int = 10) -> str:
    """Open a new chat via exact localized accessible labels and verify it is blank."""
    if not self._ready:
        self.setup()
    self._dismiss_overlays(stage="before_new_chat")
    before_id = self._get_conversation_id()
    labels = {NEW_CHAT_LABELS!r}
    selected = None
    for role in ("button", "link"):
        for label in labels:
            try:
                locator = self._page.get_by_role(role, name=label, exact=True)
                count = locator.count()
                if count == 1 and locator.is_visible():
                    selected = (role, label, locator)
                    break
                if count > 1:
                    logger.warning("[studioia-metaai] stage=new_chat outcome=ambiguous role=%s label=%s count=%s",
                                   role, label, count)
            except Exception:
                continue
        if selected:
            break
    if selected is None:
        logger.error("[studioia-metaai] stage=new_chat outcome=control_not_found path=%s",
                     self._studioia_current_path()[:160])
        raise ConnectionError("Bouton de nouvelle conversation Meta AI introuvable (libellés anglais/français exacts).")

    role, label, locator = selected
    logger.info("[studioia-metaai] stage=new_chat outcome=click role=%s exact_label=%s", role, label)
    try:
        locator.click(timeout=4000)
    except Exception as exc:
        logger.error("[studioia-metaai] stage=new_chat outcome=click_error error_type=%s", type(exc).__name__)
        raise ConnectionError(f"Échec du clic explicite sur « {{label}} ».") from exc

    deadline = time.time() + timeout
    last_state = {{}}
    while time.time() < deadline:
        path = self._studioia_current_path()
        if re.search(r"settings|applications|/apps(?:/|$|[?#])", path, re.IGNORECASE):
            logger.error("[studioia-metaai] stage=new_chat outcome=unexpected_route path=%s", path[:160])
            raise ConnectionError(f"Le bouton « {{label}} » a ouvert une page de paramètres ({{path[:120]}}).")
        try:
            last_state = self._response_dom_state()
            current_id = self._get_conversation_id()
            id_changed = current_id != before_id
            first_blank_page = not before_id and not current_id
            if (last_state.get("input_ready") and last_state.get("assistant_count") == 0
                    and last_state.get("user_count") == 0 and (id_changed or first_blank_page)):
                self._studioia_previous_conversation_id = before_id
                self._studioia_fresh_chat_armed = True
                logger.info("[studioia-metaai] stage=new_chat outcome=verified_blank path=%s id_changed=%s",
                            path[:160], id_changed)
                return current_id
        except ConnectionError:
            raise
        except Exception as exc:
            logger.warning("[studioia-metaai] stage=new_chat outcome=verification_poll_error error_type=%s",
                           type(exc).__name__)
        time.sleep(0.4)
    logger.error("[studioia-metaai] stage=new_chat outcome=timeout path=%s assistant_count=%s user_count=%s input_ready=%s",
                 self._studioia_current_path()[:160], last_state.get("assistant_count"),
                 last_state.get("user_count"), last_state.get("input_ready"))
    raise ConnectionError("Meta AI n'a pas confirmé de conversation vierge en 10 secondes.")'''


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

    baseline = self._response_dom_state()
    baseline_urls = set(baseline.get("urls", [])) | set(baseline.get("existing_urls", []))
    baseline_count = int(baseline.get("assistant_count", 0))
    baseline_user_count = int(baseline.get("user_count", 0))
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
    submit_deadline = time.time() + 8
    previous_id = getattr(self, "_studioia_previous_conversation_id", None)
    while time.time() < submit_deadline:
        current_id = self._get_conversation_id()
        current_state = self._response_dom_state()
        if (current_state.get("user_count", 0) > baseline_user_count
                or (current_id and current_id != previous_id)):
            break
        time.sleep(0.4)
    else:
        raise ConnectionError("Meta AI n'a pas confirmé la soumission du prompt en 8 secondes.")
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
    """Submit through the composer only; image requests leave the mode untouched."""
    stage = "send_message_setup"
    logger.info("[studioia-metaai] stage=%s outcome=start fresh_required=%s", stage, require_new_conversation)
    if not self._ready:
        self.setup()
    if require_new_conversation and not getattr(self, "_studioia_fresh_chat_armed", False):
        logger.error("[studioia-metaai] stage=new_chat_guard outcome=not_armed")
        raise ConnectionError("La conversation Meta AI n'a pas été confirmée comme nouvelle.")

    # Image generation does not need a mode toggle. Preserve an explicit Thinking
    # request for chat callers, but never click Instant as a default.
    if thinking_mode:
        self._switch_mode("Thinking")
    self._dismiss_overlays(stage="before_composer")
    baseline = self._response_dom_state()
    baseline_urls = set(baseline.get("urls", [])) | set(baseline.get("existing_urls", []))
    baseline_count = int(baseline.get("assistant_count", 0))
    baseline_user_count = int(baseline.get("user_count", 0))

    selectors = (
        '[data-testid="composer-input"]',
        'textarea[placeholder="Ask Meta AI"]',
        'main [role="textbox"]',
        'main textarea',
    )
    composer = None
    selected_selector = None
    stage = "composer_lookup"
    try:
        for selector in selectors:
            locator = self._page.locator(selector)
            count = locator.count()
            if count > 1:
                raise ConnectionError(f"Champ de saisie Meta ambigu ({selector}, {count} éléments).")
            if count == 1 and locator.is_visible():
                composer = locator
                selected_selector = selector
                break
        if composer is None:
            raise ConnectionError("Champ de saisie de la conversation absent; aucun autre bouton n'a été cliqué.")
        composer.scroll_into_view_if_needed(timeout=3000)
        composer.click(timeout=3000)
        stage = "prompt_fill"
        logger.info("[studioia-metaai] stage=%s outcome=start selector=%s", stage, selected_selector)
        composer.fill(prompt, timeout=3000)
        try:
            entered = composer.input_value()
        except Exception:
            entered = composer.inner_text()
        if entered != prompt:
            raise ConnectionError("Le champ Meta AI ne contient pas exactement le prompt demandé.")
        logger.info("[studioia-metaai] stage=prompt_fill outcome=verified characters=%s", len(prompt))
        stage = "prompt_submit"
        composer.press("Enter", timeout=3000)
        logger.info("[studioia-metaai] stage=%s outcome=submitted", stage)
        stage = "submit_confirmation"
        submit_deadline = time.time() + 8
        previous_id = getattr(self, "_studioia_previous_conversation_id", None)
        submitted = False
        while time.time() < submit_deadline:
            path = self._studioia_current_path()
            if re.search(r"settings|applications|/apps(?:/|$|[?#])", path, re.IGNORECASE):
                raise ConnectionError(f"Après la soumission, Meta AI a ouvert les paramètres ({path[:120]}).")
            current_id = self._get_conversation_id()
            current_state = self._response_dom_state()
            if (current_state.get("user_count", 0) > baseline_user_count
                    or (current_id and current_id != previous_id)):
                submitted = True
                break
            time.sleep(0.4)
        if not submitted:
            raise ConnectionError("Meta AI n'a pas confirmé la soumission du prompt en 8 secondes.")
        logger.info("[studioia-metaai] stage=%s outcome=confirmed", stage)
        if require_new_conversation:
            self._studioia_fresh_chat_armed = False
    except Exception as exc:
        logger.error("[studioia-metaai] stage=%s outcome=error error_type=%s", stage, type(exc).__name__)
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError(f"Échec Meta AI à l'étape {stage} ({type(exc).__name__}).") from exc

    stage = "image_wait"
    logger.info("[studioia-metaai] stage=%s outcome=start timeout_s=%s", stage, timeout)
    try:
        urls, text = self._wait_for_response(timeout, baseline_urls, baseline_count)
    except Exception as exc:
        logger.error("[studioia-metaai] stage=%s outcome=error error_type=%s", stage, type(exc).__name__)
        if isinstance(exc, ConnectionError):
            raise
        raise ConnectionError(f"Échec Meta AI pendant l'attente de l'image ({type(exc).__name__}).") from exc
    conv_id = self._get_conversation_id()
    if not conv_id or conv_id == getattr(self, "_studioia_previous_conversation_id", None):
        logger.error("[studioia-metaai] stage=conversation_id outcome=unconfirmed")
        raise ConnectionError("Meta AI n'a pas confirmé l'identifiant de la conversation courante.")
    logger.info("[studioia-metaai] stage=%s outcome=complete image_count=%s conversation_confirmed=true",
                stage, len(urls))
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
    logger.info("[studioia-metaai] stage=image_wait outcome=polling timeout_s=%s", timeout)
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
                logger.info("[studioia-metaai] stage=image_wait outcome=image_stable elapsed_s=%.1f candidates=%s",
                            time.time() - start, len(candidates))
                return list(candidates), text
        else:
            stable_urls = None
            stable_ticks = 0

        if text and not state.get("busy") and text_ticks >= 3:
            logger.info("[studioia-metaai] stage=image_wait outcome=text_only elapsed_s=%.1f",
                        time.time() - start)
            return [], text
    logger.error("[studioia-metaai] stage=image_wait outcome=timeout elapsed_s=%.1f", time.time() - start)
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


def _upsert_method(path: Path, class_name: str, method_name: str, before_method: str,
                   method_source: str) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cls = _find_node(tree, (class_name,))
    exists = any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name
                 for item in cls.body)
    if exists:
        _replace_callable(path, (class_name, method_name), method_source)
    else:
        _insert_method(path, class_name, before_method, method_source)


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


def _set_class_marker(path: Path, class_name: str) -> None:
    source = path.read_text(encoding="utf-8")
    current = f'"""{MARKER}"""'
    previous = f'"""{PREVIOUS_MARKER}"""'
    if current in source:
        return
    if previous in source:
        source = source.replace(previous, current, 1)
    else:
        declaration = f"class {class_name}:\n"
        if declaration not in source:
            raise RuntimeError(f"Class declaration not found while marking {class_name}")
        source = source.replace(declaration, declaration + f"    {current}\n", 1)
    path.write_text(source, encoding="utf-8", newline="")


def _replace_agent_browser(package: Path) -> None:
    path = package / "browser.py"
    _upsert_method(path, "BrowserBackend", "new_chat", "send_message", AGENT_NEW_CHAT)
    _upsert_method(path, "BrowserBackend", "_response_dom_state", "send_message", AGENT_STATE_METHOD)
    _upsert_method(path, "BrowserBackend", "send_message", "_wait_for_response", AGENT_SEND_MESSAGE)
    _upsert_method(path, "BrowserBackend", "_wait_for_response", "send_message", WAIT_METHOD)
    _set_class_marker(path, "BrowserBackend")


def _replace_playwright(package: Path) -> None:
    path = package / "playwright_backend.py"
    _upsert_method(path, "PlaywrightBackend", "setup", "new_chat", PLAYWRIGHT_SETUP)
    _upsert_method(path, "PlaywrightBackend", "_studioia_current_path", "new_chat", PLAYWRIGHT_CURRENT_PATH)
    _upsert_method(path, "PlaywrightBackend", "_dismiss_overlays", "new_chat", PLAYWRIGHT_DISMISS_OVERLAYS)
    _upsert_method(path, "PlaywrightBackend", "_switch_mode", "new_chat", PLAYWRIGHT_SWITCH_MODE)
    _upsert_method(path, "PlaywrightBackend", "new_chat", "send_message", PLAYWRIGHT_NEW_CHAT)
    _upsert_method(path, "PlaywrightBackend", "_response_dom_state", "_wait_for_response", PLAYWRIGHT_STATE_METHOD)
    _upsert_method(path, "PlaywrightBackend", "send_message", "_wait_for_response", PLAYWRIGHT_SEND_MESSAGE)
    _upsert_method(path, "PlaywrightBackend", "_wait_for_response", "send_message", WAIT_METHOD)
    _set_class_marker(path, "PlaywrightBackend")


def _ensure_asyncio_import(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if "import asyncio\n" not in source:
        source = source.replace("import logging\n", "import asyncio\nimport logging\n", 1)
    if "_studioia_image_generation_lock" not in source:
        source = source.replace("client_manager = ClientManager()\n", "client_manager = ClientManager()\n\n# Studio IA: serialize requests that share the persistent browser session.\n_studioia_image_generation_lock = threading.Lock()\n", 1)
    path.write_text(source, encoding="utf-8", newline="")


def _append_marker(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    source = source.replace(f"# {PREVIOUS_MARKER}\n", "")
    if MARKER not in source:
        path.write_text(source.rstrip() + f"\n\n# {MARKER}\n", encoding="utf-8", newline="")
    else:
        path.write_text(source, encoding="utf-8", newline="")


def patch(package: Path, backup_root: Path) -> None:
    required = ("browser.py", "playwright_backend.py", "generation.py", "main.py", "api_server.py")
    for name in required:
        if not (package / name).is_file():
            raise RuntimeError(f"Package module missing; refusing partial patch: {name}")
    marker_states = {
        name: (MARKER in (package / name).read_text(encoding="utf-8"),
               PREVIOUS_MARKER in (package / name).read_text(encoding="utf-8"))
        for name in required
    }
    v2_count = sum(v2 for v2, _ in marker_states.values())
    v1_count = sum(v1 for _, v1 in marker_states.values())
    if v2_count:
        if v2_count == len(required):
            print("Studio IA MetaAI patch v2 already installed; no files changed.")
            return
        raise RuntimeError("Partial Studio IA patch v2 detected; restore backup before retrying.")
    if v1_count and v1_count != len(required):
        raise RuntimeError("Partial v1 patch detected; restore its backup before upgrading.")
    upgrading_v1 = v1_count == len(required)

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
    action = "Upgraded v1 to v2" if upgrading_v1 else "Patched"
    print(f"{action} metaai-sdk 5.3.0 modules only. Backup: {backup}")


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
