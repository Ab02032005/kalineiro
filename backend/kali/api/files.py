"""Маршруты файлов рабочей папки: загрузка, список, скачивание и удаление."""

from __future__ import annotations

import base64
import binascii
import os
import time
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from kali.tools.builtin import WRITABLE_EXTENSIONS, Workspace

router = APIRouter(prefix="/v1/files", tags=["files"])

UPLOAD_EXTENSIONS = WRITABLE_EXTENSIONS | {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".xlsx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class FileUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1)


def _workspace(request: Request) -> Workspace:
    workspace: Workspace | None = getattr(request.app.state, "workspace", None)
    if workspace is None:
        raise HTTPException(status_code=503, detail="Рабочая папка не подключена")
    return workspace


def _resolve(workspace: Workspace, name: str) -> Path:
    try:
        return workspace.path_for(name)
    except Exception as exc:  # ToolError с понятным текстом
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
async def list_files(request: Request) -> dict:
    root = _workspace(request).root
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(root.iterdir()):
        if path.is_file():
            stat = path.stat()
            items.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
            })
    return {"items": items}


@router.post("", status_code=201)
async def upload_file(body: FileUpload, request: Request) -> dict:
    workspace = _workspace(request)
    name = os.path.basename(body.filename.strip())
    extension = Path(name).suffix.lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Такой тип файла не поддерживается")
    if len(body.content_base64) > MAX_UPLOAD_BYTES * 4 // 3 + 4:
        raise HTTPException(status_code=413, detail="Файл слишком большой (максимум 10 МБ)")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Содержимое файла должно быть в base64") from exc
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="Файл пустой или слишком большой")

    target = _resolve(workspace, name)
    if target.exists():  # не перезаписываем молча: добавляем метку времени
        stem, suffix = os.path.splitext(name)
        name = f"{stem}_{int(time.time())}{suffix}"
        target = _resolve(workspace, name)
    workspace.root.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"item": {"name": name, "size_bytes": len(data)}}


@router.get("/{name}")
async def download_file(name: str, request: Request) -> Response:
    target = _resolve(_workspace(request), urllib.parse.unquote(name))
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return Response(
        content=target.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(target.name)}"},
    )


@router.delete("/{name}", status_code=204)
async def delete_file(name: str, request: Request) -> None:
    target = _resolve(_workspace(request), urllib.parse.unquote(name))
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    os.remove(target)
