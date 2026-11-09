"""Профили агентов: инструкция и набор разрешённых инструментов для типа задачи.

Профиль — это не отдельная модель, а режим работы: какие инструменты видит модель
и как она себя ведёт. Многоагентные сценарии (исследователь, аналитик, планировщик)
будут добавлены как новые профили на следующих этапах.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentProfile:
    name: str
    description: str
    instructions: str
    tools: frozenset[str]


PROFILES: dict[str, AgentProfile] = {
    "assistant": AgentProfile(
        name="assistant",
        description="Обычный ассистент без инструментов.",
        instructions="",
        tools=frozenset(),
    ),
    "math": AgentProfile(
        name="math",
        description="Математик: все вычисления выполняет через калькулятор.",
        instructions=(
            "Ты математический помощник. Любые вычисления выполняй через инструмент calculator, "
            "не считай в уме. Объясняй ход решения кратко."
        ),
        tools=frozenset({"calculator"}),
    ),
    "coding": AgentProfile(
        name="coding",
        description="Программист: пишет код и проверяет его запуском в песочнице.",
        instructions=(
            "Ты программист. Перед тем как утверждать, что код работает, запусти его через python_exec "
            "и опирайся на реальный вывод. Если запуск упал, исправь код и проверь снова."
        ),
        tools=frozenset({"python_exec", "list_files", "read_file", "write_file"}),
    ),
    "writer": AgentProfile(
        name="writer",
        description="Автор текстов: работает с файлами рабочей папки.",
        instructions=(
            "Ты редактор и автор текстов. Работай с файлами рабочей папки через инструменты, "
            "когда это нужно. Не выдавай содержимое файла за сохранённое, если запись не выполнена."
        ),
        tools=frozenset({"list_files", "read_file", "write_file"}),
    ),
    "artist": AgentProfile(
        name="artist",
        description="Художник: рисует картинки по описанию.",
        instructions=(
            "Ты помогаешь создавать картинки. Для рисования используй generate_image, "
            "описание переводи на английский для лучшего результата."
        ),
        tools=frozenset({"generate_image"}),
    ),
}


def get_profile(name: str) -> AgentProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"Неизвестный профиль агента: {name}") from None
