"""Тесты базы знаний: извлечение текста, разбиение, загрузка, поиск и устойчивость к сбоям."""

import asyncio
import io

import pytest

from kali.knowledge.embeddings import cosine
from kali.knowledge.extract import extract_text, split_text
from kali.knowledge.service import KnowledgeService
from kali.knowledge.store import SqliteKnowledgeStore, pack_vector, unpack_vector
from tests.fakes import FakeEmbedder


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _xlsx_bytes(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Продажи"
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---- извлечение текста ----

def test_plain_text_utf8_and_cp1251():
    assert extract_text("a.txt", "Привет, мир".encode()) == "Привет, мир"
    assert extract_text("a.md", "Привет".encode("cp1251")) == "Привет"


def test_docx_text_is_extracted():
    text = extract_text("отчёт.docx", _docx_bytes(["Первый абзац", "Второй абзац"]))
    assert "Первый абзац" in text and "Второй абзац" in text


def test_xlsx_rows_are_extracted_with_sheet_name():
    text = extract_text("таблица.xlsx", _xlsx_bytes([["Товар", "Цена"], ["Кофе", 350]]))
    assert "## Лист: Продажи" in text
    assert "Кофе | 350" in text


def test_unsupported_format_is_rejected():
    with pytest.raises(ValueError):
        extract_text("картинка.png", b"\x89PNG")


def test_broken_pdf_gives_clean_error():
    with pytest.raises(ValueError) as info:
        extract_text("битый.pdf", b"not a pdf at all")
    assert "повреждён" in str(info.value)


# ---- разбиение ----

def test_short_text_is_one_chunk():
    assert split_text("Короткий текст.", size=800, overlap=100) == ["Короткий текст."]


def test_long_text_is_split_within_size_and_keeps_overlap():
    text = "\n\n".join(f"Абзац {i}. " + "слово " * 60 for i in range(10))
    chunks = split_text(text, size=300, overlap=50)
    assert len(chunks) > 3
    assert all(len(c) <= 300 for c in chunks)
    # Соседние фрагменты перекрываются: конец одного есть в начале следующего
    assert chunks[0][-20:].strip() in chunks[1]


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        split_text("текст", size=100, overlap=100)


def test_very_long_paragraph_is_hard_split():
    chunks = split_text("а" * 1000, size=300, overlap=0)
    assert all(len(c) <= 300 for c in chunks)
    assert sum(len(c) for c in chunks) >= 1000


# ---- векторы ----

def test_vector_roundtrip_and_cosine():
    original = [0.5, -1.0, 2.0]
    restored = unpack_vector(pack_vector(original))
    assert [round(x, 5) for x in restored] == [0.5, -1.0, 2.0]
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0


# ---- сервис ----

def _service(embedder=None) -> KnowledgeService:
    store = SqliteKnowledgeStore(":memory:")
    return KnowledgeService(store, embedder, top_k=3, chunk_chars=300, chunk_overlap=40)


def test_add_and_find_by_words():
    service = _service()
    doc, created = asyncio.run(service.add_document(
        "кофе.txt", "Эспрессо готовят под давлением.\n\nЛатте — это эспрессо с молоком.".encode()))
    assert created is True
    hits = asyncio.run(service.search("эспрессо"))
    assert hits and hits[0].document_id == doc.public_id


def test_same_file_is_not_stored_twice():
    service = _service()
    data = "одинаковый текст".encode()
    first, created_first = asyncio.run(service.add_document("a.txt", data))
    second, created_second = asyncio.run(service.add_document("b.txt", data))
    assert created_first is True and created_second is False
    assert first.public_id == second.public_id
    assert len(service.list_documents()) == 1


def test_semantic_search_finds_related_chunk_without_shared_words_in_query():
    embedder = FakeEmbedder()
    service = _service(embedder)
    asyncio.run(service.add_document("кухня.txt", "Варим кофе в турке и эспрессо на машине.".encode()))
    asyncio.run(service.add_document("погода.txt", "Завтра ожидается дождь и сильный ветер.".encode()))
    hits = asyncio.run(service.search("кофе эспрессо"))
    assert hits[0].title == "кухня"


def test_embedding_failure_falls_back_to_words():
    service = _service(FakeEmbedder(fail=True))
    doc, created = asyncio.run(service.add_document("з.txt", "Текст про кошек и собак.".encode()))
    assert created is True
    assert doc.embedded_chunks == 0
    assert asyncio.run(service.search("кошек"))


def test_documents_with_embeddings_are_marked():
    service = _service(FakeEmbedder())
    doc, _ = asyncio.run(service.add_document("e.txt", "Проверка эмбеддингов.".encode()))
    assert doc.embedded_chunks == doc.chunk_count == 1


def test_empty_and_scanned_documents_are_rejected():
    service = _service()
    with pytest.raises(ValueError):
        asyncio.run(service.add_document("пусто.txt", b""))
    with pytest.raises(ValueError):
        asyncio.run(service.add_document("пробелы.txt", b"   \n  "))


def test_upload_size_limit():
    store = SqliteKnowledgeStore(":memory:")
    service = KnowledgeService(store, None, max_upload_bytes=10)
    with pytest.raises(ValueError):
        asyncio.run(service.add_document("big.txt", b"x" * 11))


def test_delete_removes_chunks_from_search():
    service = _service()
    doc, _ = asyncio.run(service.add_document("del.txt", "Уникальное слово зебра.".encode()))
    assert asyncio.run(service.search("зебра"))
    assert service.delete_document(doc.public_id) is True
    assert asyncio.run(service.search("зебра")) == []


def test_format_context_numbers_fragments_and_marks_as_data():
    service = _service()
    _doc, _ = asyncio.run(service.add_document("ctx.txt", "Фрагмент про лодку.".encode()))
    hits = asyncio.run(service.search("лодку"))
    text = KnowledgeService.format_context(hits)
    assert "[1]" in text
    assert "не инструкции" in text
