"""Интеграция памяти: маршруты /v1/memories и влияние памяти на ответы модели."""

import json

from fastapi.testclient import TestClient

from kali.api.app import create_app
from kali.config.settings import Settings
from kali.memory.service import MemoryService
from kali.memory.store import SqliteMemoryStore
from kali.models.registry import ProviderRegistry
from tests.fakes import FakeProvider


def _app(provider: FakeProvider | None = None, memory_enabled: bool = True):
    settings = Settings(
        environment="test",
        log_level="WARNING",
        system_prompt="SYS",
        memory_db_path=":memory:",
        memory_enabled=memory_enabled,
    )
    registry = None
    if provider is not None:
        registry = ProviderRegistry()
        registry.register(provider, default=True)
    store = SqliteMemoryStore(":memory:")
    service = MemoryService(store, context_limit=5, enabled=memory_enabled)
    return create_app(settings, registry=registry, memory=service), service


def _client(provider=None, memory_enabled=True) -> TestClient:
    app, _ = _app(provider, memory_enabled)
    return TestClient(app)


# ---- маршруты памяти ----

def test_create_list_search_update_delete_flow():
    client = _client()
    created = client.post("/v1/memories", json={"kind": "fact", "content": "Живу во Франкфурте"})
    assert created.status_code == 201
    assert created.json()["created"] is True
    item_id = created.json()["item"]["id"]

    listed = client.get("/v1/memories").json()["items"]
    assert [i["id"] for i in listed] == [item_id]

    found = client.get("/v1/memories/search", params={"q": "франкфурт"}).json()["items"]
    assert [i["content"] for i in found] == ["Живу во Франкфурте"]

    patched = client.patch(f"/v1/memories/{item_id}", json={"content": "Живу в Берлине"})
    assert patched.json()["item"]["content"] == "Живу в Берлине"

    assert client.delete(f"/v1/memories/{item_id}").status_code == 204
    assert client.delete(f"/v1/memories/{item_id}").status_code == 404


def test_secret_is_rejected_with_422_and_not_echoed():
    client = _client()
    response = client.post(
        "/v1/memories",
        json={"kind": "fact", "content": "ключ sk-zsXoXfcY2XLeSDULcP6DU6QaSVe7fLTqvizLuXlnLksS7HAq"},
    )
    assert response.status_code == 422
    assert "sk-zs" not in response.text


def test_episode_kind_cannot_be_created_by_hand():
    response = _client().post("/v1/memories", json={"kind": "episode", "content": "x"})
    assert response.status_code == 422


def test_wipe_requires_confirmation():
    client = _client()
    client.post("/v1/memories", json={"kind": "fact", "content": "факт"})
    assert client.delete("/v1/memories").status_code == 400
    assert client.delete("/v1/memories", params={"confirm": "true"}).json() == {"deleted": 1}
    assert client.get("/v1/memories").json()["items"] == []


def test_export_returns_all_items():
    client = _client()
    client.post("/v1/memories", json={"kind": "procedure", "content": "шаг 1, шаг 2"})
    body = client.get("/v1/memories/export").json()
    assert body["format_version"] == 1
    assert [i["kind"] for i in body["items"]] == ["procedure"]


def test_list_rejects_unknown_kind():
    assert _client().get("/v1/memories", params={"kind": "bogus"}).status_code == 422


# ---- влияние памяти на ответы ----

def test_fact_is_injected_into_model_request():
    provider = FakeProvider(reply="ok")
    client = _client(provider)
    client.post("/v1/memories", json={"kind": "fact", "content": "Пользователь любит Python"})
    client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Расскажи про Python"}]})

    system_texts = [m.content for m in provider.requests[0].messages if m.role == "system"]
    assert system_texts[0] == "SYS"
    assert any("Пользователь любит Python" in t for t in system_texts)


def test_memory_not_used_when_flag_is_off():
    provider = FakeProvider(reply="ok")
    client = _client(provider)
    client.post("/v1/memories", json={"kind": "fact", "content": "любит Python"})
    client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "Python"}], "use_memory": False},
    )
    assert len(provider.requests[0].messages) == 2  # системная инструкция и вопрос


def test_memory_disabled_in_settings_gives_no_context():
    provider = FakeProvider(reply="ok")
    client = _client(provider, memory_enabled=False)
    client.post("/v1/memories", json={"kind": "fact", "content": "любит Python"})
    client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Python"}]})
    assert len(provider.requests[0].messages) == 2


def test_remember_saves_episode_only_when_asked():
    provider = FakeProvider(reply="Ответ про Kali")
    client = _client(provider)
    client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Что такое Kali?"}]})
    assert client.get("/v1/memories").json()["items"] == []

    client.post(
        "/v1/chat",
        json={"messages": [{"role": "user", "content": "Что такое Kali?"}], "remember": True},
    )
    items = client.get("/v1/memories").json()["items"]
    assert [i["kind"] for i in items] == ["episode"]
    assert "Что такое Kali?" in items[0]["content"]


def test_stream_with_remember_saves_full_reply():
    provider = FakeProvider(chunks=["Kali ", "— платформа"])
    client = _client(provider)
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"messages": [{"role": "user", "content": "Кто ты?"}], "remember": True},
    ) as response:
        events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
    assert events[-1] == {"done": True}
    items = client.get("/v1/memories").json()["items"]
    assert len(items) == 1
    assert "Kali — платформа" in items[0]["content"]
