"""Faux navigateur Playwright : rejoue des états de page scriptés, sans réseau."""

from __future__ import annotations

import copy
from typing import Any


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


def blank_state(**overrides: Any) -> dict:
    state = {
        "path": "/",
        "composer_ready": True,
        "composer_text": "",
        "assistant_count": 0,
        "user_count": 0,
        "last_assistant_text": "",
        "last_user_text": "",
        "last_assistant_media": [],
        "dialogs": [],
        "buttons": ["New chat", "Create an AI image", "Settings"],
        "page_media_snapshot": [],
    }
    state.update(overrides)
    return state


def answered_state(
    prompt: str,
    *,
    media: list[dict] | None = None,
    text: str = "Here is your image.",
    extra_snapshot: list[str] | None = None,
) -> dict:
    media = media if media is not None else [
        {"url": "https://scontent.xx.fbcdn.net/v/t39/generated-red-cube.png", "inside_assistant_turn": True, "w": 1024, "h": 1024}
    ]
    return blank_state(
        path="/prompt/11111111-2222-3333-4444-555555555555",
        composer_text="",
        assistant_count=1,
        user_count=1,
        last_assistant_text=text,
        last_user_text=prompt,
        last_assistant_media=media,
        page_media_snapshot=list(extra_snapshot or []),
    )


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return 1 if self.page.composer_available else 0

    def is_visible(self) -> bool:
        return self.page.composer_available

    def click(self, timeout: int | None = None) -> None:
        self.page.clicked.append(self.selector)

    def fill(self, value: str, timeout: int | None = None) -> None:
        if not self.page.fill_enabled:
            raise RuntimeError("fill indisponible")
        self.page.composer_text = value
        self.page.fills.append(value)


class FakeRoleLocator(FakeLocator):
    def __init__(self, page: "FakePage", name: str) -> None:
        super().__init__(page, f"role:button:{name}")
        self.name = name

    def count(self) -> int:
        return 1 if self.name in self.page.controls else 0

    def is_visible(self) -> bool:
        return self.name in self.page.controls

    def click(self, timeout: int | None = None) -> None:
        self.page.controls.remove(self.name)
        self.page.clicked.append(self.name)
        if self.name in self.page.blank_on_control:
            self.page.set_state(blank_state(page_media_snapshot=self.page.state.get("page_media_snapshot", [])))


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def insert_text(self, text: str) -> None:
        self.page.composer_text = text
        self.page.fills.append(text)

    def press(self, key: str) -> None:
        self.page.pressed.append(key)
        if key == "Enter":
            self.page.after_send()


class FakePage:
    """Chronologie : accueil → saisie → réponses scriptées → identifiant d'URL."""

    def __init__(
        self,
        *,
        home: dict | None = None,
        responses: list[dict] | None = None,
        conversation_id: str = "11111111-2222-3333-4444-555555555555",
        composer_available: bool = True,
        fill_enabled: bool = True,
        controls: list[str] | None = None,
        blank_on_control: list[str] | None = None,
        url: str = "https://www.meta.ai/",
    ) -> None:
        self.state = copy.deepcopy(home or blank_state())
        self.home = copy.deepcopy(home or blank_state())
        self.responses = [copy.deepcopy(item) for item in (responses or [])]
        self.conversation_id = conversation_id
        self.composer_available = composer_available
        self.fill_enabled = fill_enabled
        self.controls = list(controls if controls is not None else self.home.get("buttons", []))
        self.blank_on_control = list(blank_on_control or [])
        self.url = url
        self.composer_text = ""
        self.goto_calls: list[str] = []
        self.pressed: list[str] = []
        self.clicked: list[str] = []
        self.fills: list[str] = []
        self._response_index = 0
        self.keyboard = FakeKeyboard(self)

    # API Playwright utilisée par VerifiedBrowser
    def set_default_timeout(self, timeout: int) -> None:
        pass

    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.goto_calls.append(url)
        self.url = "https://www.meta.ai/"
        self.state = copy.deepcopy(self.home)
        self.controls = list(self.home.get("buttons", []))
        self.composer_text = ""

    def evaluate(self, script: str) -> dict:
        state = copy.deepcopy(self.state)
        state["composer_text"] = self.composer_text
        return state

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> FakeRoleLocator:
        return FakeRoleLocator(self, str(name or ""))

    def set_state(self, state: dict) -> None:
        self.state = copy.deepcopy(state)

    def after_send(self) -> None:
        self.url = f"https://www.meta.ai/prompt/{self.conversation_id}"
        if self.responses:
            state = self.responses[min(self._response_index, len(self.responses) - 1)]
            self._response_index += 1
            self.state = copy.deepcopy(state)
            self.state["path"] = f"/prompt/{self.conversation_id}"
