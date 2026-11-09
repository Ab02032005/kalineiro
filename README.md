# Kali

Модульная AI-платформа: собственное ядро, память, знания, инструменты и безопасность. Языковые модели подключаются как взаимозаменяемые модули.

> Текущий статус: **этап 2 — основа проекта.** Работают настройки, JSON-логи, `/health` и тесты. Ядро, память и интерфейс — на следующих этапах. План см. в `docs/PLAN.md`.

## Структура

- `backend/` — Python-пакет `kali`, тесты, Dockerfile.
- `infra/` — Docker Compose.
- `docs/` — архитектура и план.
- `data/` — локальные данные (не в Git).

## Быстрый старт (без Docker)

```powershell
cd Kali\backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
pytest
python -m kali
```

Затем откройте http://127.0.0.1:8000/health — должно вернуться `{"status": "ok", ...}`.

## Запуск через Docker

Нужен запущенный Docker Desktop.

```powershell
cd Kali
copy .env.example .env
docker compose -f infra/docker-compose.yml up --build
```

## Конфигурация

Все настройки — переменные окружения с префиксом `KALI_` (см. `.env.example`). Секреты не выводятся в логи. В `prod` отключена документация API.

## Проверки

- `ruff check kali tests` — стиль и ошибки.
- `pytest` — тесты.
- CI в `.github/workflows/ci.yml` запускает оба и собирает образ.
