"""Core AI Engine: проверяет диалог, добавляет память и базу знаний, отдаёт запрос провайдеру.

Этапы 3–5: валидация, системная инструкция, выбор провайдера, краткосрочная память
(обрезка диалога), долговременная память (факты и процедуры) и база знаний (фрагменты
документов со ссылками на источники). Проверка ответов и инструменты — этапы 6 и 9.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kali.knowledge.service import KnowledgeService
from kali.memory.service import MemoryService
from kali.models.base import ChatRequest, LLMProvider, Message
from kali.models.registry import ProviderRegistry

MAX_MESSAGES = 200


@dataclass(frozen=True, slots=True)
class EngineLimits:
    max_input_chars: int
    max_messages: int = MAX_MESSAGES
    short_term_chars: int = 12_000


@dataclass(frozen=True, slots=True)
class Source:
    """Источник, на который может ссылаться ответ. Номер совпадает с [n] в контексте."""

    number: int
    document_id: str
    title: str
    chunk_index: int
    excerpt: str


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
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self._registry = registry
        self._system_prompt = system_prompt
        self._default_model = default_model
        self._limits = limits
        self._memory = memory
        self._knowledge = knowledge

    @property
    def memory(self) -> MemoryService | None:
        return self._memory

    @property
    def knowledge(self) -> KnowledgeService | None:
        return self._knowledge

    def provider(self, name: str | None = None) -> LLMProvider:
        return self._registry.get(name)

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

    @staticmethod
    def _insert_system(prepared: list[Message], text: str) -> list[Message]:
        """Вставляет системное сообщение после существующих системных инструкций."""
        insert_at = 0
        while insert_at < len(prepared) and prepared[insert_at].role == "system":
            insert_at += 1
        return prepared[:insert_at] + [Message(role="system", content=text)] + prepared[insert_at:]

    async def build(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_memory: bool = True,
        use_knowledge: bool = True,
    ) -> tuple[ChatRequest, list[Source]]:
        """Собирает запрос к модели и список источников. Ошибки входа — ValueError."""
        prepared = self._prepare(messages, model)
        question = prepared[-1].content

        if use_memory and self._memory is not None:
            context = self._memory.format_context(self._memory.context_for(question))
            if context:
                prepared = self._insert_system(prepared, context)

        sources: list[Source] = []
        if use_knowledge and self._knowledge is not None:
            hits = await self._knowledge.search(question)
            if hits:
                prepared = self._insert_system(prepared, self._knowledge.format_context(hits))
                sources = [
                    Source(
                        number=n,
                        document_id=hit.document_id,
                        title=hit.title,
                        chunk_index=hit.chunk_index,
                        excerpt=self._knowledge.excerpt(hit.content),
                    )
                    for n, hit in enumerate(hits, start=1)
                ]
            elif self._knowledge.has_documents():
                prepared = self._insert_system(prepared, self._knowledge.no_match_note())

        prepared = MemoryService.trim_short_term(prepared, self._limits.short_term_chars)
        request = ChatRequest(
            messages=tuple(prepared),
            model=model or self._default_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return request, sources

    async def answer(
        self,
        messages: Sequence[Message],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_memory: bool = True,
        use_knowledge: bool = True,
    ) -> tuple[str, list[Source]]:
        """Полный ответ одной строкой и список источников."""
        request, sources = await self.build(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_memory=use_memory,
            use_knowledge=use_knowledge,
        )
        reply = await self.provider(provider).complete(request)
        return reply, sources

    def remember_exchange(self, messages: Sequence[Message], reply: str) -> None:
        """Сохраняет эпизод о завершённом обмене. Вызывается только при явном разрешении."""
        if self._memory is None or not messages:
            return
        self._memory.record_episode(messages[-1].content, reply)

    @property
    def default_model(self) -> str:
        return self._default_model
