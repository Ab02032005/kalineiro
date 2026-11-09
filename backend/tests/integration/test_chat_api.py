"""Интеграционные тесты чат-маршрутов: HTTP-слой, SSE, ошибки и отсутствие провайдера."""

import json

from fastapi.testclient import TestClient

from kali.api.app import create_app
from kali.config.settings import Settings
from kali.models.errors import ProviderError
from kali.models.registry import ProviderRegistry
from tests.fakes import FakeProvider


def _client(provider: FakeProvider | None = None) -> TestClient:
    settings = Settings(
        environment="test",
        log_level="WARNING",
        system_prompt="SYS",
        memory_db_path=":memory:",
    )
    registry = None
    if provider is not None:
        registry = ProviderRegistry()
        registry.register(provider, default=True)
    return TestClient(create_app(settings, registry=registry))


def _payload(text: str = "привет", **extra) -> dict:
    return {"messages": [{"role": "user", "content": text}], **extra}


def test_chat_returns_reply_and_uses_default_model():
    provider = FakeProvider(reply="Здравствуйте")
    response = _client(provider).post("/v1/chat", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Здравствуйте"
    assert body["model"] == "gpt-4o-mini"
    assert provider.requests[0].messages[0].content == "SYS"


def test_chat_accepts_explicit_model_and_provider():
    provider = FakeProvider(name="p1", reply="x")
    response = _client(provider).post("/v1/chat", json=_payload(model="m-2", provider="p1"))
    assert response.status_code == 200
    assert response.json()["model"] == "m-2"
    assert provider.requests[0].model == "m-2"


def test_chat_unknown_provider_returns_404():
    response = _client(FakeProvider()).post("/v1/chat", json=_payload(provider="nope"))
    assert response.status_code == 404


def test_chat_without_provider_returns_503():
    response = _client(None).post("/v1/chat", json=_payload())
    assert response.status_code == 503


def test_chat_validation_rejects_bad_body():
    client = _client(FakeProvider())
    assert client.post("/v1/chat", json={"messages": []}).status_code == 422
    assert client.post("/v1/chat", json=_payload(temperature=5)).status_code == 422
    assert client.post("/v1/chat", json={"messages": [{"role": "bot", "content": "x"}]}).status_code == 422


def test_chat_last_message_must_be_user_returns_422():
    body = {"messages": [{"role": "assistant", "content": "я"}]}
    assert _client(FakeProvider()).post("/v1/chat", json=body).status_code == 422


def test_chat_provider_error_maps_to_status():
    provider = FakeProvider(fail_with=ProviderError("fake", 429, "лимит"))
    response = _client(provider).post("/v1/chat", json=_payload())
    assert response.status_code == 429
    assert response.json()["detail"] == "лимит"


def test_stream_emits_sse_events_and_done():
    provider = FakeProvider(chunks=["При", "вет"])
    with _client(provider).stream("POST", "/v1/chat/stream", json=_payload()) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
    assert events[0] == {"sources": []}
    assert events[1:] == [{"delta": "При"}, {"delta": "вет"}, {"done": True}]


def test_stream_error_is_sent_as_event():
    provider = FakeProvider(fail_with=ProviderError("fake", 502, "обрыв"))
    with _client(provider).stream("POST", "/v1/chat/stream", json=_payload()) as response:
        events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
    assert events[1:] == [{"error": "обрыв", "status": 502}]


def test_providers_endpoint_hides_secrets():
    provider = FakeProvider(name="chatanywhere")
    body = _client(provider).get("/v1/providers").json()
    assert body == {"providers": ["chatanywhere"], "default": "chatanywhere"}


def test_health_reports_provider_count():
    assert _client(FakeProvider()).get("/health").json()["providers"] == 1
    assert _client(None).get("/health").json()["providers"] == 0
