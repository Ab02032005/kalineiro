"""Тесты памяти: хранилище, правила записи, безопасность и контекст для модели."""

import pytest

from kali.memory.models import MemoryRejected
from kali.memory.safety import find_sensitive
from kali.memory.service import MemoryService
from kali.memory.store import SqliteMemoryStore, fts_query
from kali.models.base import Message


@pytest.fixture
def service():
    store = SqliteMemoryStore(":memory:")
    yield MemoryService(store, context_limit=3)
    store.close()


# ---- безопасность ----

@pytest.mark.parametrize(
    "text, label",
    [
        ("мой ключ sk-zsXoXfcY2XLeSDULcP6DU6QaSVe7fLTqvizLuXlnLksS7HAq", "api_key"),
        ("gsk_abcdefghijklmnopqrstuv", "api_key"),
        ("пароль: hunter22", "password"),
        ("карта 4111 1111 1111 1111", "card_number"),
    ],
)
def test_sensitive_data_is_detected(text, label):
    assert find_sensitive(text) == label


@pytest.mark.parametrize(
    "text",
    ["я люблю Python", "номер 12345 не карта", "версия 3.14.8", "обычный текст без секретов"],
)
def test_ordinary_text_is_not_flagged(text):
    assert find_sensitive(text) is None


def test_secret_is_never_stored(service):
    with pytest.raises(MemoryRejected) as info:
        service.add("fact", "ключ sk-zsXoXfcY2XLeSDULcP6DU6QaSVe7fLTqvizLuXlnLksS7HAq")
    assert "sk-" not in str(info.value)
    assert service.list() == []


# ---- запись ----

def test_add_fact_and_list_it(service):
    item, created = service.add("fact", "Меня зовут Ахмед")
    assert created is True
    assert item.kind == "fact"
    assert [i.content for i in service.list()] == ["Меня зовут Ахмед"]


def test_duplicate_is_not_stored_twice(service):
    first, _ = service.add("fact", "Живу во Франкфурте")
    second, created = service.add("fact", "  живу   во франкфурте ")
    assert created is False
    assert second.public_id == first.public_id
    assert len(service.list()) == 1


def test_episodes_cannot_be_added_manually(service):
    with pytest.raises(MemoryRejected):
        service.add("episode", "вручную")


def test_empty_and_too_long_content_rejected(service):
    with pytest.raises(MemoryRejected):
        service.add("fact", "   ")
    with pytest.raises(MemoryRejected):
        service.add("fact", "x" * 2001)


def test_update_and_delete(service):
    item, _ = service.add("procedure", "Сначала тесты, потом коммит")
    updated = service.update(item.public_id, "Сначала тесты, потом коммит и пуш")
    assert updated is not None
    assert updated.content == "Сначала тесты, потом коммит и пуш"
    assert service.delete(item.public_id) is True
    assert service.get(item.public_id) is None
    assert service.delete(item.public_id) is False


def test_wipe_by_kind_and_all(service):
    service.add("fact", "факт один")
    service.add("procedure", "процедура один")
    service.record_episode("вопрос", "ответ")
    assert service.wipe("episode") == 1
    assert service.wipe() == 2
    assert service.list() == []


# ---- поиск ----

def test_search_finds_by_word_and_prefix(service):
    service.add("fact", "Работаю над проектом Kali на Python")
    service.add("fact", "Люблю кофе по утрам")
    found = service.search("kali")
    assert [i.content for i in found] == ["Работаю над проектом Kali на Python"]
    assert service.search("питон") == []
    assert [i.content for i in service.search("кофе")] == ["Люблю кофе по утрам"]


def test_search_is_safe_against_fts_syntax(service):
    service.add("fact", "проект kali")
    assert service.search('kali" OR * NEAR(') is not None
    assert service.search("!!!") == []


def test_fts_query_quotes_tokens():
    assert fts_query("Hello, мир!") == '"hello"* OR "мир"*'
    assert fts_query("   ") is None


# ---- контекст и короткая память ----

def test_context_returns_facts_and_procedures_only(service):
    service.add("fact", "Пользователь любит Python")
    service.add("procedure", "Python: сначала тест, потом код")
    service.record_episode("Что такое Python?", "Язык программирования")
    kinds = {i.kind for i in service.context_for("Python")}
    assert kinds == {"fact", "procedure"}


def test_context_marks_last_used(service):
    item, _ = service.add("fact", "любит Python")
    service.context_for("Python")
    assert service.get(item.public_id).last_used_at is not None


def test_disabled_memory_gives_no_context():
    store = SqliteMemoryStore(":memory:")
    service = MemoryService(store, enabled=False)
    service.add("fact", "любит Python")
    assert service.context_for("Python") == []
    store.close()


def test_format_context_states_it_is_data_not_instructions(service):
    item, _ = service.add("fact", "Отвечает кратко")
    text = service.format_context([item])
    assert "не инструкции" in text
    assert "- [факт] Отвечает кратко" in text
    assert service.format_context([]) == ""


def test_trim_short_term_keeps_system_and_last_user():
    messages = [
        Message(role="system", content="S" * 10),
        Message(role="user", content="a" * 50),
        Message(role="assistant", content="b" * 50),
        Message(role="user", content="c" * 20),
    ]
    trimmed = MemoryService.trim_short_term(messages, max_chars=40)
    assert trimmed[0].role == "system"
    assert trimmed[-1].content == "c" * 20
    assert all(m.content != "a" * 50 for m in trimmed)


def test_trim_keeps_last_message_even_if_too_long():
    messages = [Message(role="user", content="x" * 500)]
    assert MemoryService.trim_short_term(messages, max_chars=10) == messages


# ---- выгрузка ----

def test_export_contains_all_items_and_version(service):
    service.add("fact", "факт")
    service.record_episode("q", "a")
    exported = service.export()
    assert exported["format_version"] == 1
    assert len(exported["items"]) == 2
    assert {i["kind"] for i in exported["items"]} == {"fact", "episode"}
