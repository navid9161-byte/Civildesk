"use strict";

// ───────────────────────── ابزارهای کمکی ─────────────────────────
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const faNum = new Intl.NumberFormat("fa-IR");
const num = (v) => (v === null || v === undefined || v === "" ? "" : faNum.format(v));
const faDigits = (s) => String(s ?? "").replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);

let META = null;
let PROJECTS = [];
const state = { tab: localStorageGet("tab") || "dashboard", filters: {} };

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "خطای سرور");
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 2600);
}

const money = (v) => (v || v === 0 ? `${num(v)} ${META.currency}` : "");
const choiceLabel = (entity, field, value) => {
  const f = META.schema[entity].fields.find((x) => x.name === field);
  return (f && f.choices[value]) || value || "";
};

const BADGE_COLORS = {
  priority: { urgent: "red", high: "amber", medium: "", low: "gray" },
  status: {
    todo: "", doing: "amber", waiting: "gray", done: "green", cancelled: "gray",
    active: "green", tender: "", on_hold: "amber", closed: "gray",
    draft: "gray", submitted: "", reviewing: "amber", approved: "green", partial: "amber", paid: "green",
  },
  kind: { income: "green", expense: "red" },
};
const badge = (entity, field, value) =>
  value ? `<span class="badge ${BADGE_COLORS[field]?.[value] ?? ""}">${esc(choiceLabel(entity, field, value))}</span>` : "";

// ───────────────────────── تب‌ها ─────────────────────────
const TABS = [
  ["dashboard", "داشبورد"],
  ["chat", "دستیار 🤖"],
  ["tasks", "وظایف"],
  ["projects", "پروژه‌ها"],
  ["daily_reports", "گزارش روزانه"],
  ["invoices", "صورت‌وضعیت"],
  ["transactions", "دریافت و پرداخت"],
  ["contacts", "مخاطبین"],
  ["notes", "یادداشت‌ها"],
];

function renderTabs() {
  $("#tabs").innerHTML = TABS.map(([k, l]) => `<button data-tab="${k}" class="${state.tab === k ? "active" : ""}">${l}</button>`).join("");
}
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) go(b.dataset.tab);
});

function go(tab) {
  state.tab = tab;
  localStorageSet("tab", tab);
  renderTabs();
  render();
}

async function render() {
  const view = $("#view");
  try {
    if (state.tab === "dashboard") await renderDashboard(view);
    else if (state.tab === "chat") await renderChat(view);
    else await renderEntity(view, state.tab);
  } catch (e) {
    view.innerHTML = `<div class="card error">${esc(e.message)}</div>`;
  }
}

async function loadProjects() {
  PROJECTS = await api("/api/projects");
}

