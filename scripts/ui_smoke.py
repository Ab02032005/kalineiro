# -*- coding: utf-8 -*-
"""Проверка интерфейса в браузере: вход, все разделы, чат, агент, база знаний.

Перед запуском укажите учётную запись (создаётся командой python -m kali.cli create-user):
  KALI_UI_USER=admin KALI_UI_PASSWORD=... python scripts/ui_smoke.py
Адрес по умолчанию — http://127.0.0.1:8000/ (переменная KALI_UI_BASE).
"""
import json
import os
import tempfile

from playwright.sync_api import sync_playwright

BASE = os.environ.get("KALI_UI_BASE", "http://127.0.0.1:8000/")
USER = os.environ.get("KALI_UI_USER", "")
PASSWORD = os.environ.get("KALI_UI_PASSWORD", "")
report = {"login": None, "routes": {}, "errors": [], "chat": {}, "agent": {}, "knowledge": {}}
tmp = tempfile.mkdtemp()
doc_path = os.path.join(tmp, "правила.txt")
with open(doc_path, "w", encoding="utf-8") as f:
    f.write("Выходные дни в компании — суббота и воскресенье. Офис открыт с 9:00 до 18:00.")


def wait_answer(page, timeout=180000):
    page.wait_for_function(
        "() => { const m = document.querySelectorAll('.msg.assistant'); const last = m[m.length-1];"
        " return last && !last.querySelector('.typing') && last.innerText.trim().length > 0; }",
        timeout=timeout)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: report["errors"].append("PAGEERROR: " + str(e)))
    page.on("console", lambda m: report["errors"].append("CONSOLE: " + m.text) if m.type == "error" else None)

    page.goto(BASE)
    page.wait_for_selector("input[autocomplete=username]", timeout=20000)
    page.fill("input[autocomplete=username]", USER)
    page.fill("input[autocomplete=current-password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector(".nav a", timeout=20000)
    report["login"] = "ok"

    for route in ["chat", "memory", "knowledge", "files", "agents", "models", "dashboard", "settings", "admin"]:
        page.goto(BASE + "#/" + route)
        page.wait_for_timeout(700)
        report["routes"][route] = len(page.inner_text("#view").strip())

    page.goto(BASE + "#/chat")
    page.wait_for_selector("#input")
    page.fill("#input", "Скажи одним словом: привет")
    page.press("#input", "Enter")
    wait_answer(page)
    report["chat"]["reply"] = page.query_selector_all(".msg.assistant")[-1].inner_text()[:200]

    page.select_option("select[aria-label='Агент']", "math")
    page.fill("#input", "Сколько будет 37 умножить на 91? Используй калькулятор.")
    page.press("#input", "Enter")
    wait_answer(page)
    agent_msg = page.query_selector_all(".msg.assistant")[-1]
    report["agent"]["reply"] = agent_msg.inner_text()[:200]
    report["agent"]["has_trace"] = agent_msg.query_selector("details.trace") is not None

    page.goto(BASE + "#/knowledge")
    page.wait_for_selector("input[type=file]")
    page.set_input_files("input[type=file]", doc_path)
    page.wait_for_timeout(2500)
    report["knowledge"]["added"] = "Документ добавлен" in page.inner_text("#view") or "правила" in page.inner_text("#view")
    page.goto(BASE + "#/chat")
    page.wait_for_selector("#input")
    page.select_option("select[aria-label='Агент']", "assistant")
    page.fill("#input", "В какие дни у нас выходные?")
    page.press("#input", "Enter")
    wait_answer(page)
    kb_msg = page.query_selector_all(".msg.assistant")[-1]
    report["knowledge"]["answer"] = kb_msg.inner_text()[:200]
    report["knowledge"]["has_sources"] = kb_msg.query_selector(".sources") is not None

    page.click("text=Выйти")
    page.wait_for_selector("input[autocomplete=username]", timeout=10000)
    report["logout"] = "ok"
    browser.close()

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_report.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("UI CHECK DONE")
