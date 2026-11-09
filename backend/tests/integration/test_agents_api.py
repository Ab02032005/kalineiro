"""Тесты агентов: цикл вызова инструментов, ограничения шагов и маршруты /v1/agent/run."""

import asyncio

from fastapi.testclient import TestClient

from kali.agents.loop import MAX_STEPS, run_agent
from kali.agents.profiles import PROFILES, get_profile
from kali.api.app import create_app
from kali.config.settings import Settings
from kali.models.base import ChatResult, Message, ToolCall
from kali.models.registry import ProviderRegistry
from kali.tools.base import ToolRegistry
from kali.tools.builtin import Calculator, ListFiles, Workspace, WriteFile
from tests.fakes import FakeProvider


def _tools(tmp_path) -> ToolRegistry:
    ws = Workspace(tmp_path / "ws")
    registry = ToolRegistry()
    registry.register(Calculator())
    registry.register(ListFiles(ws))
    registry.register(WriteFile(ws))
    return registry


def _call(name: str, arguments: str, call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def test_agent_calls_tool_then_answers(tmp_path):
    provider = FakeProvider(tool_script=[
        ChatResult(text="", tool_calls=(_call("calculator", '{"expression": "15*4"}'),)),
        ChatResult(text="Ответ: 60"),
    ])
    result = asyncio.run(run_agent(
        provider, [Message(role="user", content="15*4?")], _tools(tmp_path),
        model="m", allowed={"calculator"}, allow_actions=False))
    assert result.reply == "Ответ: 60"
    assert result.incomplete is False
    assert [t.tool for t in result.trace] == ["calculator"]
    assert result.trace[0].ok and result.trace[0].result == "60"
    # Модель во втором запросе видит результат инструмента
    second = provider.requests[1].messages
    assert second[-1].role == "tool" and second[-1].content == "60"
    assert second[-1].tool_call_id == "c1"


def test_risky_tool_requested_without_permission_is_refused(tmp_path):
    provider = FakeProvider(tool_script=[
        ChatResult(tool_calls=(_call("write_file", '{"name": "a.txt", "content": "x"}'),)),
        ChatResult(text="Файл не записан, нужно разрешение."),
    ])
    result = asyncio.run(run_agent(
        provider, [Message(role="user", content="запиши")], _tools(tmp_path),
        model="m", allowed={"write_file"}, allow_actions=False))
    assert result.trace[0].ok is False
    assert not (tmp_path / "ws" / "a.txt").exists()
    # Во время запроса без разрешения инструмент даже не предлагается модели
    assert all(spec.name != "write_file" for spec in provider.requests[0].tools)


def test_risky_tool_runs_when_allowed(tmp_path):
    provider = FakeProvider(tool_script=[
        ChatResult(tool_calls=(_call("write_file", '{"name": "a.txt", "content": "привет"}'),)),
        ChatResult(text="Готово."),
    ])
    result = asyncio.run(run_agent(
        provider, [Message(role="user", content="запиши")], _tools(tmp_path),
        model="m", allowed={"write_file"}, allow_actions=True))
    assert result.trace[0].ok is True
    assert (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8") == "привет"


def test_loop_stops_after_max_steps(tmp_path):
    endless = [ChatResult(tool_calls=(_call("calculator", '{"expression": "1+1"}', f"c{i}"),))
               for i in range(MAX_STEPS + 2)]
    provider = FakeProvider(tool_script=endless)
    result = asyncio.run(run_agent(
        provider, [Message(role="user", content="?")], _tools(tmp_path),
        model="m", allowed={"calculator"}, allow_actions=False))
    assert result.incomplete is True
    assert len(provider.requests) == MAX_STEPS
    assert len(result.trace) == MAX_STEPS


def test_profiles_have_known_tools_only():
    known = {"calculator", "list_files", "read_file", "write_file", "python_exec", "generate_image"}
    for profile in PROFILES.values():
        assert profile.tools <= known


def test_unknown_profile_is_rejected():
    try:
        get_profile("nope")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ---- маршруты ----

def _client(tmp_path, provider: FakeProvider) -> TestClient:
    settings = Settings(environment="test", log_level="WARNING", system_prompt="SYS",
                        memory_db_path=":memory:", workspace_dir=str(tmp_path / "ws"),
                        sandbox_enabled=False, knowledge_enabled=False)
    registry = ProviderRegistry()
    registry.register(provider, default=True)
    return TestClient(create_app(settings, registry=registry))


def test_tools_and_agents_endpoints(tmp_path):
    client = _client(tmp_path, FakeProvider())
    tools = client.get("/v1/tools").json()["tools"]
    names = {t["name"] for t in tools}
    assert {"calculator", "write_file", "generate_image"} <= names
    assert "python_exec" not in names  # песочница выключена в этих настройках
    agents = client.get("/v1/agents").json()["agents"]
    assert {a["name"] for a in agents} >= {"assistant", "math", "coding", "writer", "artist"}


def test_agent_run_endpoint_returns_trace(tmp_path):
    provider = FakeProvider(tool_script=[
        ChatResult(tool_calls=(_call("calculator", '{"expression": "2**8"}'),)),
        ChatResult(text="256"),
    ])
    body = _client(tmp_path, provider).post("/v1/agent/run", json={
        "messages": [{"role": "user", "content": "2 в степени 8?"}],
        "profile": "math",
    }).json()
    assert body["reply"] == "256"
    assert body["profile"] == "math"
    assert body["trace"][0]["tool"] == "calculator"
    assert body["trace"][0]["result"] == "256"
    # Инструкция профиля попала в системные сообщения
    system_texts = [m.content for m in provider.requests[0].messages if m.role == "system"]
    assert any("calculator" in t for t in system_texts)


def test_agent_run_blocks_actions_by_default(tmp_path):
    provider = FakeProvider(tool_script=[
        ChatResult(tool_calls=(_call("write_file", '{"name": "b.txt", "content": "x"}'),)),
        ChatResult(text="Не могу записать без разрешения."),
    ])
    body = _client(tmp_path, provider).post("/v1/agent/run", json={
        "messages": [{"role": "user", "content": "запиши"}],
        "profile": "writer",
    }).json()
    assert body["trace"][0]["ok"] is False
    assert not (tmp_path / "ws" / "b.txt").exists()


def test_agent_run_rejects_unknown_profile(tmp_path):
    response = _client(tmp_path, FakeProvider()).post("/v1/agent/run", json={
        "messages": [{"role": "user", "content": "x"}], "profile": "ghost"})
    assert response.status_code == 422
