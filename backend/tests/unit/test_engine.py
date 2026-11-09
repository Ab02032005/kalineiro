"""Тесты ядра и реестра провайдеров."""

import asyncio

import pytest

from kali.core.engine import CoreEngine, EngineLimits
from kali.models.base import Message
from kali.models.errors import ProviderNotFoundError
from kali.models.registry import ProviderRegistry
from tests.fakes import FakeProvider


def _engine(registry: ProviderRegistry, max_chars: int = 1000) -> CoreEngine:
    return CoreEngine(
        registry,
        system_prompt="SYSTEM",
        default_model="default-model",
        limits=EngineLimits(max_input_chars=max_chars),
    )


def _user(text: str = "привет") -> list[Message]:
    return [Message(role="user", content=text)]


def test_registry_default_is_first_registered_unless_overridden():
    registry = ProviderRegistry()
    registry.register(FakeProvider("a"))
    registry.register(FakeProvider("b"))
    assert registry.default_name == "a"
    registry.register(FakeProvider("c"), default=True)
    assert registry.default_name == "c"
    assert registry.names() == ["a", "b", "c"]


def test_registry_rejects_duplicate_names_and_unknown_lookup():
    registry = ProviderRegistry()
    registry.register(FakeProvider("a"))
    with pytest.raises(ValueError):
        registry.register(FakeProvider("a"))
    with pytest.raises(ProviderNotFoundError):
        registry.get("zzz")


def test_empty_registry_has_no_provider():
    with pytest.raises(ProviderNotFoundError):
        ProviderRegistry().get()


def test_engine_adds_system_prompt_and_default_model():
    provider = FakeProvider(reply="ok")
    registry = ProviderRegistry()
    registry.register(provider)
    reply = asyncio.run(_engine(registry).answer(_user()))

    assert reply == "ok"
    request = provider.requests[0]
    assert request.model == "default-model"
    assert request.messages[0] == Message(role="system", content="SYSTEM")
    assert request.messages[-1] == Message(role="user", content="привет")


def test_engine_keeps_existing_system_message():
    provider = FakeProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    messages = [Message(role="system", content="own"), Message(role="user", content="x")]
    asyncio.run(_engine(registry).answer(messages))
    assert [m.content for m in provider.requests[0].messages] == ["own", "x"]


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [Message(role="assistant", content="я последний")],
    ],
)
def test_engine_rejects_invalid_dialogs(messages):
    provider = FakeProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    with pytest.raises(ValueError):
        asyncio.run(_engine(registry).answer(messages))
    assert provider.requests == []


def test_engine_rejects_too_long_input():
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    with pytest.raises(ValueError):
        asyncio.run(_engine(registry, max_chars=10).answer(_user("x" * 50)))


def test_engine_stream_validates_before_streaming():
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    with pytest.raises(ValueError):
        _engine(registry).answer_stream([])


def test_engine_stream_yields_chunks():
    registry = ProviderRegistry()
    registry.register(FakeProvider(chunks=["а", "б", "в"]))

    async def collect():
        return [c async for c in _engine(registry).answer_stream(_user())]

    assert asyncio.run(collect()) == ["а", "б", "в"]
