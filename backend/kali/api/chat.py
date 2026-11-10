"""Маршруты чата: обычный ответ, потоковый ответ (SSE), проверка ответов и список провайдеров."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kali.api.auth import require_user
from kali.core.engine import CoreEngine, Source
from kali.models.base import ChatRequest, Message
from kali.models.errors import ProviderError, ProviderNotFoundError
from kali.verification.checker import Verification, check_answer

log = logging.getLogger("kali.api.chat")
router = APIRouter(prefix="/v1", tags=["chat"])


class MessageIn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


class ChatIn(BaseModel):
    messages: list[MessageIn] = Field(min_length=1, max_length=200)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    # Использовать сохранённые факты и процедуры (по умолчанию да)
    use_memory: bool = True
    # Искать ответ в базе знаний и ссылаться на источники (по умолчанию да)
    use_knowledge: bool = True
    # Сохранить краткую запись об этом обмене в эпизоды (по умолчанию нет)
    remember: bool = False
    # Проверять ответ по источникам и при ошибках попросить исправить (по умолчанию да)
    verify: bool = True


def _engine(request: Request) -> CoreEngine:
    engine: CoreEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Модели не подключены: задайте KALI_PROVIDER_API_KEY")
    return engine


def _to_messages(items: list[MessageIn]) -> list[Message]:
    return [Message(role=m.role, content=m.content) for m in items]


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProviderNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(status_code=exc.status_code, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _sources_view(sources: list[Source]) -> list[dict]:
    views = []
    for source in sources:
        data = asdict(source)
        data.pop("text", None)  # полный текст фрагмента клиенту не отдаём
        views.append(data)
    return views


def _source_texts(sources: list[Source]) -> dict[int, str]:
    return {s.number: s.text for s in sources}


def _correction_request(request: ChatRequest, reply: str, issues: list[str]) -> ChatRequest:
    """Просит модель исправить ответ, указав, что именно не подтверждено."""
    note = (
        "Проверка нашла проблемы в ответе:\n- " + "\n- ".join(issues[:5]) + "\n"
        "Перепиши ответ: используй только фрагменты из базы знаний с правильными номерами [n], "
        "убери утверждения без подтверждения, а если данных нет — прямо скажи об этом."
    )
    return ChatRequest(
        messages=request.messages + (Message(role="assistant", content=reply), Message(role="user", content=note)),
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )


@router.get("/providers")
async def list_providers(request: Request, _: object = Depends(require_user)) -> dict:
    """Подключённые провайдеры. Ключи и адреса не возвращаются."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None or len(registry) == 0:
        return {"providers": [], "default": None}
    return {"providers": registry.names(), "default": registry.default_name}


@router.post("/chat")
async def chat(body: ChatIn, request: Request) -> dict:
    """Полный ответ модели одним JSON: источники, проверка и, при ошибках, одна попытка исправления."""
    engine = _engine(request)
    messages = _to_messages(body.messages)
    corrected = False
    try:
        chat_request, sources = await engine.build(
            messages,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            use_memory=body.use_memory,
            use_knowledge=body.use_knowledge,
        )
        provider = engine.provider(body.provider)
        reply = await provider.complete(chat_request)
        texts = _source_texts(sources)
        verification = check_answer(reply, texts)
        if body.verify and sources and verification.issues:
            candidate = await provider.complete(_correction_request(chat_request, reply, verification.issues))
            candidate_check = check_answer(candidate, texts)
            if candidate.strip() and len(candidate_check.issues) < len(verification.issues):
                reply, verification, corrected = candidate, candidate_check, True
    except (ProviderError, ProviderNotFoundError, ValueError) as exc:
        raise _map_error(exc) from exc
    if body.remember:
        engine.remember_exchange(messages, reply)
    return {
        "reply": reply,
        "model": body.model or engine.default_model,
        "sources": _sources_view(sources),
        "verification": verification.as_dict(),
        "corrected": corrected,
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _event_stream(
    sources: list[Source],
    chunks: AsyncIterator[str],
    on_complete: Callable[[str], Verification | None] | None,
) -> AsyncIterator[str]:
    collected: list[str] = []
    yield _sse({"sources": _sources_view(sources)})
    try:
        async for piece in chunks:
            collected.append(piece)
            yield _sse({"delta": piece})
        verification = on_complete("".join(collected)) if on_complete is not None else None
        if verification is not None:
            yield _sse({"verification": verification.as_dict()})
        yield _sse({"done": True})
    except ProviderError as exc:
        log.warning("stream failed", extra={"provider": exc.provider, "status": exc.status_code})
        yield _sse({"error": exc.message, "status": exc.status_code})


@router.post("/chat/stream")
async def chat_stream(body: ChatIn, request: Request) -> StreamingResponse:
    """Ответ по мере генерации.

    События: {"sources": [...]} первым, затем {"delta": "..."}, затем {"verification": {...}}
    и {"done": true}. Исправление при потоковом ответе не выполняется: клиент видит результат проверки.
    Ошибки входа возвращаются до начала потока (HTTP 4xx).
    """
    engine = _engine(request)
    messages = _to_messages(body.messages)
    try:
        chat_request, sources = await engine.build(
            messages,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            use_memory=body.use_memory,
            use_knowledge=body.use_knowledge,
        )
        chunks = engine.provider(body.provider).stream(chat_request)
    except (ProviderNotFoundError, ValueError) as exc:
        raise _map_error(exc) from exc

    texts = _source_texts(sources)

    def finish(reply: str) -> Verification | None:
        if body.remember and reply:
            engine.remember_exchange(messages, reply)
        return check_answer(reply, texts) if body.verify and reply else None

    return StreamingResponse(
        _event_stream(sources, chunks, finish),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
