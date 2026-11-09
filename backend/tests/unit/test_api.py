"""Тесты API: проверка /health, заголовков и обработки ошибок."""

from fastapi.testclient import TestClient

from kali import __version__
from kali.api.app import create_app
from kali.config.settings import Settings


def _client(environment: str = "test") -> TestClient:
    return TestClient(create_app(Settings(environment=environment, log_level="WARNING")))


def test_health_returns_ok_and_version():
    response = _client().get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"


def test_request_id_is_generated_and_echoed():
    response = _client().get("/health")
    assert len(response.headers["X-Request-ID"]) == 32


def test_request_id_from_client_is_preserved():
    response = _client().get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_docs_disabled_in_prod():
    client = _client(environment="prod")
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_enabled_in_dev():
    assert _client(environment="dev").get("/docs").status_code == 200


def test_unknown_route_returns_404():
    assert _client().get("/nope").status_code == 404
