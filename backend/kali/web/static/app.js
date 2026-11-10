/* Kali — интерфейс. Разделы: чат, память, база знаний, файлы, агенты, модели, панель, настройки, администрирование. */
(() => {
"use strict";

// ---------- служебное ----------
const $ = (sel, root = document) => root.querySelector(sel);
const UI_KEY = "kali.ui";
const CHATS_KEY = "kali.chats";
const MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-5-mini", "deepseek-chat"];

function loadJson(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key)); return v === null || v === undefined ? fallback : v; } catch (e) { return fallback; }
}
function saveJson(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { toast("Не удалось сохранить: память браузера заполнена"); }
}

let ui = Object.assign({
  theme: "auto", useMemory: true, useKnowledge: true, remember: false,
  profile: "assistant", model: "gpt-4o-mini", allowActions: false,
}, loadJson(UI_KEY, {}));
let chats = loadJson(CHATS_KEY, []);
const SESSION_KEY = "kali.session";
let session = loadJson(SESSION_KEY, null);
let appVersion = "";
let activeChatId = chats[0] ? chats[0].id : null;

function persistUi() { saveJson(UI_KEY, ui); }
function persistChats() { saveJson(CHATS_KEY, chats); }

function applyTheme() {
  if (ui.theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", ui.theme);
}
applyTheme();

let toastTimer = null;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
    else if (key === "attrs") for (const [k, v] of Object.entries(value)) node.setAttribute(k, v);
    else node[key] = value;
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function errorText(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || "Ошибка проверки").join("; ");
  return "Ошибка сервера (" + status + ")";
}

async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const token = sessionToken();
  if (token) options.headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, options);
  if (res.status === 401 && path !== "/v1/auth/login") { handleUnauthorized(); throw new Error("Нужно войти в систему"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data, res.status));
  return data;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + " Б";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " КБ";
  return (bytes / 1024 / 1024).toFixed(1) + " МБ";
}

