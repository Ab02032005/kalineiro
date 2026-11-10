"""Тесты безопасности: вход, роли, защита маршрутов, ограничение попыток, журнал, заголовки."""

from fastapi.testclient import TestClient

from kali.api.app import create_app
from kali.config.settings import Settings
from kali.security.auth import AuthService
from kali.security.passwords import hash_password, verify_password

ADMIN = ("admin", "очень-длинный-пароль-1")
USER = ("alice", "другой-длинный-пароль-2")


def _app(attempts: int = 10):
    settings = Settings(
        environment="test", log_level="WARNING", memory_db_path=":memory:",
        auth_enabled=True, login_attempts_per_minute=attempts,
    )
    app = create_app(settings)
    auth: AuthService = app.state.auth
    auth.create_user(*ADMIN, role="admin")
    auth.create_user(*USER, role="user")
    return app


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


# ---- пароли ----

def test_password_is_stored_as_hash_and_verified():
    stored = hash_password("секрет-секрет-1")
    assert "секрет-секрет-1" not in stored
    assert verify_password("секрет-секрет-1", stored) is True
    assert verify_password("неверный", stored) is False
    assert verify_password("x", "не-хэш") is False


# ---- защита маршрутов ----

def test_public_routes_stay_open():
    client = TestClient(_app())
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_api_requires_login():
    client = TestClient(_app())
    for method, path in [
        ("get", "/v1/memories"), ("get", "/v1/knowledge/documents"), ("get", "/v1/files"),
        ("get", "/v1/stats"), ("get", "/v1/admin/status"), ("get", "/v1/users"),
        ("post", "/v1/chat"), ("get", "/v1/providers"),
    ]:
        response = client.request(method.upper(), path, json={} if method == "post" else None)
        assert response.status_code == 401, path


def test_wrong_token_is_rejected():
    client = TestClient(_app())
    assert client.get("/v1/memories", headers=_auth("forged-token-123")).status_code == 401


# ---- вход ----

def test_login_returns_token_and_me_works():
    client = TestClient(_app())
    body = _login(client, *USER)
    assert body["role"] == "user" and body["token"]
    me = client.get("/v1/auth/me", headers=_auth(body["token"]))
    assert me.json() == {"username": "alice", "role": "user"}


def test_wrong_password_and_unknown_user_look_the_same():
    client = TestClient(_app())
    wrong = client.post("/v1/auth/login", json={"username": USER[0], "password": "не-тот-пароль"})
    unknown = client.post("/v1/auth/login", json={"username": "nobody", "password": "не-тот-пароль"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_logout_revokes_token():
    client = TestClient(_app())
    token = _login(client, *USER)["token"]
    assert client.post("/v1/auth/logout", headers=_auth(token)).status_code == 204
    assert client.get("/v1/auth/me", headers=_auth(token)).status_code == 401


def test_expired_session_is_rejected():
    app = _app()
    client = TestClient(app)
    token = _login(client, *USER)["token"]
    # Принудительно устаревает сессия в базе
    app.state.auth._conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    app.state.auth._conn.commit()
    assert client.get("/v1/auth/me", headers=_auth(token)).status_code == 401


def test_login_attempts_are_rate_limited():
    client = TestClient(_app(attempts=3))
    for _ in range(3):
        client.post("/v1/auth/login", json={"username": "x", "password": "y"})
    response = client.post("/v1/auth/login", json={"username": "x", "password": "y"})
    assert response.status_code == 429


# ---- роли ----

def test_user_cannot_use_admin_endpoints():
    client = TestClient(_app())
    token = _login(client, *USER)["token"]
    assert client.get("/v1/users", headers=_auth(token)).status_code == 403
    assert client.get("/v1/admin/status", headers=_auth(token)).status_code == 403
    assert client.get("/v1/admin/audit", headers=_auth(token)).status_code == 403
    assert client.post("/v1/users", headers=_auth(token),
                       json={"username": "bob", "password": "новый-пароль-длинный", "role": "admin"}).status_code == 403


def test_admin_can_create_users_and_see_audit():
    client = TestClient(_app())
    token = _login(client, *ADMIN)["token"]
    created = client.post("/v1/users", headers=_auth(token),
                          json={"username": "bob", "password": "новый-пароль-длинный", "role": "user"})
    assert created.status_code == 201
    names = [u["username"] for u in client.get("/v1/users", headers=_auth(token)).json()["items"]]
    assert "bob" in names
    audit = client.get("/v1/admin/audit", headers=_auth(token)).json()["items"]
    actions = {event["action"] for event in audit}
    assert {"login", "user_created"} <= actions


def test_user_creation_rules():
    client = TestClient(_app())
    token = _login(client, *ADMIN)["token"]
    short = client.post("/v1/users", headers=_auth(token), json={"username": "bob", "password": "коротко"})
    assert short.status_code == 422
    same = client.post("/v1/users", headers=_auth(token),
                       json={"username": "carolcarol1", "password": "carolcarol1"})
    assert same.status_code == 422
    duplicate = client.post("/v1/users", headers=_auth(token),
                            json={"username": USER[0], "password": "другой-длинный-пароль-9"})
    assert duplicate.status_code == 422


def test_failed_logins_are_audited():
    client = TestClient(_app())
    client.post("/v1/auth/login", json={"username": "admin", "password": "плохо-плохо-плохо"})
    token = _login(client, *ADMIN)["token"]
    events = client.get("/v1/admin/audit", headers=_auth(token)).json()["items"]
    assert any(e["action"] == "login_failed" and e["actor"] == "admin" for e in events)


# ---- заголовки ----

def test_security_headers_are_set():
    client = TestClient(_app())
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_api_responses_are_not_cached():
    client = TestClient(_app())
    token = _login(client, *USER)["token"]
    response = client.get("/v1/auth/me", headers=_auth(token))
    assert response.headers["Cache-Control"] == "no-store"
