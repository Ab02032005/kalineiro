"""Тестовые двойники провайдеров. Используются только в тестах, в работе не подключаются."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator

from kali.knowledge.embeddings import EmbeddingError
from kali.models.base import ChatRequest
from kali.models.errors import ProviderError


class FakeProvider:
    """Возвращает заранее заданный ответ и запоминает запросы."""

    def __init__(self, name: str = "fake", reply: str = "ответ", chunks: list[str] | None = None,
                 fail_with: ProviderError | None = None) -> None:
        self.name = name
        self.reply = reply
        self.chunks = chunks if chunks is not None else [reply]
        self.fail_with = fail_with
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> str:
        self.requests.append(request)
        if self.fail_with:
            raise self.fail_with
        return self.reply

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        return self._gen()

    async def _gen(self) -> AsyncIterator[str]:
        if self.fail_with:
            raise self.fail_with
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class FakeEmbedder:
    """Детерминированные векторы по словам (хеш-корзины). Похожие тексты дают близкие векторы."""

    DIMS = 64

    def __init__(self, model: str = "fake-embed", fail: bool = False) -> None:
        self.model = model
        self.fail = fail
        self.calls = 0

    @classmethod
    def vector(cls, text: str) -> list[float]:
        vec = [0.0] * cls.DIMS
        for word in re.findall(r"\w+", text.lower()):
            bucket = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16) % cls.DIMS
            vec[bucket] += 1.0
        return vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise EmbeddingError("тестовый сбой")
        return [self.vector(t) for t in texts]

    async def aclose(self) -> None:
        return None