// ---------- безопасный markdown (всё экранируется до разметки) ----------
function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function inlineMd(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function textBlocks(text) {
  const out = [];
  let para = [];
  let list = null;
  const flush = () => { if (para.length) { out.push("<p>" + para.map(inlineMd).join("<br>") + "</p>"); para = []; } };
  const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };
  for (const raw of text.split("\n")) {
    const line = escHtml(raw);
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flush(); closeList(); const n = m[1].length; out.push(`<h${n}>${inlineMd(m[2])}</h${n}>`); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { flush(); if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; } out.push("<li>" + inlineMd(m[1]) + "</li>"); }
    else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) { flush(); if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; } out.push("<li>" + inlineMd(m[1]) + "</li>"); }
    else if (line.trim() === "") { flush(); closeList(); }
    else { closeList(); para.push(line); }
  }
  flush(); closeList();
  return out.join("");
}
function markdown(text) {
  let html = "";
  const re = /```([\w+-]*)\n?([\s\S]*?)```/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    html += textBlocks(text.slice(last, m.index));
    html += '<div class="codeblock"><div class="codehead"><span>' + escHtml(m[1] || "код") +
      '</span><button data-copy="1">Копировать</button></div><pre><code>' +
      escHtml(m[2].replace(/\n$/, "")) + "</code></pre></div>";
    last = re.lastIndex;
  }
  const rest = text.slice(last);
  const open = rest.match(/```([\w+-]*)\n?([\s\S]*)$/);
  if (open) {
    html += textBlocks(rest.slice(0, open.index));
    html += '<div class="codeblock"><div class="codehead"><span>' + escHtml(open[1] || "код") +
      '</span></div><pre><code>' + escHtml(open[2]) + "</code></pre></div>";
  } else {
    html += textBlocks(rest);
  }
  return html;
}
document.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-copy]");
  if (!btn) return;
  const code = btn.closest(".codeblock").querySelector("code").textContent;
  const done = () => { btn.textContent = "Скопировано"; setTimeout(() => { btn.textContent = "Копировать"; }, 1500); };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(code).then(done).catch(() => toast("Не удалось скопировать"));
  else toast("Копирование доступно только по защищённому соединению");
});

// ---------- статус сервера ----------
async function refreshStatus() {
  const dot = $("#status");
  try {
    const health = await api("/health");
    dot.className = "status up";
    dot.title = "Сервер работает, версия " + health.version;
    appVersion = health.version;
    renderFoot();
  } catch (e) {
    dot.className = "status down";
    dot.title = "Сервер недоступен";
  }
}

// ---------- чат ----------
function activeChat() {
  return chats.find((c) => c.id === activeChatId) || null;
}
function newChat() {
  const chat = { id: Math.random().toString(36).slice(2, 10), title: "Новый чат", messages: [], created: new Date().toISOString() };
  chats.unshift(chat);
  activeChatId = chat.id;
  persistChats();
  return chat;
}

let agentCatalog = null;
async function loadAgents() {
  if (!agentCatalog) agentCatalog = (await api("/v1/agents")).agents;
  return agentCatalog;
}

function renderSources(sources) {
  if (!sources || !sources.length) return null;
  return el("div", { class: "sources" },
    el("strong", { text: "Источники:" }),
    sources.map((s) => el("div", { text: `[${s.number}] ${s.title}, фрагмент ${s.chunk_index + 1}: ${s.excerpt}` })));
}
function renderTrace(trace, incomplete) {
  if (!trace || !trace.length) return incomplete ? el("div", { class: "trace error", text: "Задача не завершена за отведённое число шагов" }) : null;
  return el("details", { class: "trace" },
    el("summary", { text: `Действия инструментов: ${trace.length}${incomplete ? " (не завершено)" : ""}` }),
    trace.map((t) => el("div", { class: t.ok ? "" : "error" }, `${t.ok ? "✓" : "✗"} ${t.tool}: ${t.result}`)));
}

function verificationNode(v) {
  if (!v) return null;
  const cls = v.status === "verified" ? "ok" : (v.status === "partial" ? "" : "error");
  const parts = [el("span", { class: "chip " + cls, text: "Проверка: " + v.label })];
  if (v.issues && v.issues.length) {
    parts.push(el("details", { class: "small" },
      el("summary", { text: "Замечания проверки: " + v.issues.length }),
      v.issues.map((i) => el("div", { text: i }))));
  }
  return el("div", { class: "verify", style: "margin-top:8px;font-size:13px" }, parts);
}
function messageNode(msg) {
  if (msg.role === "user") return el("div", { class: "msg user", text: msg.content });
  const body = el("div", { class: "body" });
  body.innerHTML = markdown(msg.content || "");
  if (msg.pending) body.append(el("span", { class: "typing", text: "Думаю…" }));
  return el("div", { class: "msg assistant" }, body, renderSources(msg.sources), renderTrace(msg.trace, msg.incomplete),
    verificationNode(msg.verification));
}

function renderMessages(container, chat) {
  container.innerHTML = "";
  if (!chat || !chat.messages.length) {
    container.append(el("div", { class: "empty-state" },
      el("h2", { text: "Чем могу помочь?" }),
      el("div", { text: "Напишите сообщение ниже. Выберите агента, чтобы модель могла пользоваться инструментами." })));
    return;
  }
  for (const msg of chat.messages) container.append(messageNode(msg));
  container.scrollTop = container.scrollHeight;
}

async function streamChat(chat, assistantMsg, body) {
  const res = await fetch("/v1/chat/stream", {
    method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
    body: JSON.stringify(body),
  });
  if (res.status === 401) { handleUnauthorized(); throw new Error("Нужно войти в систему"); }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(errorText(data, res.status));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice(5));
      if (event.sources) assistantMsg.sources = event.sources;
      if (event.verification) assistantMsg.verification = event.verification;
      if (event.delta) assistantMsg.content += event.delta;
      if (event.error) throw new Error(event.error);
      if (event.delta || event.sources) updateAssistant(chat, assistantMsg);
    }
  }
}

function updateAssistant(chat, msg) {
  const container = $("#messages");
  if (!container || !chat || chat.id !== activeChatId) return;
  const index = chat.messages.indexOf(msg);
  const old = container.children[index];
  const node = messageNode(msg);
  if (old) container.replaceChild(node, old); else container.append(node);
  container.scrollTop = container.scrollHeight;
}

async function sendMessage(text) {
  const chat = activeChat() || newChat();
  chat.messages.push({ role: "user", content: text });
  if (chat.messages.length === 1) chat.title = text.slice(0, 40);
  const assistantMsg = { role: "assistant", content: "", pending: true };
  chat.messages.push(assistantMsg);
  persistChats();
  renderChatView();

  const history = chat.messages.filter((m) => !m.pending).slice(-30)
    .map((m) => ({ role: m.role, content: m.content }));
  const isAgent = ui.profile !== "assistant";
  try {
    if (isAgent) {
      const data = await api("/v1/agent/run", {
        method: "POST",
        body: {
          messages: history, profile: ui.profile, model: ui.model,
          use_memory: ui.useMemory, use_knowledge: ui.useKnowledge, allow_actions: ui.allowActions,
        },
      });
      assistantMsg.content = data.reply;
      assistantMsg.sources = data.sources;
      assistantMsg.trace = data.trace;
      assistantMsg.incomplete = data.incomplete;
      assistantMsg.verification = data.verification;
    } else {
      await streamChat(chat, assistantMsg, {
        messages: history, model: ui.model, use_memory: ui.useMemory,
        use_knowledge: ui.useKnowledge, remember: ui.remember,
      });
    }
    delete assistantMsg.pending;
    if (!assistantMsg.content) assistantMsg.content = "(пустой ответ)";
  } catch (err) {
    delete assistantMsg.pending;
    assistantMsg.content = "Ошибка: " + err.message;
    assistantMsg.failed = true;
  }
  persistChats();
  renderChatView();
}

function renderChatView() {
  const view = $("#view");
  const current = view.querySelector(".chat");
  if (!current) { renderChatLayout(view); }
  refreshConversationList();
  renderMessages($("#messages"), activeChat());
  $("#pageTitle").textContent = "Чат";
}

function refreshConversationList() {
  const list = $("#convList");
  if (!list) return;
  list.innerHTML = "";
  for (const chat of chats) {
    const row = el("div", { class: "conv" + (chat.id === activeChatId ? " active" : "") },
      el("span", { text: chat.title || "Новый чат" }),
      el("button", { text: "×", title: "Удалить диалог", on: { click: (e) => { e.stopPropagation(); deleteChat(chat.id); } } }));
    row.addEventListener("click", () => { activeChatId = chat.id; persistChats(); renderChatView(); });
    list.append(row);
  }
}

function deleteChat(id) {
  if (!confirm("Удалить этот диалог на этом устройстве?")) return;
  chats = chats.filter((c) => c.id !== id);
  if (activeChatId === id) activeChatId = chats[0] ? chats[0].id : null;
  persistChats();
  renderChatView();
}

function renderChatLayout(view) {
  const profileSelect = el("select", {
    attrs: { "aria-label": "Агент" },
    on: { change: (e) => { ui.profile = e.target.value; persistUi(); renderChatView(); } },
  });
  const modelInput = el("input", {
    type: "text", value: ui.model, attrs: { list: "modelList", "aria-label": "Модель" },
    on: { change: (e) => { ui.model = e.target.value.trim() || "gpt-4o-mini"; persistUi(); } },
  });
  const toggles = el("div", { class: "toolbar" });
  const addToggle = (label, key) => {
    const box = el("input", { type: "checkbox", checked: !!ui[key], on: { change: (e) => { ui[key] = e.target.checked; persistUi(); } } });
    toggles.append(el("label", {}, box, label));
    return box;
  };
  addToggle("память", "useMemory");
  addToggle("база знаний", "useKnowledge");
  addToggle("запоминать диалог", "remember");
  const actionsBox = el("input", { type: "checkbox", checked: !!ui.allowActions, on: { change: (e) => { ui.allowActions = e.target.checked; persistUi(); } } });
  const actionsLabel = el("label", { hidden: true }, actionsBox, "разрешить действия (файлы, код, картинки)");
  toggles.append(actionsLabel);

  view.innerHTML = "";
  view.append(el("div", { class: "chat" },
    el("div", { class: "chat-side" },
      el("button", { class: "btn btn-primary", text: "+ Новый чат", style: "width:100%;margin-bottom:8px",
        on: { click: () => { newChat(); persistChats(); renderChatView(); } } }),
      el("div", { class: "conv-list", id: "convList" })),
    el("div", { class: "chat-main" },
      el("div", { class: "toolbar" },
        el("span", { text: "Агент:" }), profileSelect,
        el("span", { text: "Модель:" }), modelInput),
      toggles,
      el("div", { class: "messages", id: "messages" }),
      el("form", { class: "composer", id: "composer" },
        el("textarea", { id: "input", rows: 2, placeholder: "Напишите сообщение (Enter — отправить, Shift+Enter — перенос)" }),
        el("button", { class: "btn btn-primary", type: "submit", text: "Отправить", id: "sendBtn" })))));

  const datalist = $("#modelList") || el("datalist", { id: "modelList" }, MODELS.map((m) => el("option", { value: m })));
  if (!$("#modelList")) document.body.append(datalist);

  loadAgents().then((agents) => {
    profileSelect.innerHTML = "";
    profileSelect.append(el("option", { value: "assistant", text: "Обычный чат" }));
    for (const a of agents) if (a.name !== "assistant") profileSelect.append(el("option", { value: a.name, text: a.name }));
    profileSelect.value = ui.profile;
    if (!profileSelect.value) { profileSelect.value = "assistant"; ui.profile = "assistant"; persistUi(); }
    const hasTools = (agents.find((a) => a.name === ui.profile) || { tools: [] }).tools.length > 0;
    actionsLabel.hidden = !hasTools;
  }).catch(() => toast("Не удалось загрузить список агентов"));

  const input = $("#input");
  const form = $("#composer");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    $("#sendBtn").disabled = true;
    sendMessage(text).finally(() => { $("#sendBtn").disabled = false; input.focus(); });
  });
  input.focus();
}

// ---------- память ----------
const KIND_LABEL = { fact: "факт", episode: "эпизод", procedure: "процедура" };

async function viewMemory(view) {
  view.innerHTML = "";
  const kindFilter = el("select", { on: { change: () => loadMemories() } },
    el("option", { value: "", text: "все виды" }),
    el("option", { value: "fact", text: "факты" }),
    el("option", { value: "episode", text: "эпизоды" }),
    el("option", { value: "procedure", text: "процедуры" }));
  const search = el("input", { type: "text", placeholder: "Поиск по памяти", on: { input: debounce(() => loadMemories(), 300) } });
  const list = el("div", { class: "list", id: "memList" });
  const addKind = el("select", {}, el("option", { value: "fact", text: "Факт" }), el("option", { value: "procedure", text: "Процедура" }));
  const addText = el("textarea", { placeholder: "Например: Я предпочитаю краткие ответы" });
  const addStatus = el("div", { class: "small" });

  view.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h3", { text: "Добавить в память" }),
      el("p", { class: "small muted", text: "Сохраняйте только то, что можно помнить. Ключи, пароли и номера карт не сохраняются." }),
      el("div", { class: "row" }, addKind),
      el("div", { class: "field" }, addText),
      el("div", { class: "row" },
        el("button", { class: "btn btn-primary", text: "Сохранить", on: { click: async () => {
          const content = addText.value.trim();
          if (!content) return;
          try {
            const res = await api("/v1/memories", { method: "POST", body: { kind: addKind.value, content } });
            addStatus.textContent = res.created ? "Сохранено." : "Такая запись уже есть.";
            addStatus.className = "small ok";
            addText.value = "";
            loadMemories();
          } catch (err) { addStatus.textContent = err.message; addStatus.className = "small error"; }
        } } }),
        addStatus)),
    el("div", { class: "row", style: "margin-bottom:10px" }, kindFilter, search,
      el("button", { class: "btn", text: "Экспорт JSON", on: { click: exportMemory } }),
      el("button", { class: "btn btn-danger", text: "Очистить всё", on: { click: wipeMemory } })),
    list);

  async function loadMemories() {
    const q = search.value.trim();
    const kind = kindFilter.value;
    try {
      let items;
      if (q) items = (await api(`/v1/memories/search?q=${encodeURIComponent(q)}${kind ? "&kind=" + kind : ""}&limit=50`)).items;
      else items = (await api(`/v1/memories?limit=100${kind ? "&kind=" + kind : ""}`)).items;
      renderMemoryItems(list, items);
    } catch (err) { list.innerHTML = ""; list.append(el("div", { class: "error", text: err.message })); }
  }
  $("#pageTitle").textContent = "Память";
  loadMemories();
}

function renderMemoryItems(list, items) {
  list.innerHTML = "";
  if (!items.length) { list.append(el("div", { class: "muted", text: "Записей нет" })); return; }
  for (const item of items) {
    const body = el("div", { text: item.content });
    const actions = el("div", { class: "row" },
      el("button", { class: "btn small", text: "Изменить", on: { click: () => editMemory(item, body, actions) } }),
      el("button", { class: "btn btn-danger small", text: "Удалить", on: { click: async () => {
        if (!confirm("Удалить запись?")) return;
        await api("/v1/memories/" + item.id, { method: "DELETE" });
        toast("Запись удалена");
        body.parentElement.remove();
      } } }));
    list.append(el("div", { class: "item" },
      el("div", { class: "item-head" }, el("span", { class: "chip", text: KIND_LABEL[item.kind] || item.kind }),
        el("span", { class: "small muted", text: formatDate(item.updated_at) })),
      body, actions));
  }
}

function editMemory(item, body, actions) {
  const area = el("textarea", { text: item.content });
  const save = el("button", { class: "btn btn-primary small", text: "Сохранить", on: { click: async () => {
    try {
      const res = await api("/v1/memories/" + item.id, { method: "PATCH", body: { content: area.value } });
      body.textContent = res.item.content;
      item.content = res.item.content;
      area.replaceWith(body);
      save.remove(); cancel.remove();
      toast("Сохранено");
    } catch (err) { toast(err.message); }
  } } });
  const cancel = el("button", { class: "btn small", text: "Отмена", on: { click: () => { area.replaceWith(body); save.remove(); cancel.remove(); } } });
  body.replaceWith(area);
  actions.prepend(save, cancel);
}

async function exportMemory() {
  try {
    const data = await api("/v1/memories/export");
    downloadBlob(JSON.stringify(data, null, 2), "kali-memory.json", "application/json");
  } catch (err) { toast(err.message); }
}

async function wipeMemory() {
  if (!confirm("Удалить ВСЮ память? Это нельзя отменить.")) return;
  try {
    const res = await api("/v1/memories?confirm=true", { method: "DELETE" });
    toast("Удалено записей: " + res.deleted);
    viewMemory($("#view"));
  } catch (err) { toast(err.message); }
}

function downloadBlob(text, filename, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------- база знаний ----------
async function viewKnowledge(view) {
  $("#pageTitle").textContent = "База знаний";
  view.innerHTML = "";
  const docsEl = el("div", { class: "list" });
  const hitsEl = el("div", { class: "list" });
  const status = el("div", { class: "small muted" });
  const fileInput = el("input", { type: "file", attrs: { accept: ".txt,.md,.csv,.json,.log,.py,.js,.html,.xml,.yaml,.yml,.pdf,.docx,.xlsx" },
    on: { change: async (e) => {
      const file = e.target.files[0];
      e.target.value = "";
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) { toast("Файл больше 10 МБ"); return; }
      status.textContent = "Загрузка «" + file.name + "»…";
      try {
        const content_base64 = await fileToBase64(file);
        const res = await api("/v1/knowledge/documents", { method: "POST", body: { filename: file.name, content_base64 } });
        status.textContent = res.created ? "Документ добавлен: " + res.item.title : "Такой документ уже есть в базе";
        status.className = "small ok";
        loadDocs();
      } catch (err) { status.textContent = err.message; status.className = "small error"; }
    } } });
  const search = el("input", { type: "text", placeholder: "Поиск по базе знаний" });
  view.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h3", { text: "Загрузить документ" }),
      el("p", { class: "small muted", text: "TXT, MD, CSV, JSON, PDF, DOCX, XLSX — до 10 МБ. Одинаковые файлы не дублируются." }),
      fileInput, status),
    el("div", { class: "field" }, el("label", { text: "Поиск по документам" }), search),
    hitsEl,
    el("h3", { text: "Документы", style: "margin-top:14px" }),
    docsEl);

  search.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const q = search.value.trim();
    if (!q) return;
    try {
      const res = await api(`/v1/knowledge/search?q=${encodeURIComponent(q)}&limit=8`);
      hitsEl.innerHTML = "";
      if (!res.items.length) hitsEl.append(el("div", { class: "muted", text: "Ничего не найдено" }));
      for (const h of res.items) {
        hitsEl.append(el("div", { class: "item" },
          el("div", { class: "item-head" }, el("strong", { text: h.title }), el("span", { class: "small muted", text: "фрагмент " + (h.chunk_index + 1) })),
          el("div", { class: "small", text: h.content })));
      }
      if (!res.semantic) hitsEl.prepend(el("div", { class: "small muted", text: "Поиск только по словам: эмбеддинги выключены." }));
    } catch (err) { toast(err.message); }
  });

  async function loadDocs() {
    try {
      const { items } = await api("/v1/knowledge/documents");
      docsEl.innerHTML = "";
      if (!items.length) docsEl.append(el("div", { class: "muted", text: "Документов пока нет" }));
      for (const d of items) {
        docsEl.append(el("div", { class: "item" },
          el("div", { class: "item-head" }, el("strong", { text: d.title }),
            el("button", { class: "btn btn-danger small", text: "Удалить", on: { click: async () => {
              if (!confirm("Удалить документ из базы?")) return;
              await api("/v1/knowledge/documents/" + d.id, { method: "DELETE" });
              loadDocs();
            } } })),
          el("div", { class: "small muted", text: `${d.filename} · ${formatSize(d.size_bytes)} · фрагментов: ${d.chunks} · с эмбеддингами: ${d.embedded_chunks}` })));
      }
    } catch (err) { docsEl.textContent = err.message; }
  }
  loadDocs();
}

// ---------- файлы рабочей папки ----------
async function viewFiles(view) {
  $("#pageTitle").textContent = "Файлы";
  view.innerHTML = "";
  const list = el("div", { class: "list" });
  const status = el("div", { class: "small muted" });
  const input = el("input", { type: "file", on: { change: async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { toast("Файл больше 10 МБ"); return; }
    try {
      const content_base64 = await fileToBase64(file);
      const res = await api("/v1/files", { method: "POST", body: { filename: file.name, content_base64 } });
      status.textContent = "Загружено: " + res.item.name;
      status.className = "small ok";
      load();
    } catch (err) { status.textContent = err.message; status.className = "small error"; }
  } } });
  view.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h3", { text: "Рабочая папка" }),
      el("p", { class: "small muted", text: "Здесь лежат файлы, которые загружаете вы и которые создают агенты. Агент может читать их, а записывать — только с разрешения." }),
      input, status),
    list);

  async function load() {
    try {
      const { items } = await api("/v1/files");
      list.innerHTML = "";
      if (!items.length) list.append(el("div", { class: "muted", text: "Файлов нет" }));
      for (const f of items) {
        list.append(el("div", { class: "item" },
          el("div", { class: "item-head" }, el("strong", { text: f.name }),
            el("div", { class: "row" },
              el("button", { class: "btn small", text: "Скачать", on: { click: () => downloadFile(f.name) } }),
              el("button", { class: "btn btn-danger small", text: "Удалить", on: { click: async () => {
                if (!confirm("Удалить файл «" + f.name + "»?")) return;
                await api("/v1/files/" + encodeURIComponent(f.name), { method: "DELETE" });
                load();
              } } }))),
          el("div", { class: "small muted", text: `${formatSize(f.size_bytes)} · ${formatDate(f.modified)}` })));
      }
    } catch (err) { list.textContent = err.message; }
  }
  load();
}

// ---------- агенты ----------
async function viewAgents(view) {
  $("#pageTitle").textContent = "Агенты";
  view.innerHTML = "";
  try {
    const agents = await loadAgents();
    const tools = await api("/v1/tools");
    const cards = el("div", { class: "grid" });
    for (const a of agents) {
      cards.append(el("div", { class: "card" },
        el("h3", { text: a.name }),
        el("p", { class: "small", text: a.description }),
        el("div", { class: "small muted", text: "Инструменты: " + (a.tools.length ? a.tools.join(", ") : "нет") }),
        el("div", { class: "row", style: "margin-top:10px" },
          el("button", { class: "btn", text: "Открыть в чате", on: { click: () => { ui.profile = a.name; persistUi(); location.hash = "#/chat"; } } }))));
    }
    const toolList = el("div", { class: "list" });
    for (const t of tools.tools) {
      const badge = t.risky ? "требует разрешения" : "безопасный";
      const head = el("div", { class: "item-head" }, el("strong", { text: t.name }), el("span", { class: "chip", text: badge }));
      toolList.append(el("div", { class: "item" }, head));
    }
    view.append(
      el("p", { class: "muted", text: "Агент — это режим работы модели: какие инструменты она видит и как себя ведёт. Рискованные инструменты работают только при явном разрешении в чате." }),
      cards,
      el("h3", { text: "Инструменты", style: "margin-top:18px" }),
      toolList);
  } catch (err) { view.append(el("div", { class: "error", text: err.message })); }
}

// ---------- модели ----------
async function viewModels(view) {
  $("#pageTitle").textContent = "Модели";
  view.innerHTML = "";
  try {
    const [providers, admin] = await Promise.all([api("/v1/providers"), api("/v1/admin/status")]);
    view.append(
      el("div", { class: "card", style: "margin-bottom:14px" },
        el("h3", { text: "Подключённые провайдеры" }),
        providers.providers.length
          ? el("div", { class: "list" }, providers.providers.map((p) => el("div", { class: "item" },
              el("strong", { text: p + (p === providers.default ? " (по умолчанию)" : "") }))))
          : el("div", { class: "error", text: "Ни один провайдер не подключён. Задайте KALI_PROVIDER_API_KEY в файле .env и перезапустите сервер." })),
      el("div", { class: "card", style: "margin-bottom:14px" },
        el("h3", { text: "Модель по умолчанию" }),
        el("div", { text: admin.settings.default_model }),
        el("p", { class: "small muted", text: "Модель в чате выбирается в верхней строке. Список моделей зависит от провайдера и вашего тарифа." })),
      el("div", { class: "card" },
        el("h3", { text: "Эмбеддинги (поиск по смыслу)" }),
        el("div", { text: (admin.semantic_search ? "включены: " : "выключены: ") + admin.settings.embedding_model }),
        el("p", { class: "small muted", text: "Без эмбеддингов база знаний ищет только по словам." })));
  } catch (err) { view.append(el("div", { class: "error", text: err.message })); }
}

// ---------- панель ----------
async function viewDashboard(view) {
  $("#pageTitle").textContent = "Панель";
  view.innerHTML = "";
  try {
    const stats = await api("/v1/stats");
    const cards = [
      ["Факты в памяти", stats.memory.fact || 0],
      ["Процедуры", stats.memory.procedure || 0],
      ["Эпизоды", stats.memory.episode || 0],
      ["Документы", stats.documents],
      ["Фрагменты", stats.chunks],
      ["Инструменты", stats.tools],
      ["Провайдеры", stats.providers],
      ["Диалоги на устройстве", chats.length],
    ];
    view.append(el("div", { class: "grid" }, cards.map(([label, value]) =>
      el("div", { class: "card" }, el("div", { class: "small muted", text: label }), el("div", { class: "stat-value", text: String(value) })))));
    view.append(el("p", { class: "small muted", style: "margin-top:14px",
      text: "Диалоги хранятся в браузере этого устройства, остальные данные — на сервере Kali." }));
  } catch (err) { view.append(el("div", { class: "error", text: err.message })); }
}

// ---------- настройки ----------
function viewSettings(view) {
  $("#pageTitle").textContent = "Настройки";
  view.innerHTML = "";
  const theme = el("select", { on: { change: (e) => { ui.theme = e.target.value; persistUi(); applyTheme(); } } },
    el("option", { value: "auto", text: "Как в системе" }),
    el("option", { value: "light", text: "Светлая" }),
    el("option", { value: "dark", text: "Тёмная" }));
  theme.value = ui.theme;
  const toggle = (label, key) => el("label", { class: "row" },
    el("input", { type: "checkbox", checked: !!ui[key], on: { change: (e) => { ui[key] = e.target.checked; persistUi(); } } }), label);
  view.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h3", { text: "Внешний вид" }),
      el("div", { class: "field" }, el("label", { text: "Тема" }), theme)),
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("h3", { text: "Чат по умолчанию" }),
      toggle("использовать память", "useMemory"),
      toggle("использовать базу знаний", "useKnowledge"),
      toggle("запоминать диалоги в эпизоды", "remember")),
    el("div", { class: "card" },
      el("h3", { text: "Данные на этом устройстве" }),
      el("p", { class: "small muted", text: "Диалоги хранятся в браузере. Память, документы и файлы — на сервере." }),
      el("div", { class: "row" },
        el("button", { class: "btn", text: "Экспорт диалогов", on: { click: () => downloadBlob(JSON.stringify(chats, null, 2), "kali-chats.json", "application/json") } }),
        el("button", { class: "btn btn-danger", text: "Удалить все диалоги", on: { click: () => {
          if (!confirm("Удалить все диалоги на этом устройстве?")) return;
          chats = []; activeChatId = null; persistChats(); toast("Диалоги удалены");
        } } }))),
    el("p", { class: "small muted", style: "margin-top:14px", text: "Настройки сохраняются в этом браузере." }));
}

// ---------- администрирование ----------
async function viewAdmin(view) {
  $("#pageTitle").textContent = "Администрирование";
  view.innerHTML = "";
  const body = el("div");
  view.append(
    el("div", { class: "card", style: "margin-bottom:14px" },
      el("p", { class: "small", text: "Вход по паролю появится на этапе 8 (безопасность). До тех пор держите Kali на своём компьютере и не открывайте его в интернет." })),
    el("div", { class: "row", style: "margin-bottom:12px" },
      el("button", { class: "btn", text: "Обновить", on: { click: () => viewAdmin(view) } })),
    body);
  try {
    const s = await api("/v1/admin/status");
    const rows = [
      ["Версия", s.version],
      ["Среда", s.settings.environment],
      ["Провайдеры", s.providers.join(", ") || "не подключены"],
      ["Поиск по смыслу", s.semantic_search ? "да" : "нет"],
      ["Docker (песочница)", s.docker_available ? "доступен" : "недоступен"],
    ];
    body.append(el("div", { class: "card", style: "margin-bottom:14px" },
      el("table", {}, el("tbody", {}, rows.map(([k, v]) => el("tr", {}, el("th", { text: k }), el("td", { text: v })))))));
    body.append(el("h3", { text: "Настройки (без секретов)" }),
      el("div", { class: "card" }, el("table", {},
        el("tbody", {}, Object.entries(s.settings).map(([k, v]) => el("tr", {}, el("th", { text: k }), el("td", { text: String(v) })))))));
  } catch (err) { body.append(el("div", { class: "error", text: err.message })); }
  if (session && session.role === "admin") await adminExtras(view);
}

// ---------- вход и сессия ----------
function sessionToken() { return session && session.token ? session.token : ""; }
function authHeaders() { const t = sessionToken(); return t ? { Authorization: "Bearer " + t } : {}; }
function endSession() { session = null; localStorage.removeItem(SESSION_KEY); }
function handleUnauthorized() {
  if (session) { endSession(); toast("Сессия истекла, войдите снова"); render(); }
}
function roleName(role) { return role === "admin" ? "администратор" : "пользователь"; }
function renderFoot() {
  const foot = $("#sidebarFoot");
  foot.innerHTML = "";
  foot.append(el("div", { text: appVersion ? "v" + appVersion : "" }));
  if (session) {
    foot.append(el("div", { class: "row", style: "margin-top:6px" },
      el("span", { text: session.username + " · " + roleName(session.role) }),
      el("button", { class: "btn small", text: "Выйти", on: { click: logout } })));
  }
}
async function logout() {
  try { await api("/v1/auth/logout", { method: "POST" }); } catch (e) { /* сессия всё равно закрывается */ }
  endSession();
  toast("Вы вышли");
  location.hash = "#/chat";
  render();
}
function viewLogin(view) {
  view.innerHTML = "";
  const user = el("input", { type: "text", attrs: { autocomplete: "username" } });
  const pass = el("input", { type: "password", attrs: { autocomplete: "current-password" } });
  const status = el("div", { class: "small error" });
  const submit = async (e) => {
    e.preventDefault();
    status.textContent = "";
    try {
      const res = await fetch("/v1/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user.value.trim(), password: pass.value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorText(data, res.status));
      session = { token: data.token, username: data.username, role: data.role, expires_at: data.expires_at };
      saveJson(SESSION_KEY, session);
      location.hash = "#/chat";
      render();
      refreshStatus();
    } catch (err) { status.textContent = err.message; }
  };
  view.append(el("form", { class: "card", style: "max-width:360px;margin:8vh auto", on: { submit } },
    el("h2", { text: "Вход в Kali" }),
    el("div", { class: "field" }, el("label", { text: "Логин" }), user),
    el("div", { class: "field" }, el("label", { text: "Пароль" }), pass),
    status,
    el("button", { class: "btn btn-primary", type: "submit", text: "Войти" }),
    el("p", { class: "small muted", text: "Учётную запись создаёт администратор на этом компьютере: python -m kali.cli create-user --username ИМЯ --role user" })));
  $("#pageTitle").textContent = "Вход";
  setTimeout(() => user.focus(), 0);
}
async function downloadFile(name) {
  try {
    const res = await fetch("/v1/files/" + encodeURIComponent(name), { headers: authHeaders() });
    if (res.status === 401) { handleUnauthorized(); return; }
    if (!res.ok) throw new Error("Не удалось скачать файл");
    const url = URL.createObjectURL(await res.blob());
    const a = el("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (err) { toast(err.message); }
}
async function adminExtras(view) {
  const usersBox = el("div", { class: "list" });
  const auditBox = el("div", { class: "list" });
  const newUser = el("input", { type: "text", placeholder: "логин (3–32 символа)" });
  const newPass = el("input", { type: "password", placeholder: "пароль (не короче 10 символов)" });
  const newRole = el("select", {}, el("option", { value: "user", text: "пользователь" }),
    el("option", { value: "admin", text: "администратор" }));
  const msg = el("div", { class: "small" });
  const loadUsers = async () => {
    const { items } = await api("/v1/users");
    usersBox.innerHTML = "";
    for (const u of items) {
      usersBox.append(el("div", { class: "item" }, el("strong", { text: u.username }),
        el("span", { class: "small muted", text: " · " + roleName(u.role) + " · " + formatDate(u.created_at) })));
    }
  };
  const loadAudit = async () => {
    const { items } = await api("/v1/admin/audit");
    auditBox.innerHTML = "";
    for (const e of items.slice(0, 50)) {
      auditBox.append(el("div", { class: "item small" },
        formatDate(e.ts) + " · " + e.actor + " · " + e.action + (e.detail ? " · " + e.detail : "")));
    }
  };
  view.append(el("h3", { text: "Пользователи", style: "margin-top:18px" }),
    el("div", { class: "card", style: "margin-bottom:12px" },
      el("div", { class: "row" }, newUser, newPass, newRole,
        el("button", { class: "btn btn-primary", text: "Создать", on: { click: async () => {
          try {
            await api("/v1/users", { method: "POST", body: { username: newUser.value.trim(), password: newPass.value, role: newRole.value } });
            msg.textContent = "Пользователь создан"; msg.className = "small ok";
            newUser.value = ""; newPass.value = "";
            loadUsers();
          } catch (err) { msg.textContent = err.message; msg.className = "small error"; }
        } } })),
      msg),
    usersBox,
    el("h3", { text: "Журнал событий", style: "margin-top:18px" }),
    auditBox);
  try { await loadUsers(); await loadAudit(); } catch (err) { view.append(el("div", { class: "error", text: err.message })); }
}

// ---------- маршрутизация ----------
const ROUTES = {
  chat: (v) => renderChatView(),
  memory: viewMemory,
  knowledge: viewKnowledge,
  files: viewFiles,
  agents: viewAgents,
  models: viewModels,
  dashboard: viewDashboard,
  settings: viewSettings,
  admin: viewAdmin,
};

function currentRoute() {
  const name = (location.hash.replace(/^#\/?/, "") || "chat").split("?")[0];
  return ROUTES[name] ? name : "chat";
}

function render() {
  if (!sessionToken()) {
    $("#layout").classList.remove("menu-open");
    renderFoot();
    viewLogin($("#view"));
    return;
  }
  const name = currentRoute();
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  $("#layout").classList.remove("menu-open");
  renderFoot();
  const view = $("#view");
  view.innerHTML = "";
  if (name === "chat") {
    $("#pageTitle").textContent = "Чат";
    renderChatLayout(view);
    refreshConversationList();
    renderMessages($("#messages"), activeChat());
  } else {
    ROUTES[name](view);
  }
}

$("#menuBtn").addEventListener("click", () => $("#layout").classList.toggle("menu-open"));
$("#scrim").addEventListener("click", () => $("#layout").classList.remove("menu-open"));
window.addEventListener("hashchange", render);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#layout").classList.remove("menu-open"); });

if (!location.hash) location.hash = "#/chat";
render();
refreshStatus();
setInterval(refreshStatus, 30000);
})();
