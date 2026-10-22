"""Navigateur Meta AI vérifié : une conversation neuve par image, provenance prouvée.

Différences assumées avec metaai-api / metaai-sdk 5.3.0 (constatées dans le code
upstream du paquet) :

- ``MetaAI.generate_image_new`` ignore ``new_conversation`` : chaque appel écrit
  dans la conversation courante ;
- ``PlaywrightBackend._wait_for_response`` collecte **toute** URL fbcdn nouvelle
  de la page (avatar, barre latérale, anciennes générations) ;
- ``PlaywrightBackend._dismiss_overlays`` clique des boutons par texte
  (« Connect », « Accept »…), ce qui peut ouvrir Paramètres → Applications ;
- ``_switch_mode`` clique le premier bouton contenant « Instant »/« Thinking ».

Ce service ne modifie aucun fichier de ``site-packages``. Il pilote Chromium via
Playwright et n'utilise que des contrôles explicites, en échouant fermé lorsque
la provenance ne peut pas être établie.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .dom import (
    DEFAULT_MEDIA_HOST_SUFFIXES,
    PageState,
    ProvenanceError,
    RefusalError,
    detect_refusal,
    is_forbidden_path,
    normalize_space,
    parse_page_state,
    select_images,
    verify_blank_home,
    verify_conversation,
)

HOME_URL = "https://www.meta.ai/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
NEW_CHAT_LABELS = (
    "New chat",
    "New conversation",
    "Nouvelle discussion",
    "Nouvelle conversation",
    "Nouveau chat",
)
CONSENT_LABELS = ("Accept all", "Tout accepter", "Accept", "Accepter")
DANGEROUS_LABELS = {
    "connect", "connecter", "se connecter", "sign in", "log in", "s'inscrire",
    "settings", "paramètres", "parametres", "apps", "applications",
}

# Une seule lecture de page : compteurs, texte, médias du dernier tour assistant,
# instantané global des médias (pour exclure l'existant) et contrôles visibles.
STATE_JS = r"""
() => {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const root = document.querySelector('main') || document.body;
  const nonEmpty = (el) => !!(String(el.innerText || '').trim().length || el.querySelector('img, video'));
  // Les noeuds vides (gabarits, placeholders) ne comptent pas comme tours de conversation.
  const assistants = Array.from(root.querySelectorAll('[class*="assistant-message"]')).filter(nonEmpty);
  const users = Array.from(root.querySelectorAll('[class*="user-message"]')).filter(nonEmpty);
  const composer = root.querySelector(
    'textarea[data-testid="composer-input"], div[data-testid="composer-input"] [contenteditable],' +
    '[role="textbox"][contenteditable="true"], [contenteditable="true"][data-testid], textarea[placeholder]'
  );
  const lastAssistant = assistants.length ? assistants[assistants.length - 1] : null;
  const lastUser = users.length ? users[users.length - 1] : null;
  const mediaNodes = lastAssistant
    ? Array.from(lastAssistant.querySelectorAll('img, video, source, a[href]'))
    : [];
  const urlOf = (el) => {
    if (el.tagName === 'IMG') return el.currentSrc || el.getAttribute('src') || '';
    return el.getAttribute('src') || el.getAttribute('href') || '';
  };
  const snapshot = Array.from(document.querySelectorAll('img, video, source, a[href]'))
    .map(urlOf).filter((u) => /fbcdn|metaaiusercontent/i.test(u));
  return {
    path: location.pathname + location.hash,
    composer_ready: !!composer && visible(composer),
    composer_text: composer
      ? (composer.value !== undefined ? String(composer.value) : String(composer.innerText || ''))
      : '',
    assistant_count: assistants.length,
    user_count: users.length,
    last_assistant_text: lastAssistant ? String(lastAssistant.innerText || '').trim().slice(0, 4000) : '',
    last_user_text: lastUser ? String(lastUser.innerText || '').trim().slice(0, 4000) : '',
    last_assistant_media: mediaNodes.map((el) => ({
      url: urlOf(el),
      inside_assistant_turn: !!lastAssistant && lastAssistant.contains(el),
      w: el.naturalWidth || 0,
      h: el.naturalHeight || 0,
    })),
    dialogs: Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
      .filter(visible).map((el) => String(el.innerText || '').trim().slice(0, 200)),
    buttons: Array.from(document.querySelectorAll('button, [role="button"], a[role="link"]'))
      .filter(visible)
      .map((el) => String(el.getAttribute('aria-label') || el.innerText || '').trim())
      .filter(Boolean).slice(0, 120),
    page_media_snapshot: snapshot,
  };
}
"""


@dataclass
class GenerationEvidence:
    """Preuves minimales renvoyées à Studio IA (aucune donnée de cookie)."""

    image_urls: list[str]
    conversation_id: str
    previous_conversation_id: str
    prompt: str
    assistant_text: str
    rejected_media: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    prompt_echo_verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "image_urls": self.image_urls,
            "conversation_id": self.conversation_id,
            "previous_conversation_id": self.previous_conversation_id,
            "prompt": self.prompt,
            "attribution": "fresh_assistant_response",
            "assistant_turn_index": 0,
            "prompt_echo_verified": self.prompt_echo_verified,
            "assistant_text": self.assistant_text[:400],
            "rejected_media": self.rejected_media,
            "steps": self.steps,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }


class VerifiedBrowser:
    """Pilote une session Chromium et ne rend que des images prouvées du tour courant."""

    def __init__(
        self,
        cookies: dict[str, str],
        *,
        headed: bool = False,
        user_agent: str = DEFAULT_UA,
        media_host_suffixes: Sequence[str] = DEFAULT_MEDIA_HOST_SUFFIXES,
        poll_interval_s: float = 1.5,
        generation_timeout_s: float = 150.0,
        page_ready_timeout_s: float = 45.0,
        settle_polls: int = 2,
        page: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        logger: Any | None = None,
    ) -> None:
        if not page and (not cookies.get("datr") or not cookies.get("ecto_1_sess")):
            raise ProvenanceError(
                "Cookies Meta AI absents : renseignez META_AI_DATR et META_AI_ECTO_1_SESS "
                "dans l'environnement du service (jamais dans Git)."
            )
        self.cookies = dict(cookies)
        self.headed = headed
        self.user_agent = user_agent
        self.media_host_suffixes = tuple(media_host_suffixes)
        self.poll_interval_s = poll_interval_s
        self.generation_timeout_s = generation_timeout_s
        self.page_ready_timeout_s = page_ready_timeout_s
        self.settle_polls = max(1, settle_polls)
        self._clock = clock
        self._sleep = sleep
        self._page = page
        self._playwright = None
        self._browser = None
        self._context = None
        self._ready = bool(page)
        self._previous_conversation_id = ""
        self.logger = logger
        self.steps: list[str] = []
        self._consent_attempted = False
        self._new_chat_attempted = False

    # ── Cycle de vie ────────────────────────────────────────────────────────
    def setup(self) -> None:
        if self._ready:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dépend de l'installation Windows
            raise ProvenanceError(
                "Playwright n'est pas installé dans l'environnement du service Meta AI. "
                "Exécutez install.ps1 (playwright install chromium)."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=not self.headed,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 900},
            locale="fr-FR",
        )
        self._context.add_cookies([
            {"name": name, "value": value, "domain": ".meta.ai", "path": "/"}
            for name, value in self.cookies.items()
        ])
        self._page = self._context.new_page()
        self._page.set_default_timeout(15_000)
        self._ready = True
        self._log("setup:ready")

    def close(self) -> None:
        for closer in (self._context, self._browser, self._playwright):
            try:
                if closer is not None:
                    closer.stop() if hasattr(closer, "stop") else closer.close()
            except Exception:  # pragma: no cover - fermeture best effort
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._ready = False

    # ── Lecture de page ─────────────────────────────────────────────────────
    def _log(self, step: str) -> None:
        self.steps.append(step)
        if self.logger is not None:
            try:
                self.logger.info("[studioia-metaai] %s", step)
            except Exception:
                pass

    def read_state(self) -> PageState:
        raw = self._page.evaluate(STATE_JS)
        return parse_page_state(raw if isinstance(raw, dict) else {})

    # ── Actions explicites ──────────────────────────────────────────────────
    def open_fresh_home(self) -> str:
        """Charge l'accueil Meta AI et confirme une conversation vierge."""
        self._consent_attempted = False
        self._new_chat_attempted = False
        self._page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        state = self._wait_for(self._probe_blank_home, self.page_ready_timeout_s, "accueil Meta AI")
        verify_blank_home(state)
        self._log(f"home:blank path={state.path[:80]}")
        return str(getattr(self._page, "url", "") or "")

    def _probe_blank_home(self) -> PageState | None:
        state = self.read_state()
        if is_forbidden_path(state.path):
            raise ProvenanceError(
                f"Meta AI a ouvert une page de paramètres au lieu du chat ({state.path[:120]})."
            )
        if state.blank_conversation:
            return state
        if not state.composer_ready and state.dialogs and not self._consent_attempted:
            self._consent_attempted = True
            self._dismiss_consent_once()
        elif state.composer_ready and (state.assistant_count or state.user_count) and not self._new_chat_attempted:
            self._new_chat_attempted = True
            self._open_new_chat_once()
        return None

    def _dismiss_consent_once(self) -> None:
        """Clique uniquement un libellé de consentement exact, jamais un lien de connexion."""
        for label in CONSENT_LABELS:
            if self._click_exact(label, roles=("button",)):
                self._log(f"consent:clicked label={label}")
                self._sleep(1.0)
                return

    def _open_new_chat_once(self) -> None:
        for label in NEW_CHAT_LABELS:
            if self._click_exact(label, roles=("button", "link")):
                self._log(f"new_chat:clicked label={label}")
                self._sleep(1.5)
                return

    def _click_exact(self, label: str, *, roles: Iterable[str]) -> bool:
        if label.strip().casefold() in DANGEROUS_LABELS:
            return False
        for role in roles:
            try:
                locator = self._page.get_by_role(role, name=label, exact=True)
                if locator.count() != 1:
                    continue
                target = locator.first
                if not target.is_visible():
                    continue
                target.click(timeout=4_000)
                return True
            except Exception:
                continue
        return False

    def _wait_for(self, probe: Callable[[], PageState | None], timeout_s: float, label: str) -> PageState:
        deadline = self._clock() + timeout_s
        last: PageState | None = None
        while self._clock() < deadline:
            result = probe()
            if result is not None:
                return result
            self._sleep(self.poll_interval_s)
        raise ProvenanceError(f"Délai dépassé en attendant {label}.")

    # ── Saisie du prompt ────────────────────────────────────────────────────
    def _composer_input(self):
        candidates = (
            'textarea[data-testid="composer-input"]',
            'div[data-testid="composer-input"] [contenteditable="true"]',
            '[role="textbox"][contenteditable="true"]',
            "textarea[placeholder]",
        )
        for selector in candidates:
            try:
                locator = self._page.locator(selector)
                if locator.count() >= 1 and locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        raise ProvenanceError("Champ de saisie Meta AI introuvable (interface modifiée ?).")

    def _type_prompt(self, prompt: str) -> None:
        composer = self._composer_input()
        composer.click(timeout=5_000)
        filled = False
        try:
            composer.fill(prompt, timeout=5_000)
            filled = True
        except Exception:
            filled = False
        if not filled:
            self._page.keyboard.insert_text(prompt)
        self._sleep(0.3)
        state = self.read_state()
        if normalize_space(state.composer_text) != normalize_space(prompt):
            raise ProvenanceError(
                "Le prompt saisi n'a pas été relu à l'identique dans le champ Meta AI : envoi annulé."
            )
        self._log("composer:filled")

    # ── Génération vérifiée ─────────────────────────────────────────────────
    def generate_image(
        self,
        prompt: str,
        *,
        used_conversation_ids: set[str],
        timeout_s: float | None = None,
    ) -> GenerationEvidence:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ProvenanceError("Prompt vide : aucune génération envoyée.")
        if not self._ready:
            self.setup()
        started = self._clock()
        self.steps = []

        before_state = self.read_state()
        before_media = set(before_state.media_snapshot)
        baseline_conversation = self._previous_conversation_id
        self.open_fresh_home()
        # L'instantané est pris sur l'accueil vierge : aucun média de génération
        # antérieure ne peut être confondu avec la réponse courante.
        after_home = self.read_state()
        before_media |= set(after_home.media_snapshot)

        self._type_prompt(prompt)
        self._page.keyboard.press("Enter")
        self._log("prompt:sent")

        deadline = started + float(timeout_s or self.generation_timeout_s)
        stable = 0
        last_signature: tuple[str, int] | None = None
        conversation_id = ""
        images: list[str] = []
        rejected: list[str] = []
        assistant_text = ""

        while self._clock() < deadline:
            self._sleep(self.poll_interval_s)
            try:
                state = self.read_state()
            except Exception:
                continue
            if is_forbidden_path(state.path):
                raise ProvenanceError(
                    f"Meta AI a quitté la conversation vers une page de paramètres ({state.path[:120]})."
                )
            current_id = self._conversation_id_from_page()
            conversation_id = current_id or conversation_id
            if state.assistant_count > 1:
                raise ProvenanceError(
                    "Plus d'une réponse assistant dans une conversation censée être neuve : provenance ambiguë."
                )
            if state.assistant_count == 1:
                assistant_text = state.last_assistant_text
                images, rejected = select_images(
                    state, before=before_media, allowed_suffixes=self.media_host_suffixes
                )
                signature = (assistant_text, len(images))
                stable = stable + 1 if signature == last_signature else 0
                last_signature = signature

                if images and stable >= self.settle_polls:
                    break
                if not images and detect_refusal(assistant_text):
                    raise RefusalError(
                        "Meta AI a refusé la génération : " + assistant_text[:240]
                    )
                if not images and stable >= self.settle_polls and assistant_text.strip():
                    raise ProvenanceError(
                        "Meta AI a répondu sans image exploitable dans le tour assistant : "
                        + assistant_text[:240]
                    )
        else:
            raise ProvenanceError(
                "Meta AI n'a pas produit d'image vérifiable dans le délai imparti. "
                "Aucune image n'a été enregistrée."
            )

        if not conversation_id:
            raise ProvenanceError(
                "Identifiant de conversation Meta AI introuvable dans l'URL : provenance non prouvable."
            )
        verify_conversation(
            conversation_id,
            previous_conversation_id=baseline_conversation,
            used_conversation_ids=used_conversation_ids,
        )
        if not images:
            raise ProvenanceError("Aucune image du tour assistant courant n'a été acceptée.")

        prompt_echo = normalize_space(state.last_user_text) == normalize_space(prompt)
        self._previous_conversation_id = conversation_id
        self._log(f"generation:verified conversation={conversation_id} prompt_echo={prompt_echo}")
        return GenerationEvidence(
            image_urls=images,
            conversation_id=conversation_id,
            previous_conversation_id=baseline_conversation,
            prompt=prompt,
            assistant_text=assistant_text,
            rejected_media=rejected,
            steps=list(self.steps),
            elapsed_seconds=self._clock() - started,
            prompt_echo_verified=prompt_echo,
        )

    def _conversation_id_from_page(self) -> str:
        from .dom import conversation_id_from_url

        try:
            return conversation_id_from_url(str(self._page.url))
        except Exception:
            return ""
