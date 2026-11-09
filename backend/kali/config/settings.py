"""Настройки Kali. Читаются из переменных окружения с префиксом KALI_ (или из файла .env)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения. Секреты хранятся в SecretStr и не печатаются в логах."""

    model_config = SettingsConfigDict(env_prefix="KALI_", env_file=".env", extra="ignore")

    app_name: str = "Kali"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    # Пути к данным (относительно каталога запуска)
    data_dir: str = "data"

    # Секреты провайдеров моделей. Пустое значение = провайдер не подключён.
    provider_api_key: SecretStr | None = None
    provider_base_url: str = "https://api.chatanywhere.tech/v1"
    default_model: str = "gpt-4o-mini"

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Одна копия настроек на процесс."""
    return Settings()
