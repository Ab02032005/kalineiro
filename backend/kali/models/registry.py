"""Реестр провайдеров моделей: регистрация, выбор по имени, закрытие соединений."""

from __future__ import annotations

from kali.models.base import LLMProvider
from kali.models.errors import ProviderNotFoundError


class ProviderRegistry:
    """Хранит подключённые провайдеры. Имена уникальны, один провайдер — по умолчанию."""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._default: str | None = None

    def register(self, provider: LLMProvider, *, default: bool = False) -> None:
        if provider.name in self._providers:
            raise ValueError(f"Провайдер с именем {provider.name!r} уже зарегистрирован")
        self._providers[provider.name] = provider
        if default or self._default is None:
            self._default = provider.name

    def get(self, name: str | None = None) -> LLMProvider:
        key = name or self._default
        if key is None:
            raise ProviderNotFoundError("Нет ни одного подключённого провайдера")
        try:
            return self._providers[key]
        except KeyError:
            raise ProviderNotFoundError(f"Провайдер {key!r} не подключён") from None

    @property
    def default_name(self) -> str | None:
        return self._default

    def names(self) -> list[str]:
        return list(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    async def close_all(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
