"""Вход, выход, пользователи и журнал. Также здесь — проверки доступа для остальных маршрутов."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from kali.security.auth import AuthService, User

router = APIRouter(prefix="/v1", tags=["auth"])

LOCAL_USER = User(id=0, username="local", role="admin", active=True, created_at="")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=256)
    role: Literal["admin", "user"] = "user"


def auth_service(request: Request) -> AuthService:
    service: AuthService | None = getattr(request.app.state, "auth", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Служба входа не подключена")
    return service


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def require_user(request: Request) -> User:
    """Пропускает только вошедших пользователей. Токен передаётся в заголовке Authorization."""
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return LOCAL_USER
    header = request.headers.get("Authorization", "")
    token = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    user = auth_service(request).resolve(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Нужно войти в систему",
                            headers={"WWW-Authenticate": "Bearer"})
    request.state.user = user
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return user


@router.post("/auth/login")
async def login(body: LoginIn, request: Request) -> dict:
    limiter = request.app.state.login_limiter
    ip = _client_ip(request)
    if not limiter.allow(ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Подождите минуту.")
    service = auth_service(request)
    user = service.authenticate(body.username, body.password)
    if user is None:
        service.audit(body.username, "login_failed", "", ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token, expires_at = service.create_session(user)
    service.audit(user.username, "login", "", ip)
    return {"token": token, "expires_at": expires_at, "username": user.username, "role": user.role}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, user: User = Depends(require_user)) -> None:
    header = request.headers.get("Authorization", "")
    service = auth_service(request)
    service.revoke(header[len("Bearer "):] if header.startswith("Bearer ") else "")
    service.audit(user.username, "logout", "", _client_ip(request))


@router.get("/auth/me")
async def me(user: User = Depends(require_user)) -> dict:
    return {"username": user.username, "role": user.role}


@router.get("/users")
async def list_users(request: Request, _: User = Depends(require_admin)) -> dict:
    return {"items": [
        {"id": u.id, "username": u.username, "role": u.role, "active": u.active, "created_at": u.created_at}
        for u in auth_service(request).list_users()
    ]}


@router.post("/users", status_code=201)
async def create_user(body: UserIn, request: Request, admin: User = Depends(require_admin)) -> dict:
    service = auth_service(request)
    try:
        user = service.create_user(body.username, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    service.audit(admin.username, "user_created", f"{user.username} ({user.role})", _client_ip(request))
    return {"item": {"id": user.id, "username": user.username, "role": user.role}}


@router.get("/admin/audit")
async def audit_log(request: Request, _: User = Depends(require_admin)) -> dict:
    return {"items": auth_service(request).recent_audit(100)}
