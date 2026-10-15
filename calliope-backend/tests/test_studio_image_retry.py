import asyncio
import base64
import json

import httpx
import pytest

from calliope.config import settings
from calliope.studio import service
from tests.test_studio import png_bytes


@pytest.fixture
def image_service(monkeypatch):
    monkeypatch.setattr(settings, "studio_image_base_url", "https://apihub.agnes-ai.com/v1")
    monkeypatch.setattr(settings, "studio_image_api_key", "test-image-key")
    original_client = httpx.AsyncClient
    calls, waits = [], []

    def install(receive):
        def transport(request):
            calls.append(request)
            return receive(request)

        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda **kw: original_client(**kw, transport=httpx.MockTransport(transport)),
        )

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    request = {
        "image_base_url": settings.studio_image_base_url,
        "image_model": "agnes-image-2.5-flash",
        "aspect_ratio": "9:16",
        "prompt": "Three characters in a dense living room.",
    }
    return request, install, calls, waits


def busy():
    return httpx.Response(503, json={"error": {"message": "image queue is full, please retry later"}})


@pytest.mark.asyncio
async def test_image_queue_busy_recovers_without_changing_request(image_service):
    request, install, calls, waits = image_service

    def receive(_):
        if len(calls) == 1:
            return busy()
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(png_bytes()).decode()}],
        })

    install(receive)
    content, metadata = await service._generate_image(request)
    assert content.startswith(b"\x89PNG") and metadata["width"] > 0
    assert len(calls) == 2
    assert calls[0].content == calls[1].content
    assert json.loads(calls[1].content)["model"] == request["image_model"]
    assert sum(waits) >= 10


@pytest.mark.asyncio
async def test_image_queue_busy_stops_after_bounded_backoff(image_service):
    request, install, calls, waits = image_service
    install(lambda _: busy())
    with pytest.raises(ValueError, match="HTTP 503.*image queue is full"):
        await service._generate_image(request)
    assert len(calls) == 4
    assert sum(waits) == 70


@pytest.mark.asyncio
@pytest.mark.parametrize("status,message", [
    (400, "invalid prompt"),
    (401, "invalid key"),
    (429, "quota exhausted"),
    (503, "unknown upstream failure"),
])
async def test_image_other_errors_do_not_repeat_submission(image_service, status, message):
    request, install, calls, waits = image_service
    install(lambda _: httpx.Response(status, json={"error": {"message": message}}))
    with pytest.raises(ValueError, match=f"HTTP {status}"):
        await service._generate_image(request)
    assert len(calls) == 1 and not waits


@pytest.mark.asyncio
async def test_image_timeout_does_not_repeat_ambiguous_submission(image_service):
    request, install, calls, waits = image_service

    def receive(outgoing):
        raise httpx.ReadTimeout("Response lost", request=outgoing)

    install(receive)
    with pytest.raises(ValueError, match="images.*dix minutes"):
        await service._generate_image(request)
    assert len(calls) == 1 and not waits
    assert calls[0].extensions["timeout"]["read"] == 600


@pytest.mark.asyncio
async def test_image_read_error_recovers_with_identical_request(image_service):
    request, install, calls, waits = image_service

    def receive(outgoing):
        if len(calls) == 1:
            return busy()
        if len(calls) == 2:
            raise httpx.ReadError("Connection closed", request=outgoing)
        return httpx.Response(200, json={
            "data": [{"b64_json": base64.b64encode(png_bytes()).decode()}],
        })

    install(receive)
    content, _ = await service._generate_image(request)
    assert content.startswith(b"\x89PNG")
    assert len(calls) == 3 and sum(waits) == 30
    assert all(call.content == calls[0].content for call in calls)


@pytest.mark.asyncio
async def test_image_read_errors_stop_after_existing_attempt_limit(image_service):
    request, install, calls, waits = image_service

    def receive(outgoing):
        raise httpx.ReadError("Connection closed", request=outgoing)

    install(receive)
    with pytest.raises(ValueError, match="Connexion.*images.*interrompue"):
        await service._generate_image(request)
    assert len(calls) == 4 and sum(waits) == 70


@pytest.mark.asyncio
async def test_image_cancel_after_read_error_prevents_another_submission(image_service):
    request, install, calls, waits = image_service

    def receive(outgoing):
        raise httpx.ReadError("Connection closed", request=outgoing)

    install(receive)

    async def check_cancel():
        if waits:
            raise service.StudioCancelledError()

    with pytest.raises(service.StudioCancelledError):
        await service._generate_image(request, check_cancel=check_cancel)
    assert len(calls) == 1 and sum(waits) == 1


@pytest.mark.asyncio
async def test_image_cancel_during_backoff_prevents_next_submission(image_service):
    request, install, calls, waits = image_service
    install(lambda _: busy())

    async def check_cancel():
        if waits:
            raise service.StudioCancelledError()

    with pytest.raises(service.StudioCancelledError):
        await service._generate_image(request, check_cancel=check_cancel)
    assert len(calls) == 1
    assert sum(waits) == 1


@pytest.mark.asyncio
async def test_image_server_stop_before_submission_prevents_request(image_service):
    request, install, calls, waits = image_service
    install(lambda _: busy())

    async def check_cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await service._generate_image(request, check_cancel=check_cancel)
    assert not calls and not waits
