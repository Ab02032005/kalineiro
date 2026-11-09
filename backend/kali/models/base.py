"""Контракт провайдера моделей. Ядро знает только эти типы, а не конкретный API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Запрос модели вызвать инструмент. arguments — JSON-строка, как отдаёт модель."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Описание инструмента для модели: имя, назначение и JSON-схема аргументов."""

    name: str
    description: str
    parameters: dict


@dataclass(frozen=True, slots=True)
class Message:
    """Одно сообщение диалога. Для инструментов заполняются tool_calls, tool_call_id и name."""

    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Запрос к модели в нейтральном виде. Адаптер сам переводит его в формат провайдера."""

    messages: tuple[Message, ...]
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    tools: tuple[ToolSpec, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class ChatResult:
    """Ответ модели: текст и/или запросы на вызов инструментов."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@runtime_checkable
class LLMProvider(Protocol):
    """Все адаптеры моделей реализуют этот интерфейс.

    name — уникальное имя провайдера в реестре.
    complete — полный ответ одной строкой (без инструментов).
    complete_with_tools — ответ, который может содержать вызовы инструментов.
    stream — асинхронный поток текстовых фрагментов (без инструментов).
    Ошибки провайдера всегда выражаются через ProviderError.
    """

    name: str

    async def complete(self, request: ChatRequest) -> str: ...

    async def complete_with_tools(self, request: ChatRequest) -> ChatResult: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...
