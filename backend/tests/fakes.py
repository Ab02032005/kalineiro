"""Тестовые двойники провайдера. Используются только в тестах, в работе не подключаются."""

from __future__ import annotations

from collections.abc import AsyncIterator

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
