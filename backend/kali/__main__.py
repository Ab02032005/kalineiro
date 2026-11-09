"""Точка входа: python -m kali"""

from __future__ import annotations

import uvicorn

from kali.api.app import create_app
from kali.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
