"""Извлечение текста из загруженных файлов и разбиение его на фрагменты."""

from __future__ import annotations

import io
import re
from pathlib import Path

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".html", ".xml", ".yaml", ".yml"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx"}


def _decode(data: bytes) -> str:
    """Текстовые файлы: UTF-8, а если не подходит — кодировка Windows для кириллицы."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _xlsx_text(data: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines: list[str] = []
    for sheet in workbook.worksheets:
        lines.append(f"## Лист: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            values = ["" if v is None else str(v) for v in row]
            if any(values):
                lines.append(" | ".join(values))
    workbook.close()
    return "\n".join(lines)


def extract_text(filename: str, data: bytes) -> str:
    """Возвращает текст файла. Неподдерживаемый формат или повреждённый файл — ValueError."""
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Формат {extension or 'без расширения'} не поддерживается")
    try:
        if extension in TEXT_EXTENSIONS:
            return _decode(data)
        if extension == ".pdf":
            return _pdf_text(data)
        if extension == ".docx":
            return _docx_text(data)
        return _xlsx_text(data)
    except ValueError:
        raise
    except Exception as exc:  # повреждённый файл: не раскрываем подробности библиотеки
        raise ValueError("Не удалось прочитать файл: он повреждён или защищён паролем") from exc


_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def split_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    """Делит текст на фрагменты примерно по size символов, сохраняя абзацы и перекрытие.

    Перекрытие нужно, чтобы смысл на границе фрагментов не терялся.
    """
    if size <= overlap:
        raise ValueError("Размер фрагмента должен быть больше перекрытия")
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        while len(paragraph) > size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.append(paragraph[:size])
            paragraph = paragraph[size - overlap:]
        if not buffer:
            buffer = paragraph
        elif len(buffer) + 2 + len(paragraph) <= size:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            chunks.append(buffer)
            tail = buffer[-overlap:] if overlap else ""
            buffer = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
    if buffer:
        chunks.append(buffer)
    return chunks
