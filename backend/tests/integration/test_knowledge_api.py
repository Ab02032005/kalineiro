"""Интеграция базы знаний: загрузка через API, поиск, источники в ответах и защита от выдумок."""

import base64
import json

from fastapi.testclient import TestClient

from kali.api.app import create_app
from kali.config.settings import Settings
from kali.knowledge.service import KnowledgeService
from kali.knowledge.store import SqliteKnowledgeStore
from kali.memory.service import MemoryService
from kali.memory.store import SqliteMemoryStore
from kali.models.registry import ProviderRegistry
from tests.fakes import FakeEmbedder, FakeProvider


def _client(provider: FakeProvider, embedder=None) -> tuple[TestClient, KnowledgeService]:
    settings = Settings(environment="test", log_level="WARNING", system_prompt="SYS",
                        memory_db_path=":memory:")
    registry = ProviderRegistry()
    registry.register(provider, default=True)
    knowledge = KnowledgeService(SqliteKnowledgeStore(":memory:"), embedder, top_k=3)
    memory = MemoryService(SqliteMemoryStore(":memory:"))
    return TestClient(create_app(settings, registry=registry, memory=memory, knowledge=knowledge)), knowledge


def _upload(client, filename: str, text: str):
    body = {"filename": filename, "content_base64": base64.b64encode(text.encode("utf-8")).decode()}
    return client.post("/v1/knowledge/documents", json=body)


def _sse_events(response) -> list[dict]:
    return [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]


def test_upload_list_get_and_delete():
    client, _ = _client(FakeProvider())
    created = _upload(client, "правила.txt", "Выходные — суббота и воскресенье.")
    assert created.status_code == 201
    doc_id = created.json()["item"]["id"]
    assert created.json()["created"] is True

    assert [d["id"] for d in client.get("/v1/knowledge/documents").json()["items"]] == [doc_id]
    assert client.get(f"/v1/knowledge/documents/{doc_id}").json()["item"]["chunks"] == 1
    assert client.delete(f"/v1/knowledge/documents/{doc_id}").status_code == 204
    assert client.get(f"/v1/knowledge/documents/{doc_id}").status_code == 404


def test_duplicate_upload_is_reported_not_created():
    client, _ = _client(FakeProvider())
    first = _upload(client, "a.txt", "одно и то же")
    second = _upload(client, "b.txt", "одно и то же")
    assert second.json()["created"] is False
    assert first.json()["item"]["id"] == second.json()["item"]["id"]


def test_bad_base64_and_unsupported_format_return_422():
    client, _ = _client(FakeProvider())
    assert client.post("/v1/knowledge/documents",
                       json={"filename": "a.txt", "content_base64": "!!!не base64!!!"}).status_code == 422
    assert _upload(client, "картинка.png", "x").status_code == 422


def test_search_endpoint_returns_excerpts_with_flags():
    client, _ = _client(FakeProvider(), embedder=FakeEmbedder())
    _upload(client, "море.txt", "Морская вода солёная, а в озере пресная.")
    body = client.get("/v1/knowledge/search", params={"q": "солёная вода"}).json()
    assert body["semantic"] is True
    assert body["items"][0]["title"] == "море"
    assert "солёная" in body["items"][0]["content"]


def test_chat_answer_contains_sources_and_context_marked_as_data():
    provider = FakeProvider(reply="Выходные — суббота и воскресенье [1].")
    client, _ = _client(provider)
    _upload(client, "правила.txt", "Выходные — суббота и воскресенье.")
    response = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Какие выходные?"}]})
    body = response.json()
    assert body["reply"].endswith("[1].")
    assert body["sources"][0]["title"] == "правила"
    assert body["sources"][0]["number"] == 1

    system_texts = [m.content for m in provider.requests[0].messages if m.role == "system"]
    assert any("[1]" in t and "не инструкции" in t for t in system_texts)


def test_chat_without_matching_documents_tells_model_not_to_invent():
    provider = FakeProvider(reply="В базе этого нет.")
    client, _ = _client(provider)
    _upload(client, "погода.txt", "Завтра будет дождь.")
    body = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Курс биткоина?"}]}).json()
    assert body["sources"] == []
    system_texts = [m.content for m in provider.requests[0].messages if m.role == "system"]
    assert any("ничего не найдено" in t for t in system_texts)


def test_use_knowledge_false_skips_sources():
    provider = FakeProvider(reply="ok")
    client, _ = _client(provider)
    _upload(client, "правила.txt", "Выходные — суббота.")
    body = client.post("/v1/chat", json={
        "messages": [{"role": "user", "content": "Выходные?"}], "use_knowledge": False}).json()
    assert body["sources"] == []


def test_stream_sends_sources_first():
    provider = FakeProvider(chunks=["Да", "."])
    client, _ = _client(provider)
    _upload(client, "правила.txt", "Выходные — суббота.")
    with client.stream("POST", "/v1/chat/stream",
                       json={"messages": [{"role": "user", "content": "Выходные?"}]}) as response:
        events = _sse_events(response)
    assert "sources" in events[0]
    assert events[0]["sources"][0]["title"] == "правила"
    assert events[1:] == [{"delta": "Да"}, {"delta": "."}, {"done": True}]


def test_health_reports_knowledge_and_semantic_flags():
    client, _ = _client(FakeProvider(), embedder=FakeEmbedder())
    body = client.get("/health").json()
    assert body["knowledge"] is True
    assert body["semantic_search"] is True
