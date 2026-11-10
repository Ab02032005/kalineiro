"""Раздача веб-интерфейса Kali: главная страница и статические файлы."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
STATIC_DIR = WEB_DIR / "static"

router = APIRouter(include_in_schema=False)


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})
