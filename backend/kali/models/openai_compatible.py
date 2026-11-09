"""Адаптер для OpenAI-совместимых API (ChatAnywhere, Groq, OpenRouter, Ollama и др.)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from kali.models.base import ChatRequest, ChatResult, Message, ToolCall
from kali.models.errors import ProviderError

log = logging.getLogger("kali.models.openai")

_ERROR_PREVIEW_CHARS = 300


def _serialize_message(message: Message) -> dict:
    data: dict = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["content"] = message.content or None
        data["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in message.tool_calls
        ]
    if message.role == "tool":
        data["tool_call_id"] = message.tool_call_id
        data["name"] = message.name
    return data


class OpenAICompatibleProvider:
    """Реализует LLMProvider для любого сервиса с endpoint /chat/completions.

    transport передаётся в тестах (httpx.MockTransport), в работе не задаётся.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        *,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    def _payload(self, request: ChatRequest, stream: bool) -> dict:
        body: dict = {
            "model": request.model,
            "messages": [_serialize_message(m) for m in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.tools and not stream:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}}
                for t in request.tools
            ]
        return body

    def _error(self, status: int, text: str) -> ProviderError:
        preview = text[:_ERROR_PREVIEW_CHARS]
        if status == 429:
            return ProviderError(self.name, 429, "Лимит запросов провайдера исчерпан")
        return ProviderError(self.name, 502, f"Провайдер вернул ошибку {status}: {preview}")

    async def _post(self, request: ChatRequest) -> dict:
        try:
            response = await self._client.post("/chat/completions", json=self._payload(request, False))
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, 502, f"Нет связи с провайдером: {exc}") from exc
        if response.status_code >= 400:
            raise self._error(response.status_code, response.text)
        try:
            return response.json()["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(self.name, 502, "Неожиданный формат ответа провайдера") from exc

    async def complete(self, request: ChatRequest) -> str:
        message = await self._post(request)
        return message.get("content") or ""

    async def complete_with_tools(self, request: ChatRequest) -> ChatResult:
        message = await self._post(request)
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            try:
                calls.append(ToolCall(
                    id=str(raw["id"]),
                    name=str(raw["function"]["name"]),
                    arguments=str(raw["function"].get("arguments") or "{}"),
                ))
            except (KeyError, TypeError) as exc:
                raise ProviderError(self.name, 502, "Неожиданный формат вызова инструмента") from exc
        return ChatResult(text=message.get("content") or "", tool_calls=tuple(calls))

    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=self._payload(request, True)
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._error(response.status_code, body)
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError:
                        log.warning("unparsable stream event", extra={"provider": self.name})
                        continue
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, 502, f"Обрыв потока от провайдера: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
