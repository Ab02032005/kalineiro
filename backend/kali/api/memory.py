"""Маршруты памяти: просмотр, поиск, добавление, правка, удаление и выгрузка данных пользователя."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from kali.memory.models import KINDS, MemoryItem, MemoryRejected
from kali.memory.service import MemoryService

router = APIRouter(prefix="/v1/memories", tags=["memory"])


class MemoryIn(BaseModel):
    kind: Literal["fact", "procedure"]
    content: str = Field(min_length=1, max_length=2000)


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


def _service(request: Request) -> MemoryService:
    service: MemoryService | None = getattr(request.app.state, "memory", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Память не подключена")
    return service


def _view(item: MemoryItem) -> dict:
    return {
        "id": item.public_id,
        "kind": item.kind,
        "content": item.content,
        "source": item.source,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "last_used_at": item.last_used_at,
    }


def _rejected(exc: MemoryRejected) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("")
async def list_memories(
    request: Request,
    kind: Literal["fact", "episode", "procedure"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    service = _service(request)
    items = service.list(kind, limit, offset)
    return {"items": [_view(i) for i in items], "limit": limit, "offset": offset}


@router.get("/search")
async def search_memories(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    kind: Literal["fact", "episode", "procedure"] | None = None,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    service = _service(request)
    kinds = (kind,) if kind else KINDS
    items = service.search(q, kinds=kinds, limit=limit)
    return {"items": [_view(i) for i in items]}


@router.post("", status_code=201)
async def create_memory(body: MemoryIn, request: Request) -> dict:
    service = _service(request)
    try:
        item, created = service.add(body.kind, body.content, source="user")
    except MemoryRejected as exc:
        raise _rejected(exc) from exc
    return {"item": _view(item), "created": created}


@router.patch("/{public_id}")
async def update_memory(public_id: str, body: MemoryUpdate, request: Request) -> dict:
    service = _service(request)
    try:
        item = service.update(public_id, body.content)
    except MemoryRejected as exc:
        raise _rejected(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"item": _view(item)}


@router.delete("/{public_id}", status_code=204)
async def delete_memory(public_id: str, request: Request) -> None:
    service = _service(request)
    if not service.delete(public_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")


@router.delete("")
async def wipe_memories(
    request: Request,
    confirm: bool = Query(default=False),
    kind: Literal["fact", "episode", "procedure"] | None = None,
) -> dict:
    """Полная очистка. Без confirm=true не выполняется: это необратимо."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Для очистки передайте confirm=true")
    service = _service(request)
    return {"deleted": service.wipe(kind)}


@router.get("/export")
async def export_memories(request: Request) -> dict:
    """Полная выгрузка всех записей пользователя в JSON."""
    return _service(request).export()
