"""Статистика и страница администратора: только безопасные сведения, без секретов."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from kali import __version__
from kali.api.auth import require_admin, require_user
from kali.security.auth import User
from kali.tools.sandbox import docker_available

router = APIRouter(prefix="/v1", tags=["system"])

# Настройки, которые можно показать в интерфейсе. Секреты сюда не входят никогда.
PUBLIC_SETTINGS = (
    "environment",
    "default_model",
    "provider_name",
    "memory_enabled",
    "memory_context_limit",
    "knowledge_enabled",
    "knowledge_top_k",
    "knowledge_max_upload_mb",
    "embeddings_enabled",
    "embedding_model",
    "sandbox_enabled",
    "sandbox_image",
    "sandbox_timeout_s",
    "max_input_chars",
    "short_term_chars",
)


@router.get("/stats")
async def stats(request: Request, _: User = Depends(require_user)) -> dict:
    state = request.app.state
    memory = getattr(state, "memory", None)
    knowledge = getattr(state, "knowledge", None)
    registry = getattr(state, "registry", None)
    tools = getattr(state, "tools", None)
    documents = knowledge.list_documents() if knowledge else []
    return {
        "memory": memory.counts() if memory else {},
        "documents": len(documents),
        "chunks": sum(d.chunk_count for d in documents),
        "providers": len(registry) if registry else 0,
        "tools": len(tools.names()) if tools else 0,
    }


@router.get("/admin/status")
async def admin_status(request: Request, _: User = Depends(require_admin)) -> dict:
    state = request.app.state
    settings = state.settings
    registry = getattr(state, "registry", None)
    knowledge = getattr(state, "knowledge", None)
    return {
        "version": __version__,
        "app": settings.app_name,
        "providers": registry.names() if registry else [],
        "default_provider": registry.default_name if registry else None,
        "semantic_search": bool(knowledge and knowledge.semantic_enabled),
        "docker_available": docker_available(),
        "settings": {name: getattr(settings, name) for name in PUBLIC_SETTINGS},
    }
