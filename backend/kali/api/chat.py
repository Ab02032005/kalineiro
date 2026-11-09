"""Маршруты чата: обычный ответ, потоковый ответ (SSE) и список провайдеров."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kali.core.engine import CoreEngine
from kali.models.base import Message
from kali.models.errors import ProviderError, ProviderNotFoundError

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


@router.get("/providers")
async def list_providers(request: Request) -> dict:
    """Подключённые провайдеры. Ключи и адреса не возвращаются."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None or len(registry) == 0:
        return {"providers": [], "default": None}
    return {"providers": registry.names(), "default": registry.default_name}


@router.post("/chat")
async def chat(body: ChatIn, request: Request) -> dict:
    """Полный ответ модели одним JSON."""
    engine = _engine(request)
    try:
        reply = await engine.answer(
            _to_messages(body.messages),
            provider=body.provider,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except (ProviderError, ProviderNotFoundError, ValueError) as exc:
        raise _map_error(exc) from exc
    return {"reply": reply, "model": body.model or engine.default_model}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _event_stream(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        async for piece in chunks:
            yield _sse({"delta": piece})
        yield _sse({"done": True})
    except ProviderError as exc:
        log.warning("stream failed", extra={"provider": exc.provider, "status": exc.status_code})
        yield _sse({"error": exc.message, "status": exc.status_code})


@router.post("/chat/stream")
async def chat_stream(body: ChatIn, request: Request) -> StreamingResponse:
    """Ответ по мере генерации: события SSE вида data: {"delta": "..."}, в конце {"done": true}."""
    engine = _engine(request)
    try:
        chunks = engine.answer_stream(
            _to_messages(body.messages),
            provider=body.provider,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except (ProviderNotFoundError, ValueError) as exc:
        raise _map_error(exc) from exc
    return StreamingResponse(
        _event_stream(chunks),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
