"""Пароли: хранение только в виде scrypt-хэша с солью. Сравнение без утечки времени."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
_PREFIX = "scrypt"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"{_PREFIX}${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        prefix, n, r, p, salt_hex, digest_hex = stored.split("$")
        if prefix != _PREFIX:
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest_hex)


# Заглушка для проверки, когда пользователя нет: время ответа не выдаёт существование логина
DUMMY_HASH = hash_password("не-существующий-пользователь")
