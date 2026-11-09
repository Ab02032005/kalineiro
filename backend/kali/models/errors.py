"""Ошибки провайдеров и реестра моделей."""

from __future__ import annotations


class ProviderError(Exception):
    """Ошибка при обращении к провайдеру моделей.

    status_code — HTTP-код, который стоит вернуть клиенту (502 для сбоев связи и ответов,
    429 при лимите провайдера, и т.д.). В message не должно быть секретов.
    """

    def __init__(self, provider: str, status_code: int, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status_code = status_code
        self.message = message


class ProviderNotFoundError(LookupError):
    """Запрошен провайдер, которого нет в реестре."""
