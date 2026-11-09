"""Защита персональных данных: не сохраняем то, что похоже на секреты и платёжные данные."""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("api_key", re.compile(r"\bgsk_[A-Za-z0-9]{16,}")),
    ("cloud_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("password", re.compile(r"(?i)\b(password|passwd|пароль|секрет)\s*[:=]\s*\S{4,}")),
    ("token", re.compile(r"(?i)\b(token|токен)\s*[:=]\s*\S{8,}")),
)
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def find_sensitive(text: str) -> str | None:
    """Возвращает тип найденного секрета или None. Сам найденный текст не возвращается."""
    for label, pattern in _PATTERNS:
        if pattern.search(text):
            return label
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return "card_number"
    return None
