"""Тесты интерфейса и служебных маршрутов: файлы рабочей папки, статистика, администрирование, раздача UI."""

import base64

from fastapi.testclient import TestClient

from kali.api.app import create_app
from kali.config.settings import Settings

SECRET = "sk-test-secret-value-1234567890"


def _client(tmp_path) -> TestClient:
    settings = Settings(
        environment="test",
        log_level="WARNING",
        memory_db_path=":memory:",
        workspace_dir=str(tmp_path / "ws"),
        sandbox_enabled=False,
        provider_api_key=SECRET,
    )
    return TestClient(create_app(settings))


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_upload_list_download_delete(tmp_path):
    client = _client(tmp_path)
    created = client.post("/v1/files", json={"filename": "заметка.md", "content_base64": _b64("Привет")})
    assert created.status_code == 201
    assert created.json()["item"]["name"] == "заметка.md"

    names = [f["name"] for f in client.get("/v1/files").json()["items"]]
    assert names == ["заметка.md"]

    download = client.get("/v1/files/" + "заметка.md")
    assert download.status_code == 200
    assert download.content == "Привет".encode()
    assert "attachment" in download.headers["content-disposition"]

    assert client.delete("/v1/files/" + "заметка.md").status_code == 204
    assert client.get("/v1/files").json()["items"] == []


def test_duplicate_name_is_not_overwritten(tmp_path):
    client = _client(tmp_path)
    client.post("/v1/files", json={"filename": "a.txt", "content_base64": _b64("один")})
    second = client.post("/v1/files", json={"filename": "a.txt", "content_base64": _b64("два")})
    assert second.json()["item"]["name"] != "a.txt"
    assert len(client.get("/v1/files").json()["items"]) == 2


def test_upload_rejects_dangerous_type_and_traversal(tmp_path):
    client = _client(tmp_path)
    assert client.post("/v1/files", json={"filename": "run.exe", "content_base64": _b64("x")}).status_code == 422
    # Имя очищается до базового: попытка выйти из папки не удаётся
    response = client.post("/v1/files", json={"filename": "../../evil.txt", "content_base64": _b64("x")})
    assert response.status_code == 201
    assert response.json()["item"]["name"] == "evil.txt"


def test_download_outside_workspace_is_refused(tmp_path):
    client = _client(tmp_path)
    # Путь с .. не доходит до файловой системы: маршрут его отклоняет (404 или 422)
    response = client.get("/v1/files/..%2Fsecret.txt")
    assert response.status_code in (404, 422)


def test_upload_rejects_bad_base64_and_empty(tmp_path):
    client = _client(tmp_path)
    assert client.post("/v1/files", json={"filename": "a.txt", "content_base64": "%%%"}).status_code == 422
    assert client.post("/v1/files", json={"filename": "a.txt", "content_base64": _b64("")}).status_code == 422


def test_stats_endpoint_counts(tmp_path):
    body = _client(tmp_path).get("/v1/stats").json()
    assert set(body) >= {"memory", "documents", "chunks", "providers", "tools"}
    assert body["providers"] == 1
    assert body["documents"] == 0


def test_admin_status_never_exposes_secrets(tmp_path):
    client = _client(tmp_path)
    response = client.get("/v1/admin/status")
    assert response.status_code == 200
    assert SECRET not in response.text
    assert "provider_api_key" not in response.json()["settings"]
    assert response.json()["providers"] == ["default"]


def test_ui_is_served_with_assets(tmp_path):
    client = _client(tmp_path)
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "/static/app.js" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200
