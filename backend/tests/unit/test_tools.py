"""Тесты инструментов: калькулятор, файлы, права доступа, песочница (без запуска Docker)."""

import asyncio

import pytest

from kali.models.base import ToolCall
from kali.tools.base import ToolError, ToolRegistry
from kali.tools.builtin import Calculator, ListFiles, ReadFile, Workspace, WriteFile, calculate
from kali.tools.sandbox import MAX_CODE_CHARS, SandboxLimits, build_docker_command, run_python

# ---- калькулятор ----

@pytest.mark.parametrize(
    "expression, expected",
    [
        ("2 + 3 * 4", "14"),
        ("(2 + 3) * 4", "20"),
        ("2 ** 10", "1024"),
        ("7 // 2", "3"),
        ("7 % 3", "1"),
        ("sqrt(16)", "4"),
        ("-5 + 2", "-3"),
        ("10 / 4", "2.5"),
        ("12,5 * 2", "25"),
    ],
)
def test_calculator_values(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "open('x')",
        "1 +",
        "x + 1",
        "2 ** 100000",
        "9" * 400,
    ],
)
def test_calculator_rejects_unsafe_or_broken_input(expression):
    with pytest.raises(ToolError):
        calculate(expression)


# ---- файлы в рабочей папке ----

def test_workspace_rejects_paths_outside_root(tmp_path):
    ws = Workspace(tmp_path / "ws")
    for bad in ["../secret.txt", "sub/x.txt", "..\\x.txt", "/etc/passwd", ""]:
        with pytest.raises(ToolError):
            ws.path_for(bad)


def test_write_and_read_roundtrip(tmp_path):
    ws = Workspace(tmp_path / "ws")
    asyncio.run(WriteFile(ws).run({"name": "заметка.md", "content": "Привет"}))
    assert asyncio.run(ReadFile(ws).run({"name": "заметка.md"})) == "Привет"
    assert "заметка.md" in asyncio.run(ListFiles(ws).run({}))


def test_write_rejects_dangerous_extension(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(ToolError):
        asyncio.run(WriteFile(ws).run({"name": "run.exe", "content": "x"}))


def test_write_rejects_huge_content(tmp_path):
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(ToolError):
        asyncio.run(WriteFile(ws).run({"name": "big.txt", "content": "x" * 200_001}))


def test_read_missing_file(tmp_path):
    with pytest.raises(ToolError):
        asyncio.run(ReadFile(Workspace(tmp_path)).run({"name": "нет.txt"}))


# ---- реестр и права ----

def _registry(tmp_path) -> ToolRegistry:
    ws = Workspace(tmp_path / "ws")
    registry = ToolRegistry()
    registry.register(Calculator())
    registry.register(ListFiles(ws))
    registry.register(WriteFile(ws))
    return registry


def test_risky_tool_is_hidden_without_allow_actions(tmp_path):
    registry = _registry(tmp_path)
    names = {s.name for s in registry.specs(None, allow_risky=False)}
    assert "write_file" not in names
    assert "calculator" in names
    names_allowed = {s.name for s in registry.specs(None, allow_risky=True)}
    assert "write_file" in names_allowed


def test_profile_allowlist_limits_visible_tools(tmp_path):
    registry = _registry(tmp_path)
    names = {s.name for s in registry.specs({"calculator"}, allow_risky=True)}
    assert names == {"calculator"}


def test_execute_blocks_risky_without_permission(tmp_path):
    registry = _registry(tmp_path)
    outcome = asyncio.run(registry.execute(
        "write_file", '{"name": "a.txt", "content": "x"}', allowed=None, allow_risky=False))
    assert outcome.ok is False
    assert "разрешения" in outcome.text
    assert not (tmp_path / "ws" / "a.txt").exists()


def test_execute_unknown_or_disallowed_tool(tmp_path):
    registry = _registry(tmp_path)
    assert asyncio.run(registry.execute("nope", "{}", allowed=None, allow_risky=True)).ok is False
    assert asyncio.run(registry.execute("calculator", '{"expression": "1+1"}',
                                        allowed={"list_files"}, allow_risky=True)).ok is False


def test_execute_bad_arguments_are_reported_not_raised(tmp_path):
    registry = _registry(tmp_path)
    outcome = asyncio.run(registry.execute("calculator", "не json", allowed=None, allow_risky=False))
    assert outcome.ok is False and "JSON" in outcome.text


def test_execute_success_returns_text(tmp_path):
    outcome = asyncio.run(_registry(tmp_path).execute(
        "calculator", '{"expression": "6*7"}', allowed=None, allow_risky=False))
    assert outcome.ok is True and outcome.text == "42"


def test_duplicate_registration_rejected(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(ValueError):
        registry.register(Calculator())


# ---- песочница ----

def test_docker_command_has_all_isolation_flags(tmp_path):
    command = build_docker_command(str(tmp_path), SandboxLimits(), "name-1")
    joined = " ".join(command)
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--memory 256m", "--pids-limit 64", "--user 65534:65534", "-v", ":/work:ro"]:
        assert flag in joined
    assert command[-4:] == ["python", "-I", "-B", "main.py"]


def test_sandbox_rejects_empty_and_huge_code():
    with pytest.raises(ToolError):
        asyncio.run(run_python("   "))
    with pytest.raises(ToolError):
        asyncio.run(run_python("x" * (MAX_CODE_CHARS + 1)))


def test_sandbox_reports_missing_docker(monkeypatch):
    from kali.tools import sandbox

    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    with pytest.raises(ToolError) as info:
        asyncio.run(run_python("print(1)"))
    assert "Docker" in str(info.value)


def test_tool_call_dataclass_is_plain():
    call = ToolCall(id="1", name="calculator", arguments='{"expression": "1"}')
    assert call.name == "calculator"
