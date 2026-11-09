"""Песочница для выполнения Python-кода: отдельный контейнер Docker без сети и с лимитами.

Код не выполняется на машине напрямую. Контейнер:
- без сети (--network none), без прав (--cap-drop ALL), файловая система только на чтение;
- ограничены память, процессор, число процессов и время;
- получает код только как файл в смонтированном каталоге на чтение.
Сервис работает только там, где доступен Docker CLI (запуск на хосте, не в контейнере Kali).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass

from kali.tools.base import ToolError

MAX_CODE_CHARS = 20_000
MAX_OUTPUT_CHARS = 4_000


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    image: str = "python:3.13-slim"
    timeout_s: int = 10
    memory: str = "256m"
    cpus: str = "0.5"
    pids: int = 64


def build_docker_command(workdir: str, limits: SandboxLimits, container_name: str) -> list[str]:
    """Аргументы docker run. Вынесено отдельно, чтобы проверять набор ограничений тестами."""
    return [
        "docker", "run", "--rm",
        "--name", container_name,
        "--network", "none",
        "--memory", limits.memory,
        "--cpus", limits.cpus,
        "--pids-limit", str(limits.pids),
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "-v", f"{workdir}:/work:ro",
        "-w", "/work",
        limits.image,
        "python", "-I", "-B", "main.py",
    ]


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _write_code(workdir: str, code: str) -> None:
    """Записывает программу в файл. Выполняется в отдельном потоке."""
    with open(os.path.join(workdir, "main.py"), "w", encoding="utf-8") as handle:
        handle.write(code)


async def run_python(code: str, limits: SandboxLimits | None = None) -> str:
    """Выполняет код в песочнице и возвращает вывод. Ошибки выполнения — текстом для модели."""
    limits = limits or SandboxLimits()
    if not code.strip():
        raise ToolError("Код пустой")
    if len(code) > MAX_CODE_CHARS:
        raise ToolError(f"Код длиннее {MAX_CODE_CHARS} символов")
    if not docker_available():
        raise ToolError("Песочница недоступна: на этой машине не найден Docker")

    workdir = tempfile.mkdtemp(prefix="kali-sbx-")
    container_name = f"kali-sbx-{os.getpid()}-{os.urandom(4).hex()}"
    try:
        await asyncio.to_thread(_write_code, workdir, code)
        os.chmod(workdir, 0o755)
        command = build_docker_command(workdir, limits, container_name)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=limits.timeout_s + 5)
        except TimeoutError:
            process.kill()
            await asyncio.create_subprocess_exec("docker", "kill", container_name,
                                                 stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
            raise ToolError(f"Выполнение остановлено: превышено время {limits.timeout_s} с") from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    out = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    err = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    if process.returncode == 0:
        return out or "(программа ничего не вывела)"
    return f"Код завершился с ошибкой (код {process.returncode}).\n{err or out}".strip()
