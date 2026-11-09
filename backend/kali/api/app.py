"""Фабрика FastAPI-приложения Kali."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kali import __version__
from kali.config.settings import Settings, get_settings
from kali.observability.logging import configure_logging

log = logging.getLogger("kali.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создаёт приложение. Настройки передаются явно (удобно для тестов)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )
    app.state.settings = settings

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
    async def health() -> dict[str, str]:
        """Проверка работоспособности. Не раскрывает секреты и внутренние пути."""
        return {"status": "ok", "version": __version__, "environment": settings.environment}

    return app
