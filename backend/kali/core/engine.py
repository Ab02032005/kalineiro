"""Core AI Engine: принимает диалог, проверяет его, добавляет память и отдаёт провайдеру модели.

Этапы 3–4: валидация, системная инструкция, выбор провайдера, краткосрочная память
(обрезка диалога по бюджету) и долговременная память (факты и процедуры в контексте).
Проверка ответов и инструменты подключаются на этапах 6 и 9 через те же точки входа.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from kali.memory.service import MemoryService
from kali.models.base import ChatRequest, Message
from kali.models.registry import ProviderRegistry

MAX_MESSAGES = 200


@dataclass(frozen=True, slots=True)
class EngineLimits:
    max_input_chars: int
    max_messages: int = MAX_MESSAGES
    short_term_chars: int = 12_000


class CoreEngine:
    """Единая точка входа для ответов модели."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        system_prompt: str,
        default_model: str,
        limits: EngineLimits,
        memory: MemoryService | None = None,
    ) -> None:
        self._registry = registry
        self._system_prompt = system_prompt
        self._default_model = default_model
        self._limits = limits
        self._memory = memory

    @property
    def memory(self) -> MemoryService | None:
        return self._memory

    def _prepare(self, messages: Sequence[Message], model: str | None) -> list[Message]:
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
        return prepared

    def _with_memory(self, prepared: list[Message]) -> list[Message]:
        """Вставляет в диалог релевантные записи памяти сразу после системных инструкций."""
        if self._memory is None:
            return prepared
        query = prepared[-1].content
        items = self._memory.context_for(query)
        context = self._memory.format_context(items)
        if not context:
            return prepared
        insert_at = 0
        while insert_at < len(prepared) and prepared[insert_at].role == "system":
            insert_at += 1
        return prepared[:insert_at] + [Message(role="system", content=context)] + prepared[insert_at:]

    def _request(
        self,
        messages: Sequence[Message],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        use_memory: bool,
    ) -> ChatRequest:
        prepared = self._prepare(messages, model)
        if use_memory:
            prepared = self._with_memory(prepared)
        prepared = MemoryService.trim_short_term(prepared, self._limits.short_term_chars)
        return ChatRequest(
            messages=tuple(prepared),
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
        use_memory: bool = True,
    ) -> str:
        """Полный ответ одной строкой."""
        request = self._request(messages, model, temperature, max_tokens, use_memory)
        return await self._registry.get(provider).complete(request)

    def answer_stream(
        self,
        messages: Sequence[Message],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_memory: bool = True,
    ) -> AsyncIterator[str]:
        """Поток фрагментов ответа. Проверка входа выполняется сразу, до начала потока."""
        request = self._request(messages, model, temperature, max_tokens, use_memory)
        return self._registry.get(provider).stream(request)

    def remember_exchange(self, messages: Sequence[Message], reply: str) -> None:
        """Сохраняет эпизод о завершённом обмене. Вызывается только при явном разрешении."""
        if self._memory is None or not messages:
            return
        self._memory.record_episode(messages[-1].content, reply)

    @property
    def default_model(self) -> str:
        return self._default_model
