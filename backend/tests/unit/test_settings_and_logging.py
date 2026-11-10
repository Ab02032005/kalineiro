"""Тесты настроек и JSON-логов."""

import json
import logging

from kali.config.settings import Settings
from kali.observability.logging import JsonFormatter


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("KALI_PORT", "9001")
    monkeypatch.setenv("KALI_ENVIRONMENT", "prod")
    settings = Settings(auth_enabled=False, )
    assert settings.port == 9001
    assert settings.is_prod


def test_api_key_is_secret_and_not_printed(monkeypatch):
    monkeypatch.setenv("KALI_PROVIDER_API_KEY", "sk-secret-value")
    settings = Settings(auth_enabled=False, )
    assert "sk-secret-value" not in repr(settings)
    assert "sk-secret-value" not in str(settings)
    assert settings.provider_api_key.get_secret_value() == "sk-secret-value"


def test_invalid_port_is_rejected(monkeypatch):
    import pytest
    from pydantic import ValidationError

    monkeypatch.setenv("KALI_PORT", "99999")
    with pytest.raises(ValidationError):
        Settings(auth_enabled=False, )


def test_json_formatter_outputs_valid_json_with_extra_fields():
    record = logging.LogRecord("kali.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.request_id = "r1"
    line = JsonFormatter().format(record)
    data = json.loads(line)
    assert data["msg"] == "hello world"
    assert data["level"] == "INFO"
    assert data["request_id"] == "r1"
    assert "ts" in data


def test_blank_provider_key_means_not_configured(monkeypatch):
    monkeypatch.setenv("KALI_PROVIDER_API_KEY", "")
    assert Settings(auth_enabled=False, ).provider_api_key is None

