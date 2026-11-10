"""Тесты проверки ответов: ссылки, подтверждение фрагментами, неизвестное, действия агентов и исправление."""

import base64
import json

from fastapi.testclient import TestClient

from kali.api.app import create_app
from kali.config.settings import Settings
from kali.models.registry import ProviderRegistry
from kali.verification.checker import check_actions, check_answer
from tests.fakes import FakeProvider

RULES = {1: "Выходные дни в компании — суббота и воскресенье. Офис открыт с 9:00 до 18:00."}


# ---- логика проверки ----

def test_supported_citation_is_verified():
    result = check_answer("Выходные — суббота и воскресенье [1].", RULES)
    assert result.status == "verified"
    assert result.issues == []
    assert result.citations_supported == 1


def test_citation_to_missing_source_is_reported():
    result = check_answer("Выходные — суббота и воскресенье [5].", RULES)
    assert result.status == "unsupported"
    assert any("[5]" in issue for issue in result.issues)


def test_claim_not_in_fragment_is_unsupported():
    result = check_answer("Зарплата выплачивается каждую пятницу [1].", RULES)
    assert result.status == "unsupported"
    assert any("не подтверждено" in issue for issue in result.issues)


def test_uncited_claims_make_answer_partial():
    result = check_answer(
        "Выходные — суббота и воскресенье [1]. Компания работает очень давно и имеет много офисов.", RULES)
    assert result.status == "partial"
    assert any("без ссылок" in issue for issue in result.issues)


def test_admitting_no_data_is_unknown():
    assert check_answer("В базе нет ответа на этот вопрос.", RULES).status == "unknown"


def test_answer_without_sources_is_marked_as_assumption():
    assert check_answer("Столица Франции — Париж.", {}).status == "assumption"


def test_citation_without_sources_is_unsupported():
    assert check_answer("Это точно так [1].", {}).status == "unsupported"


def test_empty_answer_is_unknown():
    assert check_answer("   ", RULES).status == "unknown"


def test_agent_claim_without_tool_is_flagged():
    issues = check_actions("Я сохранил файл заметка.md.", [])
    assert issues and "write_file" in issues[0]


def test_agent_claim_with_successful_tool_is_accepted():
    trace = [{"tool": "write_file", "ok": True}]
    assert check_actions("Я сохранил файл заметка.md.", trace) == []


def test_failed_tool_does_not_confirm_claim():
    trace = [{"tool": "write_file", "ok": False}]
    assert check_actions("Файл сохранён.", trace)


# ---- маршруты ----

def _client(provider: FakeProvider) -> TestClient:
    settings = Settings(auth_enabled=False, environment="test", log_level="WARNING",
                        memory_db_path=":memory:", system_prompt="SYS", embeddings_enabled=False)
    registry = ProviderRegistry()
    registry.register(provider, default=True)
    return TestClient(create_app(settings, registry=registry))


def _upload(client: TestClient) -> None:
    body = {"filename": "правила.txt", "content_base64": base64.b64encode(RULES[1].encode("utf-8")).decode()}
    assert client.post("/v1/knowledge/documents", json=body).status_code == 201


def test_chat_reports_verification_for_grounded_answer():
    provider = FakeProvider(reply="Выходные — суббота и воскресенье [1].")
    client = _client(provider)
    _upload(client)
    body = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Какие выходные?"}]}).json()
    assert body["verification"]["status"] == "verified"
    assert body["corrected"] is False
    assert "text" not in body["sources"][0]  # полный текст фрагмента не утекает


def test_bad_citation_is_corrected_once():
    provider = FakeProvider(reply_script=[
        "Выходные — суббота и воскресенье [7].",
        "Выходные — суббота и воскресенье [1].",
    ])
    client = _client(provider)
    _upload(client)
    body = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Какие выходные?"}]}).json()
    assert body["corrected"] is True
    assert body["reply"].endswith("[1].")
    assert body["verification"]["status"] == "verified"
    assert len(provider.requests) == 2  # ответ + одна попытка исправления


def test_correction_can_be_disabled():
    provider = FakeProvider(reply="Выходные — суббота и воскресенье [7].")
    client = _client(provider)
    _upload(client)
    body = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Какие выходные?"}],
                                         "verify": False}).json()
    assert body["corrected"] is False
    assert len(provider.requests) == 1


def test_stream_sends_verification_before_done():
    provider = FakeProvider(chunks=["Выходные — суббота и воскресенье [1]."])
    client = _client(provider)
    _upload(client)
    with client.stream("POST", "/v1/chat/stream",
                       json={"messages": [{"role": "user", "content": "Какие выходные?"}]}) as response:
        events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
    kinds = [next(iter(e)) for e in events]
    assert kinds[-2:] == ["verification", "done"]
    assert events[-2]["verification"]["status"] == "verified"


def test_agent_claiming_unperformed_action_is_flagged():
    provider = FakeProvider(reply="Я сохранил файл заметка.md.")
    client = _client(provider)
    body = client.post("/v1/agent/run", json={
        "messages": [{"role": "user", "content": "Сохрани заметку"}], "profile": "writer"}).json()
    assert body["verification"]["status"] == "unsupported"
    assert any("write_file" in issue for issue in body["verification"]["issues"])