// ───────────────────────── رندر آیتم‌ها ─────────────────────────
function itemHTML(entity, r, today) {
  const m = [];
  let snippet = "";
  let pre = "";
  let cls = "";
  let title = r[META.schema[entity].title_field];
  switch (entity) {
    case "tasks": {
      const isDone = r.status === "done" || r.status === "cancelled";
      cls = isDone ? "done" : "";
      pre = `<input type="checkbox" data-done="${r.id}" ${r.status === "done" ? "checked" : ""} title="انجام شد">`;
      if (r.due_date) {
        const late = !isDone && today && r.due_date < today;
        const isToday = r.due_date === today;
        m.push(`<span class="${late ? "badge red" : isToday ? "badge amber" : ""}">📅 ${faDigits(r.due_date)}${r.due_time ? " ⏰" + faDigits(r.due_time) : ""}</span>`);
      }
      if (r.project_name) m.push(`🏗️ ${esc(r.project_name)}`);
      if (r.priority && r.priority !== "medium") m.push(badge(entity, "priority", r.priority));
      if (r.status !== "todo") m.push(badge(entity, "status", r.status));
      if (r.category && r.category !== "work") m.push(esc(choiceLabel(entity, "category", r.category)));
      if (r.remind_at && !r.reminded) m.push(`🔔 ${faDigits(r.remind_at)}`);
      snippet = r.description;
      break;
    }
    case "projects":
      m.push(badge(entity, "status", r.status));
      if (r.role) m.push(esc(choiceLabel(entity, "role", r.role)));
      if (r.employer) m.push(`کارفرما: ${esc(r.employer)}`);
      if (r.location) m.push(`📍 ${esc(r.location)}`);
      if (r.end_date) m.push(`پایان: ${faDigits(r.end_date)}`);
      if (r.contract_amount) m.push(money(r.contract_amount));
      snippet = "";
      m.push(`پیشرفت ${num(r.progress || 0)}٪`);
      break;
    case "daily_reports":
      title = `${faDigits(r.report_date)} — ${r.project_name || ""}`;
      if (r.weather) m.push(`🌤 ${esc(r.weather)}${r.temperature ? " " + esc(r.temperature) : ""}`);
      if (r.manpower) m.push(`👷 ${esc(r.manpower.slice(0, 60))}`);
      snippet = r.work_done + (r.issues ? `\n⚠️ ${r.issues}` : "");
      break;
    case "invoices":
      title = `صورت‌وضعیت ${r.number} ${choiceLabel(entity, "kind", r.kind)} — ${r.project_name || ""}`;
      m.push(badge(entity, "status", r.status));
      m.push(`خالص: ${money(r.net_amount)}`);
      if (r.paid_amount) m.push(`پرداختی: ${money(r.paid_amount)}`);
      if (r.submit_date) m.push(`ارسال: ${faDigits(r.submit_date)}`);
      break;
    case "transactions":
      m.push(badge(entity, "kind", r.kind));
      m.push(`<b>${money(r.amount)}</b>`);
      m.push(faDigits(r.tx_date));
      if (r.category) m.push(esc(r.category));
      if (r.counterparty) m.push(esc(r.counterparty));
      if (r.project_name) m.push(`🏗️ ${esc(r.project_name)}`);
      break;
    case "contacts":
      if (r.role || r.company) m.push(esc([r.role, r.company].filter(Boolean).join(" — ")));
      if (r.phone) m.push(`<a href="tel:${esc(r.phone)}" onclick="event.stopPropagation()">📞 ${esc(r.phone)}</a>`);
      if (r.email) m.push(`✉️ ${esc(r.email)}`);
      if (r.project_name) m.push(`🏗️ ${esc(r.project_name)}`);
      snippet = r.notes;
      break;
    case "notes":
      if (r.tags) m.push(r.tags.split(/[,،]/).map((t) => `<span class="badge gray">${esc(t.trim())}</span>`).join(" "));
      if (r.project_name) m.push(`🏗️ ${esc(r.project_name)}`);
      m.push(faDigits((r.updated_at || "").slice(0, 10)));
      snippet = r.content;
      break;
  }
  const progress =
    entity === "projects" ? `<div class="progress"><span style="width:${Math.min(100, r.progress || 0)}%"></span></div>` : "";
  return `<div class="item ${cls}" data-entity="${entity}" data-id="${r.id}">
    ${pre}
    <div class="body">
      <div class="title">${esc(title)}</div>
      <div class="meta">${m.filter(Boolean).map((x) => `<span>${x}</span>`).join("")}</div>
      ${progress}
      ${snippet ? `<div class="snippet">${esc(snippet)}</div>` : ""}
    </div>
  </div>`;
}

document.addEventListener("click", async (e) => {
  const cb = e.target.closest("input[data-done]");
  if (cb) {
    e.stopPropagation();
    try {
      await api(`/api/tasks/${cb.dataset.done}`, { method: "PATCH", body: { status: cb.checked ? "done" : "todo" } });
      toast(cb.checked ? "✔ انجام شد" : "برگشت به انجام‌نشده");
      render();
    } catch (err) { toast(err.message); }
    return;
  }
  const item = e.target.closest(".item[data-entity]");
  if (item) {
    const rec = await api(`/api/${item.dataset.entity}/${item.dataset.id}`);
    openForm(item.dataset.entity, rec);
  }
});

