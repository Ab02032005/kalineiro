"""Фабрика FastAPI-приложения Kali: сборка провайдеров, памяти, ядра и маршрутов."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kali import __version__
from kali.api.chat import router as chat_router
from kali.api.memory import router as memory_router
from kali.config.settings import Settings, get_settings
from kali.core.engine import CoreEngine, EngineLimits
from kali.memory.service import MemoryService
from kali.memory.store import SqliteMemoryStore
from kali.models.openai_compatible import OpenAICompatibleProvider
from kali.models.registry import ProviderRegistry
from kali.observability.logging import configure_logging

log = logging.getLogger("kali.api")


def build_registry(settings: Settings) -> ProviderRegistry:
    """Подключает провайдер из настроек. Без ключа реестр остаётся пустым."""
    registry = ProviderRegistry()
    if settings.provider_api_key is not None:
        provider = OpenAICompatibleProvider(
            name=settings.provider_name,
            base_url=settings.provider_base_url,
            api_key=settings.provider_api_key.get_secret_value(),
            timeout_s=settings.provider_timeout_s,
        )
        registry.register(provider, default=True)
        log.info("provider registered", extra={"provider": settings.provider_name})
    else:
        log.warning("no provider key configured; chat endpoints will return 503")
    return registry


def build_memory(settings: Settings) -> tuple[SqliteMemoryStore, MemoryService]:
    """Открывает базу памяти. Для ':memory:' база живёт только в процессе (тесты)."""
    path = settings.database_path
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    store = SqliteMemoryStore(path)
    service = MemoryService(
        store,
        context_limit=settings.memory_context_limit,
        enabled=settings.memory_enabled,
    )
    return store, service


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    memory: MemoryService | None = None,
) -> FastAPI:
    """Создаёт приложение. В тестах можно передать свои реестр провайдеров и память."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    registry = registry if registry is not None else build_registry(settings)

    owned_store: SqliteMemoryStore | None = None
    if memory is None:
        owned_store, memory = build_memory(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await registry.close_all()
        if owned_store is not None:
            owned_store.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.memory = memory
    app.state.engine = (
        CoreEngine(
            registry,
            system_prompt=settings.system_prompt,
            default_model=settings.default_model,
            limits=EngineLimits(
                max_input_chars=settings.max_input_chars,
                short_term_chars=settings.short_term_chars,
            ),
            memory=memory,
        )
        if len(registry) > 0
        else None
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Присваивает каждому запросу ID и пишет его длительность в лог."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "request_id": request_id},
            )
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        """Проверка работоспособности. Не раскрывает секреты и внутренние пути."""
        return {
            "status": "ok",
            "version": __version__,
            "environment": settings.environment,
            "providers": len(registry),
            "memory": memory.enabled if memory else False,
        }

    app.include_router(chat_router)
    app.include_router(memory_router)
    return app
