"""Настройки Kali. Читаются из переменных окружения с префиксом KALI_ (или из файла .env)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYSTEM_PROMPT = (
    "Ты — Kali, полезный ассистент. Отвечай на языке собеседника, по существу и без лишней воды. "
    "Если не уверен в факте, прямо скажи об этом."
)


class Settings(BaseSettings):
    """Конфигурация приложения. Секреты хранятся в SecretStr и не печатаются в логах."""

    model_config = SettingsConfigDict(env_prefix="KALI_", env_file=".env", extra="ignore")

    app_name: str = "Kali"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    # Пути к данным (относительно каталога запуска). ":memory:" — только для тестов.
    data_dir: str = "data"
    memory_db_path: str = ""

    # Провайдер моделей. Без ключа провайдер не подключается, API отвечает 503.
    provider_name: str = "default"
    provider_api_key: SecretStr | None = None
    provider_base_url: str = "https://api.chatanywhere.tech/v1"
    default_model: str = "gpt-4o-mini"
    provider_timeout_s: float = Field(default=120.0, gt=0, le=600)

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_input_chars: int = Field(default=100_000, ge=1000, le=1_000_000)
    short_term_chars: int = Field(default=12_000, ge=1000, le=200_000)

    # Память
    memory_enabled: bool = True
    memory_context_limit: int = Field(default=5, ge=0, le=20)

    # База знаний
    knowledge_enabled: bool = True
    knowledge_top_k: int = Field(default=5, ge=1, le=20)
    knowledge_max_upload_mb: int = Field(default=10, ge=1, le=50)
    knowledge_chunk_chars: int = Field(default=800, ge=200, le=4000)
    knowledge_chunk_overlap: int = Field(default=120, ge=0, le=800)
    embeddings_enabled: bool = True
    embedding_model: str = "text-embedding-3-small"

    # Инструменты
    workspace_dir: str = "data/workspace"
    sandbox_enabled: bool = True
    sandbox_image: str = "python:3.13-slim"
    sandbox_timeout_s: int = Field(default=10, ge=1, le=60)

    # Безопасность
    auth_enabled: bool = True
    auth_session_hours: int = Field(default=12, ge=1, le=168)
    login_attempts_per_minute: int = Field(default=10, ge=1, le=100)

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def database_path(self) -> str:
        """Путь к файлу базы данных. По умолчанию — data/kali.db."""
        if self.memory_db_path:
            return self.memory_db_path
        return os.path.join(self.data_dir, "kali.db")

    @field_validator("provider_api_key", mode="before")
    @classmethod
    def _blank_key_means_not_configured(cls, value: object) -> object:
        """Пустая строка в .env означает, что ключ не задан."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """Одна копия настроек на процесс."""
    return Settings()
