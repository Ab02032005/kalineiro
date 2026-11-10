"""Командная строка Kali: создание пользователей. Первого администратора создают только здесь.

Примеры:
  python -m kali.cli create-user --username admin --role admin
  python -m kali.cli list-users
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from kali.config.settings import get_settings
from kali.security.auth import AuthService


def _service() -> AuthService:
    settings = get_settings()
    directory = os.path.dirname(os.path.abspath(settings.database_path))
    if settings.database_path != ":memory:":
        os.makedirs(directory, exist_ok=True)
    return AuthService(settings.database_path, session_hours=settings.auth_session_hours)


def create_user(username: str, role: str) -> int:
    password = getpass.getpass("Пароль (не короче 10 символов): ")
    if getpass.getpass("Повторите пароль: ") != password:
        print("Пароли не совпадают", file=sys.stderr)
        return 1
    service = _service()
    try:
        user = service.create_user(username, password, role)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    print(f"Создан пользователь {user.username} с ролью {user.role}")
    return 0


def list_users() -> int:
    service = _service()
    try:
        for user in service.list_users():
            print(f"{user.id}\t{user.username}\t{user.role}\t{'активен' if user.active else 'отключён'}")
    finally:
        service.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kali", description="Управление пользователями Kali")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="создать пользователя")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=["admin", "user"], default="user")
    sub.add_parser("list-users", help="показать пользователей")
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return create_user(args.username, args.role)
    return list_users()


if __name__ == "__main__":
    sys.exit(main())
