"""Маршруты агентов и инструментов: список, запуск агента с трассировкой действий."""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from kali.agents.loop import run_agent
from kali.agents.profiles import PROFILES, get_profile
from kali.api.auth import auth_service, require_user
from kali.core.engine import CoreEngine
from kali.models.base import Message
from kali.models.errors import ProviderError, ProviderNotFoundError
from kali.security.auth import User
from kali.tools.base import ToolRegistry

router = APIRouter(prefix="/v1", tags=["agents"])


class AgentMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


class AgentRunIn(BaseModel):
    messages: list[AgentMessage] = Field(min_length=1, max_length=200)
    profile: str = Field(default="assistant", max_length=32)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    use_memory: bool = True
    use_knowledge: bool = True
    # Рискованные инструменты (запись файлов, запуск кода, рисование) только при явном разрешении
    allow_actions: bool = False


def _engine(request: Request) -> CoreEngine:
    engine: CoreEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Модели не подключены: задайте KALI_PROVIDER_API_KEY")
    return engine


def _tools(request: Request) -> ToolRegistry:
    tools: ToolRegistry | None = getattr(request.app.state, "tools", None)
    if tools is None:
        raise HTTPException(status_code=503, detail="Инструменты не подключены")
    return tools


@router.get("/tools")
async def list_tools(request: Request) -> dict:
    return {"tools": _tools(request).describe()}


@router.get("/agents")
async def list_agents() -> dict:
    return {
        "agents": [
            {"name": p.name, "description": p.description, "tools": sorted(p.tools)}
            for p in PROFILES.values()
        ]
    }


@router.post("/agent/run")
async def run(body: AgentRunIn, request: Request, user: User = Depends(require_user)) -> dict:
    """Запускает агента: модель может вызывать инструменты профиля и получает их результаты."""
    engine = _engine(request)
    if body.allow_actions:
        auth_service(request).audit(user.username, "actions_allowed", body.profile)
    tools = _tools(request)
    try:
        profile = get_profile(body.profile)
        messages = [Message(role=m.role, content=m.content) for m in body.messages]
        prepared, sources = await engine.build(
            messages,
            model=body.model,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            use_memory=body.use_memory,
            use_knowledge=body.use_knowledge,
        )
        system_messages = list(prepared.messages)
        if profile.instructions:
            system_messages = engine.add_system(system_messages, profile.instructions)
        provider = engine.provider(body.provider)
        result = await run_agent(
            provider,
            system_messages,
            tools,
            model=prepared.model,
            allowed=set(profile.tools),
            allow_actions=body.allow_actions,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
        )
    except ProviderNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "reply": result.reply,
        "model": prepared.model,
        "profile": profile.name,
        "incomplete": result.incomplete,
        "sources": [asdict(s) for s in sources],
        "trace": [asdict(t) for t in result.trace],
    }
