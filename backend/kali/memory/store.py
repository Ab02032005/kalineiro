"""Хранилище памяти на SQLite. Поиск по тексту — через FTS5 (BM25)."""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime

from kali.memory.models import Kind, MemoryItem

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'episode', 'procedure')),
    content TEXT NOT NULL,
    content_key TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_kind_key ON memories (kind, content_key);
CREATE INDEX IF NOT EXISTS idx_memories_kind_updated ON memories (kind, updated_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def content_key(text: str) -> str:
    """Ключ для поиска дубликатов: нижний регистр с учётом кириллицы (SQLite этого не умеет)."""
    return " ".join(text.split()).lower()


def fts_query(text: str) -> str | None:
    """Превращает произвольный текст в безопасное выражение FTS5 (каждое слово — префиксный поиск)."""
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return None
    return " OR ".join(f'"{token}"*' for token in tokens[:20])


class SqliteMemoryStore:
    """Низкоуровневые операции с таблицей памяти. Бизнес-правила — в MemoryService."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _item(row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            public_id=row["public_id"],
            kind=row["kind"],
            content=row["content"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
        )

    def insert(self, kind: Kind, content: str, source: str) -> MemoryItem:
        now = _now()
        public_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (public_id, kind, content, content_key, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (public_id, kind, content, content_key(content), source, now, now),
            )
            self._conn.commit()
        found = self.get(public_id)
        assert found is not None
        return found

    def get(self, public_id: str) -> MemoryItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE public_id = ?", (public_id,)
            ).fetchone()
        return self._item(row) if row else None

    def find_duplicate(self, kind: Kind, content: str) -> MemoryItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE kind = ? AND content_key = ? LIMIT 1",
                (kind, content_key(content)),
            ).fetchone()
        return self._item(row) if row else None

    def list(self, kind: Kind | None, limit: int, offset: int) -> list[MemoryItem]:
        sql = "SELECT * FROM memories"
        params: list = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._item(r) for r in rows]

    def search(self, text: str, kinds: tuple[Kind, ...] | None, limit: int) -> list[MemoryItem]:
        match = fts_query(text)
        if match is None:
            return []
        sql = (
            "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ?"
        )
        params: list = [match]
        if kinds:
            sql += f" AND m.kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY bm25(memories_fts) LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._item(r) for r in rows]

    def update_content(self, public_id: str, content: str) -> MemoryItem | None:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE memories SET content = ?, content_key = ?, updated_at = ? WHERE public_id = ?",
                (content, content_key(content), _now(), public_id),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.get(public_id)

    def touch(self, public_ids: list[str]) -> None:
        if not public_ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE memories SET last_used_at = ? WHERE public_id = ?",
                [(_now(), pid) for pid in public_ids],
            )
            self._conn.commit()

    def delete(self, public_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memories WHERE public_id = ?", (public_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_kinds(self, kinds: tuple[Kind, ...] | None) -> int:
        with self._lock:
            if kinds:
                cursor = self._conn.execute(
                    f"DELETE FROM memories WHERE kind IN ({','.join('?' * len(kinds))})", kinds
                )
            else:
                cursor = self._conn.execute("DELETE FROM memories")
            self._conn.commit()
            return cursor.rowcount

    def count_by_kind(self) -> dict[str, int]:
        """Количество записей по видам памяти."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM memories GROUP BY kind"
            ).fetchall()
        return {row["kind"]: row["n"] for row in rows}

    def export_all(self) -> list[MemoryItem]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
        return [self._item(r) for r in rows]
