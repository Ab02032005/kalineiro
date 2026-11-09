"""Tool Engine: общий интерфейс инструментов и реестр с правами доступа.

Инструмент — объект с именем, описанием, JSON-схемой аргументов и методом run.
Рискованные инструменты (пишут файлы, запускают код, обращаются к внешним сервисам)
доступны модели только если запрос явно разрешил действия (allow_actions).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from kali.models.base import ToolSpec

log = logging.getLogger("kali.tools")

MAX_OUTPUT_CHARS = 4000


class ToolError(Exception):
    """Ошибка, понятная модели и пользователю. Текст не должен содержать секретов."""


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    text: str


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict
    risky: bool

    async def run(self, args: dict) -> str: ...


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n… (вывод обрезан)"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Инструмент {tool.name!r} уже зарегистрирован")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "risky": t.risky}
            for t in self._tools.values()
        ]

    def specs(self, allowed: set[str] | None, allow_risky: bool) -> tuple[ToolSpec, ...]:
        """Инструменты, которые можно показать модели в этом запросе."""
        result = []
        for tool in self._tools.values():
            if allowed is not None and tool.name not in allowed:
                continue
            if tool.risky and not allow_risky:
                continue
            result.append(ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters))
        return tuple(result)

    async def execute(
        self,
        name: str,
        raw_arguments: str,
        *,
        allowed: set[str] | None,
        allow_risky: bool,
    ) -> Outcome:
        """Выполняет вызов модели. Любые ошибки возвращаются как результат, а не исключения:
        модель видит сообщение и может ответить без инструмента."""
        tool = self._tools.get(name)
        if tool is None or (allowed is not None and name not in allowed):
            return Outcome(False, f"Инструмент {name!r} недоступен в этом режиме")
        if tool.risky and not allow_risky:
            return Outcome(False, f"Инструмент {name!r} требует разрешения действий (allow_actions)")
        try:
            args = json.loads(raw_arguments or "{}")
        except ValueError:
            return Outcome(False, "Аргументы инструмента не являются корректным JSON")
        if not isinstance(args, dict):
            return Outcome(False, "Аргументы инструмента должны быть объектом")
        try:
            text = await tool.run(args)
            return Outcome(True, _truncate(text))
        except ToolError as exc:
            return Outcome(False, str(exc))
        except Exception:  # непредвиденная ошибка инструмента: подробности только в логе
            log.exception("tool failed", extra={"tool": name})
            return Outcome(False, "Инструмент завершился с внутренней ошибкой")
