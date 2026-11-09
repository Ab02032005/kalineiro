"""Контракт провайдера моделей. Ядро знает только эти типы, а не конкретный API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    """Одно сообщение диалога."""

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Запрос к модели в нейтральном виде. Адаптер сам переводит его в формат провайдера."""

    messages: tuple[Message, ...]
    model: str
    temperature: float | None = None
    max_tokens: int | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Все адаптеры моделей реализуют этот интерфейс.

    name — уникальное имя провайдера в реестре.
    complete — полный ответ одной строкой.
    stream — асинхронный поток текстовых фрагментов.
    Ошибки провайдера всегда выражаются через ProviderError.
    """

    name: str

    async def complete(self, request: ChatRequest) -> str: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...
