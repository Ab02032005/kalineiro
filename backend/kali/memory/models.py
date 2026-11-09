"""Модели памяти и правила допустимого содержимого."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# fact — устойчивые сведения, которые пользователь разрешил помнить (семантическая память)
# episode — краткая запись о прошлом диалоге (эпизодическая память)
# procedure — сохранённый способ решения задачи (процедурная память)
Kind = Literal["fact", "episode", "procedure"]

KINDS: tuple[Kind, ...] = ("fact", "episode", "procedure")
MANUAL_KINDS: tuple[Kind, ...] = ("fact", "procedure")  # эпизоды создаются только системой
MAX_CONTENT_CHARS = 2000


@dataclass(frozen=True, slots=True)
class MemoryItem:
    public_id: str
    kind: Kind
    content: str
    source: str
    created_at: str
    updated_at: str
    last_used_at: str | None


class MemoryRejected(ValueError):
    """Запись отклонена правилами памяти. Сообщение не содержит самого текста записи."""
