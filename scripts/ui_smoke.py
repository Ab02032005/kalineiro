# -*- coding: utf-8 -*-
"""Проверка интерфейса в настоящем браузере: все разделы, чат, агент, загрузка документа."""
import json
import os
import tempfile

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8001/"
report = {"routes": {}, "errors": [], "chat": {}, "agent": {}, "knowledge": {}}
tmp = tempfile.mkdtemp()
doc_path = os.path.join(tmp, "правила.txt")
with open(doc_path, "w", encoding="utf-8") as f:
    f.write("Выходные дни в компании — суббота и воскресенье. Офис открыт с 9:00 до 18:00.")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: report["errors"].append("PAGEERROR: " + str(e)))
    page.on("console", lambda m: report["errors"].append("CONSOLE: " + m.text) if m.type == "error" else None)

    page.goto(BASE)
    page.wait_for_selector(".nav a")
    for route in ["chat", "memory", "knowledge", "files", "agents", "models", "dashboard", "settings", "admin"]:
        page.goto(BASE + "#/" + route)
        page.wait_for_timeout(700)
        text = page.inner_text("#view")
        report["routes"][route] = len(text.strip())
        if route == "dashboard":
            page.screenshot(path=os.path.join(tmp, "dashboard.png"))

    # Чат: обычный ответ (потоковый)
    page.goto(BASE + "#/chat")
    page.wait_for_selector("#input")
    page.fill("#input", "Скажи одним словом: привет")
    page.press("#input", "Enter")
    page.wait_for_function(
        "() => { const m = document.querySelectorAll('.msg.assistant'); const last = m[m.length-1];"
        " return last && !last.querySelector('.typing') && last.innerText.trim().length > 0; }", timeout=120000)
    last = page.query_selector_all(".msg.assistant")[-1].inner_text()
    report["chat"]["reply"] = last[:200]
    page.screenshot(path=os.path.join(tmp, "chat.png"))

    # Агент с калькулятором: выбираем профиль math
    page.select_option("select[aria-label='Агент']", "math")
    page.wait_for_timeout(300)
    page.fill("#input", "Сколько будет 37 умножить на 91? Используй калькулятор.")
    page.press("#input", "Enter")
    page.wait_for_function(
        "() => { const m = document.querySelectorAll('.msg.assistant'); const last = m[m.length-1];"
        " return last && !last.querySelector('.typing') && last.innerText.trim().length > 0; }", timeout=180000)
    agent_msg = page.query_selector_all(".msg.assistant")[-1]
    report["agent"]["reply"] = agent_msg.inner_text()[:200]
    report["agent"]["has_trace"] = agent_msg.query_selector("details.trace") is not None
    if report["agent"]["has_trace"]:
        agent_msg.query_selector("details.trace summary").click()
        report["agent"]["trace"] = agent_msg.query_selector("details.trace").inner_text()[:300]
    page.screenshot(path=os.path.join(tmp, "agent.png"))

    # База знаний: загрузка документа и вопрос по нему
    page.goto(BASE + "#/knowledge")
    page.wait_for_selector("input[type=file]")
    page.set_input_files("input[type=file]", doc_path)
    page.wait_for_timeout(2500)
    report["knowledge"]["status"] = page.inner_text("#view")[:300]
    page.goto(BASE + "#/chat")
    page.wait_for_selector("#input")
    page.select_option("select[aria-label='Агент']", "assistant")
    page.fill("#input", "В какие дни у нас выходные?")
    page.press("#input", "Enter")
    page.wait_for_function(
        "() => { const m = document.querySelectorAll('.msg.assistant'); const last = m[m.length-1];"
        " return last && !last.querySelector('.typing') && last.innerText.trim().length > 0; }", timeout=120000)
    kb_msg = page.query_selector_all(".msg.assistant")[-1]
    report["knowledge"]["answer"] = kb_msg.inner_text()[:300]
    report["knowledge"]["has_sources"] = kb_msg.query_selector(".sources") is not None
    page.screenshot(path=os.path.join(tmp, "knowledge_answer.png"))

    browser.close()

with open(r"C:\Users\Akhmed\Desktop\claude\Kali\backend\ui_report.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
with open(r"C:\Users\Akhmed\Desktop\claude\Kali\backend\ui_shots_dir.txt", "w", encoding="utf-8") as f:
    f.write(tmp)
print("UI CHECK DONE")
