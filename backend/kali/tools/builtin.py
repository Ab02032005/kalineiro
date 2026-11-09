"""Встроенные инструменты Kali: калькулятор, файлы в рабочей папке, Python и картинки."""

from __future__ import annotations

import ast
import asyncio
import math
import operator
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from kali.tools.base import ToolError
from kali.tools.sandbox import SandboxLimits, run_python

SAFE_NAME = re.compile(r"^[\w .()\-]{1,120}$")
WRITABLE_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".html", ".css", ".js", ".xml", ".log"}
MAX_WRITE_BYTES = 200_000
MAX_READ_CHARS = 20_000
MAX_POWER = 1000
IMAGE_URL = "https://image.pollinations.ai/prompt/"

# ---------- калькулятор (без eval: разбираем дерево выражения и разрешаем только арифметику) ----------

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "round": round}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER:
            raise ToolError("Слишком большая степень")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS and not node.keywords:
        return _FUNCTIONS[node.func.id](*[_eval_node(a) for a in node.args])
    raise ToolError("В выражении есть недопустимые элементы: разрешена только арифметика")


def calculate(expression: str) -> str:
    if len(expression) > 300:
        raise ToolError("Выражение слишком длинное")
    try:
        tree = ast.parse(expression.replace(",", "."), mode="eval")
    except SyntaxError as exc:
        raise ToolError("Не удалось разобрать выражение") from exc
    value = _eval_node(tree.body)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return str(value)


class Calculator:
    name = "calculator"
    description = "Точный расчёт арифметического выражения: + - * / // % ** скобки, sqrt, abs, round."
    parameters = {
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "Например: (12.5 + 7) * 3"}},
        "required": ["expression"],
    }
    risky = False

    async def run(self, args: dict) -> str:
        return calculate(str(args.get("expression", "")))


# ---------- файлы в рабочей папке ----------

@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path

    def path_for(self, name: str) -> Path:
        """Только имя файла внутри рабочей папки. Пути с каталогами и выход наружу запрещены."""
        if not SAFE_NAME.match(name) or os.path.basename(name) != name:
            raise ToolError("Недопустимое имя файла: без каталогов, только буквы, цифры, пробел и . _ - ( )")
        target = (self.root / name).resolve()
        if self.root.resolve() not in target.parents:
            raise ToolError("Файл должен находиться в рабочей папке")
        return target


class ListFiles:
    name = "list_files"
    description = "Список файлов в рабочей папке Kali (имя и размер)."
    parameters = {"type": "object", "properties": {}}
    risky = False

    def __init__(self, workspace: Workspace) -> None:
        self._ws = workspace

    async def run(self, args: dict) -> str:
        self._ws.root.mkdir(parents=True, exist_ok=True)
        entries = sorted(p for p in self._ws.root.iterdir() if p.is_file())
        if not entries:
            return "Рабочая папка пуста"
        return "\n".join(f"{p.name} ({p.stat().st_size} байт)" for p in entries)


class ReadFile:
    name = "read_file"
    description = "Читает текстовый файл из рабочей папки."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Имя файла, например notes.md"}},
        "required": ["name"],
    }
    risky = False

    def __init__(self, workspace: Workspace) -> None:
        self._ws = workspace

    async def run(self, args: dict) -> str:
        path = self._ws.path_for(str(args.get("name", "")))
        if not path.is_file():
            raise ToolError("Файл не найден")
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:MAX_READ_CHARS] + ("\n… (файл обрезан)" if len(text) > MAX_READ_CHARS else "")


class WriteFile:
    name = "write_file"
    description = "Создаёт или перезаписывает текстовый файл в рабочей папке. Требует разрешения действий."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя файла с расширением, например report.md"},
            "content": {"type": "string"},
        },
        "required": ["name", "content"],
    }
    risky = True

    def __init__(self, workspace: Workspace) -> None:
        self._ws = workspace

    async def run(self, args: dict) -> str:
        path = self._ws.path_for(str(args.get("name", "")))
        if path.suffix.lower() not in WRITABLE_EXTENSIONS:
            raise ToolError("Такое расширение нельзя записать: " + ", ".join(sorted(WRITABLE_EXTENSIONS)))
        content = str(args.get("content", ""))
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolError("Файл слишком большой")
        self._ws.root.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Файл {path.name} сохранён ({len(content)} символов)"


# ---------- Python в песочнице ----------

class PythonExec:
    name = "python_exec"
    description = ("Выполняет Python-код в изолированной песочнице: без сети и доступа к файлам компьютера, "
                   "ограничены время и память. Возвращает вывод программы. Требует разрешения действий.")
    parameters = {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Программа на Python, которая печатает результат"}},
        "required": ["code"],
    }
    risky = True

    def __init__(self, limits: SandboxLimits) -> None:
        self._limits = limits

    async def run(self, args: dict) -> str:
        return await run_python(str(args.get("code", "")), self._limits)


# ---------- картинки ----------

def _fetch_image(url: str) -> tuple[bytes, str]:
    """Синхронный запрос картинки. Вызывается в отдельном потоке, чтобы не блокировать сервер."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read(), response.headers.get_content_type()


class GenerateImage:
    name = "generate_image"
    description = ("Рисует картинку по описанию и сохраняет её в рабочую папку. "
                   "Использует внешний сервис. Требует разрешения действий.")
    parameters = {
        "type": "object",
        "properties": {"prompt": {"type": "string", "description": "Подробное описание картинки"}},
        "required": ["prompt"],
    }
    risky = True

    def __init__(self, workspace: Workspace) -> None:
        self._ws = workspace

    async def run(self, args: dict) -> str:
        prompt = str(args.get("prompt", "")).strip()
        if not prompt or len(prompt) > 500:
            raise ToolError("Описание картинки должно быть от 1 до 500 символов")
        url = IMAGE_URL + urllib.parse.quote(prompt) + f"?width=1024&height=1024&nologo=true&seed={int(time.time())}"
        try:
            body, ctype = await asyncio.to_thread(_fetch_image, url)
        except OSError as exc:
            raise ToolError("Сервис рисования недоступен, попробуйте позже") from exc
        if not ctype.startswith("image/") or len(body) > 15_000_000:
            raise ToolError("Сервис рисования вернул не картинку")
        extension = ".png" if "png" in ctype else ".jpg"
        name = f"image_{int(time.time())}{extension}"
        self._ws.root.mkdir(parents=True, exist_ok=True)
        (self._ws.root / name).write_bytes(body)
        return f"Картинка сохранена как {name} ({len(body)} байт)"
