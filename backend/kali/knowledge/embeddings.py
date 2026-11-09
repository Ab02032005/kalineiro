"""Эмбеддинги: векторы смысла для поиска по базе знаний."""

from __future__ import annotations

import logging
import math
from typing import Protocol, runtime_checkable

import httpx

log = logging.getLogger("kali.knowledge.embeddings")

BATCH_SIZE = 64


class EmbeddingError(Exception):
    """Не удалось получить эмбеддинги. Поиск продолжает работать по словам."""


@runtime_checkable
class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class OpenAIEmbedder:
    """Эмбеддинги через OpenAI-совместимый endpoint /embeddings."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.post("/embeddings", json={"model": self.model, "input": texts})
        except httpx.HTTPError as exc:
            raise EmbeddingError("нет связи с сервисом эмбеддингов") from exc
        if response.status_code >= 400:
            raise EmbeddingError(f"сервис эмбеддингов вернул {response.status_code}")
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("неожиданный формат ответа сервиса эмбеддингов") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError("количество векторов не совпадает с количеством текстов")
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            result.extend(await self._embed_batch(texts[start:start + BATCH_SIZE]))
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def cosine(a: list[float], b: list[float]) -> float:
    """Косинусная близость двух векторов. Нулевой вектор даёт 0."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
