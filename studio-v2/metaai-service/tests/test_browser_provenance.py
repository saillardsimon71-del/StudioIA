"""Provenance des images Meta AI : règles vérifiées hors ligne, sans navigateur réel."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from studioia_metaai.browser import VerifiedBrowser  # noqa: E402
from studioia_metaai.dom import (  # noqa: E402
    ProvenanceError,
    RefusalError,
    host_allowed,
    select_images,
    parse_page_state,
)
from tests.fake_page import FakeClock, FakePage, answered_state, blank_state  # noqa: E402

PROMPT = "A single red cube on a plain white background, no text."


def make_browser(page: FakePage, clock: FakeClock | None = None, **kwargs) -> VerifiedBrowser:
    clock = clock or FakeClock()
    return VerifiedBrowser(
        cookies={},
        page=page,
        clock=clock,
        sleep=clock.sleep,
        poll_interval_s=1.0,
        generation_timeout_s=10.0,
        page_ready_timeout_s=5.0,
        settle_polls=2,
        **kwargs,
    )


def test_fresh_home_generation_returns_only_current_turn_image():
    page = FakePage(responses=[answered_state(PROMPT)])
    browser = make_browser(page)

    evidence = browser.generate_image(PROMPT, used_conversation_ids=set())

    assert evidence.image_urls == ["https://scontent.xx.fbcdn.net/v/t39/generated-red-cube.png"]
    assert evidence.conversation_id == "11111111-2222-3333-4444-555555555555"
    payload = evidence.as_dict()
    assert payload["attribution"] == "fresh_assistant_response"
    assert payload["prompt"] == PROMPT
    assert page.goto_calls == ["https://www.meta.ai/"]
    assert page.pressed == ["Enter"]


def test_profile_and_previous_images_are_never_selected():
    avatar = "https://scontent.xx.fbcdn.net/v/t1/profile-avatar.jpg"
    old = "https://scontent.xx.fbcdn.net/v/t39/old-generation.png"
    fresh = "https://scontent.xx.fbcdn.net/v/t39/new-generation.png"
    response = answered_state(
        PROMPT,
        media=[
            {"url": avatar, "inside_assistant_turn": False, "w": 40, "h": 40},
            {"url": old, "inside_assistant_turn": True, "w": 1024, "h": 1024},
            {"url": fresh, "inside_assistant_turn": True, "w": 1024, "h": 1024},
        ],
        extra_snapshot=[avatar, old],
    )
    page = FakePage(
        home=blank_state(page_media_snapshot=[avatar, old]),
        responses=[response],
    )
    browser = make_browser(page)

    evidence = browser.generate_image(PROMPT, used_conversation_ids=set())

    assert evidence.image_urls == [fresh]
    assert any("hors du tour assistant" in reason for reason in evidence.rejected_media)
    assert any("déjà présent" in reason for reason in evidence.rejected_media)


def test_reused_conversation_is_rejected():
    page = FakePage(responses=[answered_state(PROMPT)])
    browser = make_browser(page)

    with pytest.raises(ProvenanceError, match="déjà utilisée"):
        browser.generate_image(
            PROMPT,
            used_conversation_ids={"11111111-2222-3333-4444-555555555555"},
        )


def test_same_conversation_twice_in_one_session_is_rejected():
    page = FakePage(responses=[answered_state(PROMPT), answered_state(PROMPT)])
    browser = make_browser(page)

    browser.generate_image(PROMPT, used_conversation_ids=set())
    with pytest.raises(ProvenanceError, match="déjà utilisée|précédente"):
        browser.generate_image(PROMPT, used_conversation_ids=set())


def test_refusal_without_image_is_explicit():
    refusal = answered_state(PROMPT, media=[], text="I can't create that image.")
    page = FakePage(responses=[refusal])
    browser = make_browser(page)

    with pytest.raises(RefusalError):
        browser.generate_image(PROMPT, used_conversation_ids=set())


def test_assistant_answer_without_media_fails_closed():
    page = FakePage(responses=[answered_state(PROMPT, media=[], text="Sure, here is a description.")])
    browser = make_browser(page)

    with pytest.raises(ProvenanceError, match="sans image"):
        browser.generate_image(PROMPT, used_conversation_ids=set())


def test_composer_mismatch_cancels_sending():
    page = FakePage(responses=[answered_state(PROMPT)])
    page.fill_enabled = False
    page.keyboard.insert_text = lambda text: None  # la saisie ne prend pas
    browser = make_browser(page)

    with pytest.raises(ProvenanceError, match="relu"):
        browser.generate_image(PROMPT, used_conversation_ids=set())
    assert page.pressed == []


def test_settings_page_is_never_accepted_as_conversation():
    page = FakePage(home=blank_state(path="/settings/applications"))
    browser = make_browser(page)

    with pytest.raises(ProvenanceError, match="paramètres"):
        browser.generate_image(PROMPT, used_conversation_ids=set())


def test_non_blank_home_can_recover_via_exact_new_chat_control():
    home = blank_state(assistant_count=1, user_count=1, last_assistant_text="old answer")
    page = FakePage(home=home, controls=["New chat"], blank_on_control=["New chat"], responses=[answered_state(PROMPT)])
    browser = make_browser(page)

    evidence = browser.generate_image(PROMPT, used_conversation_ids=set())

    assert evidence.image_urls
    assert "New chat" in page.clicked


def test_non_blank_home_without_control_fails_closed():
    home = blank_state(assistant_count=1, user_count=1, last_assistant_text="old answer")
    page = FakePage(home=home, controls=[], responses=[answered_state(PROMPT)])
    browser = make_browser(page)

    with pytest.raises(ProvenanceError, match="Délai dépassé"):
        browser.generate_image(PROMPT, used_conversation_ids=set())


def test_ambiguous_multiple_assistant_turns_are_rejected():
    response = answered_state(PROMPT)
    response["assistant_count"] = 2
    page = FakePage(responses=[response])
    browser = make_browser(page)

    with pytest.raises(ProvenanceError, match="Plus d'une réponse"):
        browser.generate_image(PROMPT, used_conversation_ids=set())


def test_untrusted_host_and_missing_conversation_id_are_rejected():
    assert host_allowed("https://scontent.xx.fbcdn.net/a.jpg")
    assert host_allowed("https://cdn.metaaiusercontent.com/a.jpg")
    assert not host_allowed("https://images.example.invalid/a.jpg")
    assert not host_allowed("http://127.0.0.1:8000/a.jpg")

    state = parse_page_state(
        answered_state(
            PROMPT,
            media=[{"url": "https://images.example.invalid/avatar.png", "inside_assistant_turn": True}],
        )
    )
    accepted, rejected = select_images(state, before=set())
    assert accepted == []
    assert rejected and "non fiable" in rejected[0]


def test_service_rejects_duplicate_request_id_and_reports_busy(tmp_path):
    from studioia_metaai.service import (
        DuplicateRequestError,
        MetaAIImageService,
        ServiceBusyError,
        ServiceConfigurationError,
    )

    clock = FakeClock()

    def factory(**kwargs):
        return make_browser(FakePage(responses=[answered_state(PROMPT)]), clock)

    service = MetaAIImageService(
        cookies={"datr": "x", "ecto_1_sess": "y"},
        state_path=tmp_path / "state.json",
        browser_factory=factory,
    )
    result = service.generate(PROMPT, request_id="req-1")
    assert result["request_id"] == "req-1"
    assert result["attribution"] == "fresh_assistant_response"
    assert service.health()["generated_this_session"] == 1
    # L'état est persisté : la conversation et la requête ne peuvent pas être réutilisées.
    reloaded = MetaAIImageService(
        cookies={"datr": "x", "ecto_1_sess": "y"},
        state_path=tmp_path / "state.json",
        browser_factory=factory,
    )
    with pytest.raises(DuplicateRequestError):
        reloaded.generate(PROMPT, request_id="req-1")

    with pytest.raises(ServiceConfigurationError):
        MetaAIImageService(cookies={}, state_path=tmp_path / "x.json").generate("hello")

    busy = MetaAIImageService(
        cookies={"datr": "x", "ecto_1_sess": "y"},
        state_path=tmp_path / "busy.json",
        browser_factory=factory,
    )
    assert busy._lock.acquire(blocking=False)
    try:
        with pytest.raises(ServiceBusyError):
            busy.generate(PROMPT)
    finally:
        busy._lock.release()
