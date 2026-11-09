"""Тесты адаптера OpenAI-совместимого API. Сеть не используется: подменяется транспорт httpx."""

import asyncio
import json

import httpx
import pytest

from kali.models.base import ChatRequest, Message
from kali.models.errors import ProviderError
from kali.models.openai_compatible import OpenAICompatibleProvider

REQUEST = ChatRequest(
    messages=(Message(role="system", content="sys"), Message(role="user", content="привет")),
    model="gpt-test",
    temperature=0.2,
    max_tokens=50,
)


def _provider(handler) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="test",
        base_url="https://example.test/v1/",
        api_key="sk-test-key",
        transport=httpx.MockTransport(handler),
    )


def test_complete_returns_content_and_sends_expected_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Привет!"}}]})

    result = asyncio.run(_provider(handler).complete(REQUEST))

    assert result == "Привет!"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test-key"
    assert seen["body"]["model"] == "gpt-test"
    assert seen["body"]["stream"] is False
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["max_tokens"] == 50
    assert seen["body"]["messages"][1] == {"role": "user", "content": "привет"}


def test_stream_yields_deltas_and_skips_empty_events():
    sse = (
        'data: {"choices":[]}\n\n'
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"При"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"вет"}}]}\n\n'
        "data: [DONE]\n\n"
        'data: {"choices":[{"delta":{"content":"игнор"}}]}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=sse.encode(), headers={"Content-Type": "text/event-stream"})

    async def collect():
        return [piece async for piece in _provider(handler).stream(REQUEST)]

    assert asyncio.run(collect()) == ["При", "вет"]


def test_http_error_becomes_provider_error_without_key_in_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key sk-test-key")

    with pytest.raises(ProviderError) as info:
        asyncio.run(_provider(handler).complete(REQUEST))
    assert info.value.status_code == 502
    assert "401" in info.value.message


def test_rate_limit_is_mapped_to_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    with pytest.raises(ProviderError) as info:
        asyncio.run(_provider(handler).complete(REQUEST))
    assert info.value.status_code == 429


def test_network_failure_becomes_502():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ProviderError) as info:
        asyncio.run(_provider(handler).complete(REQUEST))
    assert info.value.status_code == 502


def test_unexpected_response_shape_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oops": True})

    with pytest.raises(ProviderError) as info:
        asyncio.run(_provider(handler).complete(REQUEST))
    assert info.value.status_code == 502
