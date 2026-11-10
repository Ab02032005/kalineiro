"""Пользователи, сессии и журнал событий безопасности (SQLite)."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kali.security.passwords import DUMMY_HASH, hash_password, verify_password

ROLES = ("admin", "user")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
MIN_PASSWORD = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    ip TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str
    role: str
    active: bool
    created_at: str


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, path: str, *, session_hours: int = 12) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._session_hours = session_hours
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _user(row: sqlite3.Row) -> User:
        return User(id=row["id"], username=row["username"], role=row["role"],
                    active=bool(row["active"]), created_at=row["created_at"])

    # ---- пользователи ----

    def create_user(self, username: str, password: str, role: str) -> User:
        if not USERNAME_RE.match(username):
            raise ValueError("Логин: 3–32 символа, латинские буквы, цифры и . _ -")
        if role not in ROLES:
            raise ValueError("Роль: admin или user")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD} символов")
        if password.lower() == username.lower():
            raise ValueError("Пароль не должен совпадать с логином")
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), role, _iso(_now())),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Пользователь с таким логином уже есть") from exc
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._user(row)

    def list_users(self) -> list[User]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._user(r) for r in rows]

    def user_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def authenticate(self, username: str, password: str) -> User | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        stored = row["password_hash"] if row else DUMMY_HASH
        ok = verify_password(password, stored)
        if row is None or not ok or not row["active"]:
            return None
        return self._user(row)

    # ---- сессии ----

    def create_session(self, user: User) -> tuple[str, str]:
        """Возвращает (токен, срок окончания). Токен показывается один раз и в базе не хранится."""
        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(hours=self._session_hours)
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_token_hash(token), user.id, _iso(_now()), _iso(expires)),
            )
            self._conn.commit()
        return token, _iso(expires)

    def resolve(self, token: str) -> User | None:
        if not token or len(token) > 200:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            if datetime.fromisoformat(row["expires_at"]) < _now() or not row["active"]:
                self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
                self._conn.commit()
                return None
        return self._user(row)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
            self._conn.commit()

    # ---- журнал ----

    def audit(self, actor: str, action: str, detail: str = "", ip: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_events (ts, actor, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
                (_iso(_now()), actor[:64], action[:64], detail[:300], ip[:64]),
            )
            self._conn.commit()

    def recent_audit(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