// ───────────────────────── داشبورد ─────────────────────────
async function renderDashboard(view) {
  const d = await api("/api/dashboard");
  const list = (rows, empty) =>
    rows.length ? `<div class="list mini">${rows.map((r) => itemHTML("tasks", r, d.today)).join("")}</div>` : `<div class="empty">${empty}</div>`;
  const projects = d.projects.map((p) => {
    const late = p.days_left !== null && p.days_left < 0;
    return `<div class="item" data-entity="projects" data-id="${p.id}"><div class="body">
      <div class="title">${esc(p.name)} ${badge("projects", "status", p.status)}</div>
      <div class="meta">
        <span>پیشرفت ${num(p.progress || 0)}٪</span>
        <span>${num(p.open_tasks)} کار باز</span>
        ${p.days_left !== null ? `<span class="${late ? "badge red" : ""}">${late ? `${num(-p.days_left)} روز تأخیر` : `${num(p.days_left)} روز مانده`}</span>` : ""}
        ${p.status === "active" ? (p.last_report === d.today ? `<span class="badge green">گزارش امروز ✓</span>` : `<span class="badge amber">گزارش امروز ثبت نشده</span>`) : ""}
      </div>
      <div class="progress"><span style="width:${Math.min(100, p.progress || 0)}%"></span></div>
    </div></div>`;
  }).join("");
  const invoices = d.pending_invoices.map((i) => `<div class="item" data-entity="invoices" data-id="${i.id}"><div class="body">
      <div class="title">${esc(i.project_name)} — صورت‌وضعیت ${esc(i.number)}</div>
      <div class="meta"><span>${badge("invoices", "status", i.status)}</span><span>مانده: ${money(i.outstanding)}</span>
        ${i.days_waiting !== null ? `<span class="${i.days_waiting > 30 ? "badge red" : ""}">${num(i.days_waiting)} روز از ارسال</span>` : ""}</div>
    </div></div>`).join("");

  view.innerHTML = `
    ${META.ai_enabled ? `<form class="quick" id="quick"><input name="q" placeholder="سریع بنویس… مثلاً «پنج‌شنبه ساعت ۹ جلسه با کارفرمای پروژه مهر، یادم بنداز»" autocomplete="off"><button class="btn primary">ثبت</button></form>` : ""}
    <div class="stats">
      <div class="stat ${d.overdue.length ? "bad" : ""}"><div class="v">${num(d.overdue.length)}</div><div class="l">کار عقب‌افتاده</div></div>
      <div class="stat"><div class="v">${num(d.due_today.length)}</div><div class="l">کار امروز</div></div>
      <div class="stat"><div class="v">${num(d.projects.filter((p) => p.status === "active").length)}</div><div class="l">پروژه‌ی فعال</div></div>
      <div class="stat good"><div class="v">${num(d.month.income)}</div><div class="l">دریافتی این ماه (${META.currency})</div></div>
      <div class="stat bad"><div class="v">${num(d.month.expense)}</div><div class="l">هزینه‌ی این ماه (${META.currency})</div></div>
    </div>
    <div class="grid">
      ${d.overdue.length ? `<div class="card"><h3>🔴 عقب‌افتاده <span class="count">${num(d.overdue.length)}</span></h3>${list(d.overdue, "")}</div>` : ""}
      <div class="card"><h3>📌 امروز <span class="count">${num(d.due_today.length)}</span></h3>${list(d.due_today, "برای امروز کاری ثبت نشده.")}</div>
      <div class="card"><h3>🗓 هفت روز آینده <span class="count">${num(d.upcoming.length)}</span></h3>${list(d.upcoming, "موردی نیست.")}</div>
      ${d.important_undated.length ? `<div class="card"><h3>❗ مهم بدون مهلت</h3>${list(d.important_undated, "")}</div>` : ""}
      <div class="card"><h3>🏗️ پروژه‌ها</h3>${projects ? `<div class="list mini">${projects}</div>` : `<div class="empty">هنوز پروژه‌ای ثبت نشده.</div>`}</div>
      <div class="card"><h3>💰 صورت‌وضعیت‌های در انتظار پرداخت</h3>${invoices ? `<div class="list mini">${invoices}</div>` : `<div class="empty">موردی نیست.</div>`}</div>
    </div>`;

  const quick = $("#quick");
  if (quick) {
    quick.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = quick.q;
      const text = input.value.trim();
      if (!text) return;
      input.disabled = true;
      input.value = "در حال پردازش…";
      try {
        const r = await api("/api/chat", { method: "POST", body: { message: text } });
        toast(r.reply.slice(0, 180));
        render();
      } catch (err) {
        toast(err.message);
        input.value = text;
        input.disabled = false;
      }
    });
  }
}

