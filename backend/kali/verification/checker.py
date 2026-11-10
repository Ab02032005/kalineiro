"""Verification Engine: проверка ответа по источникам и по результатам инструментов.

Правила:
- ссылка [n] допустима только если источник n был передан модели;
- утверждение с ссылкой считается подтверждённым, если его слова есть во фрагменте;
- утверждения без ссылок при наличии источников отмечаются;
- агент не может сообщить о действии (сохранил файл, запустил код), если инструмент его не выполнил.
Проверка детерминированная (без второй модели), поэтому её результат можно воспроизвести.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["verified", "partial", "unsupported", "unknown", "assumption"]

LABELS: dict[str, str] = {
    "verified": "проверено источниками",
    "partial": "частично проверено",
    "unsupported": "не подтверждено источниками",
    "unknown": "неизвестно",
    "assumption": "предположение (без источников)",
}

CITATION = re.compile(r"\[(\d{1,2})\]")
SUPPORT_THRESHOLD = 0.4
MIN_CLAIM_WORDS = 5
PREFIX = 5  # грубая нормализация окончаний: «выходные» и «выходных» совпадают по основе

STOP_PREFIXES = {
    "этот", "этом", "этих", "быть", "есть", "будет", "когда", "если", "также", "только",
    "очень", "можно", "нужно", "который", "которые", "какой", "какие", "вопрос", "ответ",
    "the", "and", "that", "this", "with", "from", "have", "были", "было",
}

UNKNOWN_PHRASES = (
    "не знаю", "нет информации", "не найдено", "не нашёл", "не нашел", "не нашла",
    "ничего не найдено", "в базе нет", "в фрагментах нет", "нет в базе", "не могу ответить",
    "не содержит ответа", "i don't know", "no information", "not found",
)

# Слова, за которыми стоит заявление о действии инструмента: (фразы, инструмент, описание)
ACTION_CLAIMS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("сохранил", "записал", "создал файл", "файл сохран", "файл создан", "файл записан"),
     "write_file", "файл записан"),
    (("запустил код", "выполнил код", "результат выполнения", "код выполнен", "программа вывела"),
     "python_exec", "код выполнен"),
    (("нарисовал", "картинка готова", "создал картинку", "сгенерировал картинку"),
     "generate_image", "картинка создана"),
)


@dataclass(slots=True)
class Verification:
    status: Status
    issues: list[str] = field(default_factory=list)
    citations_total: int = 0
    citations_valid: int = 0
    citations_supported: int = 0

    @property
    def label(self) -> str:
        return LABELS[self.status]

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "label": self.label,
            "issues": self.issues,
            "citations": {
                "total": self.citations_total,
                "valid": self.citations_valid,
                "supported": self.citations_supported,
            },
        }


def _words(text: str) -> set[str]:
    result = set()
    for word in re.findall(r"[^\W\d_]+", text.lower()):
        if len(word) < 4:
            continue
        stem = word[:PREFIX]
        if stem not in STOP_PREFIXES:
            result.add(stem)
    return result


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _is_claim(sentence: str) -> bool:
    return not sentence.endswith("?") and len(_words(sentence)) >= MIN_CLAIM_WORDS


def _is_unknown(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in UNKNOWN_PHRASES)


def check_answer(reply: str, sources: dict[int, str]) -> Verification:
    """Проверка ответа по источникам. sources: номер -> полный текст фрагмента."""
    text = reply.strip()
    if not text:
        return Verification(status="unknown", issues=["Пустой ответ"])

    issues: list[str] = []
    citations = CITATION.findall(text)
    valid_numbers = set(sources)
    valid = supported = 0
    cited_ok: list[bool] = []
    uncited_claims: list[str] = []
    bad_reference = False

    for sentence in _sentences(text):
        numbers = [int(n) for n in CITATION.findall(sentence)]
        if numbers:
            for n in numbers:
                if n not in valid_numbers:
                    bad_reference = True
                    issues.append(f"Ссылка [{n}] не соответствует ни одному источнику")
                else:
                    valid += 1
            good = [n for n in numbers if n in valid_numbers]
            if not good:
                continue
            claim_words = _words(CITATION.sub("", sentence))
            if not claim_words:
                continue
            best = max(len(claim_words & _words(sources[n])) / len(claim_words) for n in good)
            if best >= SUPPORT_THRESHOLD:
                supported += 1
                cited_ok.append(True)
            else:
                cited_ok.append(False)
                issues.append("Утверждение не подтверждено фрагментом: " + sentence[:160])
        elif sources and _is_claim(sentence):
            uncited_claims.append(sentence)

    if uncited_claims and sources:
        issues.append(f"Утверждений без ссылок на источники: {len(uncited_claims)}")

    result = Verification(
        status="assumption",
        issues=issues,
        citations_total=len(citations),
        citations_valid=valid,
        citations_supported=supported,
    )
    unknown = _is_unknown(text)
    if not sources:
        if unknown:
            result.status = "unknown"
        elif citations:
            result.status = "unsupported"
            result.issues.append("Ответ ссылается на источники, которых нет")
        else:
            result.status = "assumption"
        return result

    if unknown and not citations:
        result.status = "unknown"
    elif not cited_ok:
        result.status = "unsupported"
        if not citations:
            result.issues.append("Ответ не опирается на источники из базы знаний")
    elif all(cited_ok) and not uncited_claims and not bad_reference:
        result.status = "verified"
    elif any(cited_ok):
        result.status = "partial"
    else:
        result.status = "unsupported"
    return result


def check_actions(reply: str, trace: list[dict]) -> list[str]:
    """Проверяет, что агент не заявляет о действиях, которых инструменты не выполняли."""
    low = reply.lower()
    issues = []
    for phrases, tool, description in ACTION_CLAIMS:
        if any(phrase in low for phrase in phrases):
            done = any(t.get("tool") == tool and t.get("ok") for t in trace)
            if not done:
                issues.append(f"Ответ сообщает, что {description}, но инструмент {tool} не выполнил действие")
    return issues
