"""Цикл агента: модель вызывает инструменты, получает результаты и продолжает, пока не ответит.

Ограничения: не больше MAX_STEPS обращений к модели, каждое действие записывается в trace,
ошибки инструментов возвращаются модели как результат (а не роняют запрос).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from kali.models.base import ChatRequest, LLMProvider, Message
from kali.tools.base import ToolRegistry

MAX_STEPS = 5
TRACE_PREVIEW_CHARS = 500


@dataclass(frozen=True, slots=True)
class TraceEntry:
    tool: str
    arguments: str
    ok: bool
    result: str


@dataclass(slots=True)
class AgentRun:
    reply: str
    trace: list[TraceEntry] = field(default_factory=list)
    incomplete: bool = False


async def run_agent(
    provider: LLMProvider,
    messages: Sequence[Message],
    tools: ToolRegistry,
    *,
    model: str,
    allowed: set[str],
    allow_actions: bool,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AgentRun:
    conversation = list(messages)
    specs = tools.specs(allowed, allow_actions)
    trace: list[TraceEntry] = []

    for _ in range(MAX_STEPS):
        request = ChatRequest(
            messages=tuple(conversation),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=specs,
        )
        result = await provider.complete_with_tools(request)
        if not result.tool_calls:
            return AgentRun(reply=result.text, trace=trace)

        conversation.append(Message(role="assistant", content=result.text, tool_calls=result.tool_calls))
        for call in result.tool_calls:
            outcome = await tools.execute(
                call.name, call.arguments, allowed=allowed, allow_risky=allow_actions
            )
            trace.append(TraceEntry(
                tool=call.name,
                arguments=call.arguments,
                ok=outcome.ok,
                result=outcome.text[:TRACE_PREVIEW_CHARS],
            ))
            conversation.append(Message(
                role="tool",
                content=outcome.text,
                tool_call_id=call.id,
                name=call.name,
            ))

    return AgentRun(
        reply="Не удалось завершить задачу за отведённое число шагов. Попробуйте упростить запрос.",
        trace=trace,
        incomplete=True,
    )