// ───────────────────────── فهرست موجودیت‌ها ─────────────────────────
async function renderEntity(view, entity) {
  const s = META.schema[entity];
  const f = (state.filters[entity] ||= { search: "", status: "", project_id: "", open: entity === "tasks" });
  const hasStatus = s.fields.some((x) => x.name === "status");
  const hasProject = s.fields.some((x) => x.type === "project");
  const statusField = s.fields.find((x) => x.name === "status");

  const params = new URLSearchParams({ limit: 300 });
  if (f.search) params.set("search", f.search);
  if (f.status) params.set("status", f.status);
  if (f.project_id) params.set("project_id", f.project_id);
  if (entity === "tasks" && f.open && !f.status) params.set("include_closed", "false");
  const rows = await api(`/api/${entity}?${params}`);

  let summary = "";
  if (entity === "transactions") {
    const inc = rows.filter((r) => r.kind === "income").reduce((a, r) => a + r.amount, 0);
    const exp = rows.filter((r) => r.kind === "expense").reduce((a, r) => a + r.amount, 0);
    summary = `<div class="stats"><div class="stat good"><div class="v">${num(inc)}</div><div class="l">جمع دریافتی</div></div>
      <div class="stat bad"><div class="v">${num(exp)}</div><div class="l">جمع هزینه</div></div>
      <div class="stat"><div class="v">${num(inc - exp)}</div><div class="l">مانده (${META.currency})</div></div></div>`;
  }

  view.innerHTML = `
    <div class="toolbar">
      <button class="btn primary" id="add">+ ${esc(s.label)} جدید</button>
      <input type="search" id="f-search" placeholder="جستجو…" value="${esc(f.search)}">
      ${hasProject ? `<select id="f-project"><option value="">همه‌ی پروژه‌ها</option>${PROJECTS.map((p) => `<option value="${p.id}" ${String(p.id) === String(f.project_id) ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>` : ""}
      ${hasStatus ? `<select id="f-status"><option value="">${entity === "tasks" ? "وضعیت: همه" : "همه‌ی وضعیت‌ها"}</option>${Object.entries(statusField.choices).map(([k, l]) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>` : ""}
      ${entity === "tasks" ? `<label><input type="checkbox" id="f-open" ${f.open ? "checked" : ""}> فقط باز</label>` : ""}
    </div>
    ${summary}
    <div class="list">${rows.length ? rows.map((r) => itemHTML(entity, r, META.today)).join("") : `<div class="card empty">موردی پیدا نشد.</div>`}</div>`;

  $("#add").onclick = () => openForm(entity, null);
  let t;
  $("#f-search").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { f.search = e.target.value; renderEntity(view, entity).then(() => { const el = $("#f-search"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }); }, 300); };
  if (hasProject) $("#f-project").onchange = (e) => { f.project_id = e.target.value; render(); };
  if (hasStatus) $("#f-status").onchange = (e) => { f.status = e.target.value; render(); };
  if (entity === "tasks") $("#f-open").onchange = (e) => { f.open = e.target.checked; render(); };
}

// ───────────────────────── فرم ایجاد / ویرایش ─────────────────────────
function inputFor(field, value) {
  const name = `name="${field.name}"`;
  const v = value ?? "";
  switch (field.type) {
    case "longtext":
      return `<textarea ${name}>${esc(v)}</textarea>`;
    case "choice":
      return `<select ${name}>${field.required ? "" : `<option value=""></option>`}${Object.entries(field.choices)
        .map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    case "project":
      return `<select ${name}><option value=""></option>${PROJECTS.map((p) => `<option value="${p.id}" ${p.id === v ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>`;
    case "money":
    case "int":
    case "percent":
      return `<input ${name} inputmode="numeric" value="${v === "" ? "" : Number(v).toLocaleString("en-US")}" data-num>`;
    case "date":
      return `<input ${name} placeholder="۱۴۰۵/۰۷/۰۵" value="${esc(v)}" inputmode="numeric">`;
    case "datetime":
      return `<input ${name} placeholder="۱۴۰۵/۰۷/۰۵ ۰۹:۰۰" value="${esc(v)}">`;
    default:
      return `<input ${name} value="${esc(v)}">`;
  }
}

function openForm(entity, rec) {
  const s = META.schema[entity];
  const modal = $("#modal");
  $("#modal-title").textContent = rec ? `ویرایش ${s.label}` : `${s.label} جدید`;
  $("#modal-error").textContent = "";
  const defaults = {};
  if (!rec) {
    for (const f of s.fields) {
      if (f.default !== null) defaults[f.name] = f.default;
      if (f.type === "date" && (f.name === s.date_field)) defaults[f.name] = META.today;
      if (f.type === "project" && state.filters[entity]?.project_id) defaults[f.name] = Number(state.filters[entity].project_id);
    }
    if (entity === "tasks") delete defaults.due_date;
  }
  const values = rec || defaults;
  $("#modal-body").innerHTML = s.fields
    .filter((f) => !f.system)
    .map((f) => `<label class="${f.type === "longtext" || f.name === s.title_field ? "wide" : ""}">
        ${esc(f.label)}${f.required ? " *" : ""}
        ${inputFor(f, values[f.name])}
        ${f.help ? `<small>${esc(f.help)}</small>` : ""}
      </label>`).join("");
  $("#modal-body").querySelectorAll("[data-num]").forEach((el) =>
    el.addEventListener("input", () => {
      const digits = el.value.replace(/[۰-۹]/g, (d) => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/[^\d]/g, "");
      el.value = digits ? Number(digits).toLocaleString("en-US") : "";
    }));
  $("#modal-delete").hidden = !rec;
  $("#modal-delete").onclick = async () => {
    if (!confirm(`این ${s.label} حذف شود؟`)) return;
    try {
      await api(`/api/${entity}/${rec.id}`, { method: "DELETE" });
      modal.close();
      toast("حذف شد");
      if (entity === "projects") await loadProjects();
      render();
    } catch (err) { $("#modal-error").textContent = err.message; }
  };
  $("#modal-cancel").onclick = () => modal.close();
  $("#modal-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const f of s.fields.filter((x) => !x.system)) {
      const el = $("#modal-form").elements[f.name];
      let v = el.value.trim();
      if (el.dataset.num !== undefined) v = v.replace(/,/g, "");
      body[f.name] = v === "" ? null : v;
    }
    try {
      if (rec) await api(`/api/${entity}/${rec.id}`, { method: "PATCH", body });
      else await api(`/api/${entity}`, { method: "POST", body });
      modal.close();
      toast("ذخیره شد ✔");
      if (entity === "projects") await loadProjects();
      render();
    } catch (err) { $("#modal-error").textContent = err.message; }
  };
  modal.showModal();
}

// ───────────────────────── گفتگو با دستیار ─────────────────────────
const SUGGESTIONS = [
  "امروز چه کارهایی دارم؟",
  "یک پروژه‌ی جدید ثبت کن",
  "گزارش روزانه‌ی امروز رو ثبت کن",
  "وضعیت مالی این ماه چطوره؟",
  "یک نامه‌ی درخواست تمدید مدت قرارداد بنویس",
];

async function renderChat(view) {
  if (!META.ai_enabled) {
    view.innerHTML = `<div class="card">برای فعال شدن دستیار هوشمند، متغیر <code>ANTHROPIC_API_KEY</code> را در فایل <code>.env</code> تنظیم و برنامه را دوباره اجرا کنید.</div>`;
    return;
  }
  const history = await api("/api/chat/history");
  view.innerHTML = `<div class="chat">
    <div class="toolbar"><button class="btn" id="chat-clear">گفتگوی تازه</button></div>
    <div class="messages" id="msgs">${history.map((m) => `<div class="msg ${m.role}">${esc(m.content)}</div>`).join("")}</div>
    <div class="suggestions">${SUGGESTIONS.map((s) => `<button type="button">${esc(s)}</button>`).join("")}</div>
    <form id="chat-form"><textarea name="m" rows="1" placeholder="هر چی می‌خوای بنویس… (Enter برای ارسال، Shift+Enter خط جدید)"></textarea><button class="btn primary">ارسال</button></form>
  </div>`;
  const msgs = $("#msgs");
  msgs.scrollTop = msgs.scrollHeight;
  const form = $("#chat-form");
  const ta = form.m;
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; });
  $(".suggestions").onclick = (e) => { if (e.target.tagName === "BUTTON") { ta.value = e.target.textContent; ta.focus(); } };
  $("#chat-clear").onclick = async () => { await api("/api/chat/history", { method: "DELETE" }); render(); };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) return;
    ta.value = "";
    ta.style.height = "auto";
    msgs.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div><div class="msg assistant pending">در حال فکر کردن…</div>`);
    msgs.scrollTop = msgs.scrollHeight;
    const pending = msgs.lastElementChild;
    form.querySelector("button").disabled = true;
    try {
      const r = await api("/api/chat", { method: "POST", body: { message: text } });
      pending.classList.remove("pending");
      pending.textContent = r.reply;
      if (r.actions.some((a) => a.startsWith("projects_"))) loadProjects();
    } catch (err) {
      pending.textContent = "⚠️ " + err.message;
    }
    form.querySelector("button").disabled = false;
    msgs.scrollTop = msgs.scrollHeight;
  };
  ta.focus();
}

// ───────────────────────── شروع ─────────────────────────
(async function init() {
  try {
    META = await api("/api/meta");
    await loadProjects();
  } catch (e) {
    $("#view").innerHTML = `<div class="card error">${esc(e.message)}</div>`;
    return;
  }
  const d = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(new Date());
  $("#today").textContent = d;
  if (!TABS.some(([k]) => k === state.tab)) state.tab = "dashboard";
  renderTabs();
  render();
})();
