"""Хранилище базы знаний: документы, фрагменты, полнотекстовый индекс и векторы (SQLite)."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime

from kali.memory.store import fts_query


@dataclass(frozen=True, slots=True)
class KbDocument:
    public_id: str
    title: str
    filename: str
    size_bytes: int
    chunk_count: int
    embedded_chunks: int
    created_at: str


@dataclass(frozen=True, slots=True)
class KbHit:
    chunk_id: int
    document_id: str
    title: str
    chunk_index: int
    content: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding BLOB,
    embedding_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks (doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
    content,
    content='kb_chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS kb_ai AFTER INSERT ON kb_chunks BEGIN
    INSERT INTO kb_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS kb_ad AFTER DELETE ON kb_chunks BEGIN
    INSERT INTO kb_fts(kb_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def pack_vector(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


class SqliteKnowledgeStore:
    """Таблицы базы знаний. Использует тот же файл, что и память, но свои таблицы."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- документы ----

    def find_by_hash(self, sha256: str) -> KbDocument | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT public_id FROM kb_documents WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return self.get(row["public_id"]) if row else None

    def insert_document(
        self,
        *,
        title: str,
        filename: str,
        sha256: str,
        size_bytes: int,
        chunks: list[str],
        vectors: list[list[float]] | None,
        model: str | None,
    ) -> KbDocument:
        public_id = uuid.uuid4().hex
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO kb_documents (public_id, title, filename, sha256, size_bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (public_id, title, filename, sha256, size_bytes, _now()),
            )
            doc_id = cursor.lastrowid
            for index, text in enumerate(chunks):
                blob = pack_vector(vectors[index]) if vectors else None
                self._conn.execute(
                    "INSERT INTO kb_chunks (doc_id, chunk_index, content, embedding, embedding_model) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (doc_id, index, text, blob, model if vectors else None),
                )
            self._conn.commit()
        found = self.get(public_id)
        assert found is not None
        return found

    def get(self, public_id: str) -> KbDocument | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT d.*, "
                "(SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id = d.id) AS chunk_count, "
                "(SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id = d.id AND c.embedding IS NOT NULL) "
                "AS embedded_chunks "
                "FROM kb_documents d WHERE d.public_id = ?",
                (public_id,),
            ).fetchone()
            return self._document(row) if row else None

    def list_documents(self) -> list[KbDocument]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.*, "
                "(SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id = d.id) AS chunk_count, "
                "(SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id = d.id AND c.embedding IS NOT NULL) "
                "AS embedded_chunks "
                "FROM kb_documents d ORDER BY d.created_at DESC, d.id DESC"
            ).fetchall()
            return [self._document(r) for r in rows]

    def delete_document(self, public_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM kb_documents WHERE public_id = ?", (public_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def has_documents(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM kb_documents LIMIT 1").fetchone() is not None

    @staticmethod
    def _document(row: sqlite3.Row) -> KbDocument:
        return KbDocument(
            public_id=row["public_id"],
            title=row["title"],
            filename=row["filename"],
            size_bytes=row["size_bytes"],
            chunk_count=row["chunk_count"],
            embedded_chunks=row["embedded_chunks"],
            created_at=row["created_at"],
        )

    # ---- поиск ----

    def fts_search(self, text: str, limit: int) -> list[KbHit]:
        match = fts_query(text)
        if match is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id AS chunk_id, c.chunk_index, c.content, d.public_id, d.title "
                "FROM kb_fts f JOIN kb_chunks c ON c.id = f.rowid "
                "JOIN kb_documents d ON d.id = c.doc_id "
                "WHERE kb_fts MATCH ? ORDER BY bm25(kb_fts) LIMIT ?",
                (match, limit),
            ).fetchall()
        return [self._hit(r) for r in rows]

    def vector_candidates(self, model: str) -> list[tuple[KbHit, list[float]]]:
        """Все фрагменты с эмбеддингами указанной модели. Для небольших баз достаточно перебора."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id AS chunk_id, c.chunk_index, c.content, c.embedding, d.public_id, d.title "
                "FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id "
                "WHERE c.embedding IS NOT NULL AND c.embedding_model = ?",
                (model,),
            ).fetchall()
        return [(self._hit(r), unpack_vector(r["embedding"])) for r in rows]

    @staticmethod
    def _hit(row: sqlite3.Row) -> KbHit:
        return KbHit(
            chunk_id=row["chunk_id"],
            document_id=row["public_id"],
            title=row["title"],
            chunk_index=row["chunk_index"],
            content=row["content"],
        )
