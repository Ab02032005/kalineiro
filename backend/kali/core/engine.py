"""Core AI Engine: принимает диалог, проверяет его и отдаёт провайдеру модели.

На этапе 3 ядро — тонкий оркестратор: валидация, системная инструкция, выбор провайдера.
Память, инструменты и проверка ответов подключаются на этапах 4–9 через те же точки входа.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from kali.models.base import ChatRequest, Message
from kali.models.registry import ProviderRegistry

MAX_MESSAGES = 200


@dataclass(frozen=True, slots=True)
class EngineLimits:
    max_input_chars: int
    max_messages: int = MAX_MESSAGES


class CoreEngine:
    """Единая точка входа для ответов модели."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        system_prompt: str,
        default_model: str,
        limits: EngineLimits,
    ) -> None:
        self._registry = registry
        self._system_prompt = system_prompt
        self._default_model = default_model
        self._limits = limits

    def _prepare(self, messages: Sequence[Message], model: str | None) -> tuple[Message, ...]:
        """Проверяет диалог и добавляет системную инструкцию в начало."""
        if not messages:
            raise ValueError("Диалог пуст")
        if len(messages) > self._limits.max_messages:
            raise ValueError(f"Слишком много сообщений (максимум {self._limits.max_messages})")
        if messages[-1].role != "user":
            raise ValueError("Последнее сообщение должно быть от пользователя")
        total = sum(len(m.content) for m in messages)
        if total > self._limits.max_input_chars:
            raise ValueError(f"Диалог слишком длинный (максимум {self._limits.max_input_chars} символов)")
        if model is not None and not model.strip():
            raise ValueError("Имя модели не может быть пустым")

        prepared: list[Message] = []
        if messages[0].role != "system":
            prepared.append(Message(role="system", content=self._system_prompt))
        prepared.extend(messages)
        return tuple(prepared)

    def _request(
        self,
        messages: Sequence[Message],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> ChatRequest:
        return ChatRequest(
            messages=self._prepare(messages, model),
            model=model or self._default_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def answer(
        self,
        messages: Sequence[Message],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Полный ответ одной строкой."""
        request = self._request(messages, model, temperature, max_tokens)
        return await self._registry.get(provider).complete(request)

    def answer_stream(
        self,
        messages: Sequence[Message],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Поток фрагментов ответа. Проверка входа выполняется сразу, до начала потока."""
        request = self._request(messages, model, temperature, max_tokens)
        return self._registry.get(provider).stream(request)

    @property
    def default_model(self) -> str:
        return self._default_model
