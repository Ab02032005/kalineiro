"""Маршруты базы знаний: загрузка документов, список, удаление и поиск."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from kali.knowledge.service import KnowledgeService
from kali.knowledge.store import KbDocument, KbHit

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


class DocumentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    # Содержимое файла в base64: так не нужен отдельный модуль для multipart
    content_base64: str = Field(min_length=1)


def _service(request: Request) -> KnowledgeService:
    service: KnowledgeService | None = getattr(request.app.state, "knowledge", None)
    if service is None:
        raise HTTPException(status_code=503, detail="База знаний не подключена")
    return service


def _doc_view(doc: KbDocument) -> dict:
    return {
        "id": doc.public_id,
        "title": doc.title,
        "filename": doc.filename,
        "size_bytes": doc.size_bytes,
        "chunks": doc.chunk_count,
        "embedded_chunks": doc.embedded_chunks,
        "created_at": doc.created_at,
    }


def _hit_view(hit: KbHit, service: KnowledgeService) -> dict:
    data = asdict(hit)
    data["content"] = service.excerpt(hit.content)
    return data


@router.post("/documents", status_code=201)
async def upload_document(body: DocumentIn, request: Request) -> dict:
    """Загрузка документа: TXT, MD, CSV, JSON, PDF, DOCX, XLSX и другие текстовые форматы."""
    service = _service(request)
    max_b64 = service.max_upload_bytes * 4 // 3 + 4
    if len(body.content_base64) > max_b64:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Содержимое файла должно быть в base64") from exc
    try:
        document, created = await service.add_document(body.filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"item": _doc_view(document), "created": created}


@router.get("/documents")
async def list_documents(request: Request) -> dict:
    service = _service(request)
    return {"items": [_doc_view(d) for d in service.list_documents()]}


@router.get("/documents/{public_id}")
async def get_document(public_id: str, request: Request) -> dict:
    service = _service(request)
    document = service.get_document(public_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return {"item": _doc_view(document)}


@router.delete("/documents/{public_id}", status_code=204)
async def delete_document(public_id: str, request: Request) -> None:
    service = _service(request)
    if not service.delete_document(public_id):
        raise HTTPException(status_code=404, detail="Документ не найден")


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    service = _service(request)
    hits = await service.search(q, limit=limit)
    return {"items": [_hit_view(h, service) for h in hits], "semantic": service.semantic_enabled}
