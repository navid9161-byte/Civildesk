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
  toast._t = setTimeout(() => (t.hidden = true), Math.max(2600, msg.length * 70));
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
const ICONS = {
  dashboard: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
  docs: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5M8 7h8M8 11h6",
  chat: "M12 8V4H8M4 8h16v12H4zM2 14h2M20 14h2M9 13v2M15 13v2",
  tasks: "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
  projects: "M3 21h18M5 21V11l5-3v13M10 21V5l9 4v12M13 10h3M13 14h3M13 18h3",
  daily_reports: "M9 3h6v3H9zM8 4.5H6a1 1 0 0 0-1 1V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V5.5a1 1 0 0 0-1-1h-2M9 11h6M9 15h4",
  invoices: "M5 3v18l2-1.5L9 21l2-1.5L13 21l2-1.5L17 21l2-1.5V3l-2 1.5L15 3l-2 1.5L11 3 9 4.5 7 3zM9 9h6M9 13h6",
  transactions: "M3 7h15a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H3zM3 7l12-4v4M16 13.5h.01",
  contacts: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  notes: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  alert: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  target: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  calendar: "M3 5h18v16H3zM3 10h18M8 3v4M16 3v4",
  flag: "M4 22V4M4 4h13l-2 4 2 4H4",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  plus: "M12 5v14M5 12h14",
};
const ico = (name, cls = "ico") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name] || ""}"/></svg>`;

const TABS = [
  ["dashboard", "داشبورد"],
  ["docs", "اسناد و پرسش"],
  ["chat", "دستیار هوشمند"],
  ["tasks", "وظایف"],
  ["projects", "پروژه‌ها"],
  ["daily_reports", "گزارش روزانه"],
  ["invoices", "صورت‌وضعیت"],
  ["transactions", "دریافت و پرداخت"],
  ["contacts", "مخاطبین"],
  ["notes", "یادداشت‌ها"],
];
const BOTTOM_TABS = ["dashboard", "tasks", "docs", "projects"];
const tabLabel = (k) => (TABS.find(([key]) => key === k) || [k, k])[1];

function renderTabs() {
  $("#tabs").innerHTML = TABS.map(([k, l]) =>
    `<button data-tab="${k}" class="${state.tab === k ? "active" : ""}">${ico(k)}<span>${l}</span></button>`).join("");
  $("#bottomnav").innerHTML = BOTTOM_TABS.map((k) =>
    `<button data-tab="${k}" class="${state.tab === k ? "active" : ""}">${ico(k)}<span>${tabLabel(k)}</span></button>`).join("")
    + `<button data-menu="1" class="${BOTTOM_TABS.includes(state.tab) ? "" : "active"}">${ico("more")}<span>بیشتر</span></button>`;
  $("#page-title").textContent = tabLabel(state.tab);
  document.title = `${tabLabel(state.tab)} · سیویل‌دسک`;
}
function toggleMenu(open) {
  document.body.classList.toggle("menu-open", open);
}
for (const id of ["#tabs", "#bottomnav"]) {
  $(id).addEventListener("click", (e) => {
    if (e.target.closest("[data-menu]")) return toggleMenu(true);
    const b = e.target.closest("button[data-tab]");
    if (b) { toggleMenu(false); go(b.dataset.tab); }
  });
}
$("#menu-btn").onclick = () => toggleMenu(!document.body.classList.contains("menu-open"));
$("#scrim").onclick = () => toggleMenu(false);

function go(tab) {
  state.tab = tab;
  localStorageSet("tab", tab);
  renderTabs();
  window.scrollTo(0, 0);
  render();
}

async function render() {
  const view = $("#view");
  try {
    if (state.tab === "dashboard") await renderDashboard(view);
    else if (state.tab === "chat") await renderChat(view);
    else if (state.tab === "docs") await renderDocs(view);
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

  const h = new Date().getHours();
  const greet = h < 5 ? "شب بخیر" : h < 12 ? "صبح بخیر" : h < 17 ? "روز بخیر" : "عصر بخیر";
  const activeProjects = d.projects.filter((p) => p.status === "active").length;
  view.innerHTML = `
    <section class="hero">
      <div class="hero-text">
        <div class="hero-kicker">${esc(d.today_long)}</div>
        <h2>${greet}، ${esc(META.owner)} 👷</h2>
        <p>${d.overdue.length ? `${num(d.overdue.length)} کار عقب‌افتاده و ` : ""}${num(d.due_today.length)} کار برای امروز داری${activeProjects ? ` · ${num(activeProjects)} پروژه‌ی فعال` : ""}.</p>
      </div>
      <div class="hero-actions">
        <button class="btn gold" data-new="tasks">${ico("plus")} وظیفه‌ی جدید</button>
        <button class="btn ghost" data-new="daily_reports">${ico("daily_reports")} گزارش امروز</button>
        <button class="btn ghost" data-go="docs">${ico("docs")} پرسش از اسناد</button>
      </div>
      ${META.ai_enabled ? `<form class="quick" id="quick"><input name="q" placeholder="سریع بنویس… مثلاً «پنج‌شنبه ساعت ۹ جلسه با کارفرمای پروژه مهر، یادم بنداز»" autocomplete="off"><button class="btn gold">ثبت</button></form>` : ""}
    </section>
    <div class="stats">
      <div class="stat ${d.overdue.length ? "bad" : ""}">${ico("alert")}<div><div class="v">${num(d.overdue.length)}</div><div class="l">کار عقب‌افتاده</div></div></div>
      <div class="stat">${ico("target")}<div><div class="v">${num(d.due_today.length)}</div><div class="l">کار امروز</div></div></div>
      <div class="stat">${ico("projects")}<div><div class="v">${num(activeProjects)}</div><div class="l">پروژه‌ی فعال</div></div></div>
      <div class="stat good">${ico("transactions")}<div><div class="v">${num(d.month.income)}</div><div class="l">دریافتی این ماه (${META.currency})</div></div></div>
      <div class="stat bad">${ico("invoices")}<div><div class="v">${num(d.month.expense)}</div><div class="l">هزینه‌ی این ماه (${META.currency})</div></div></div>
    </div>
    <div class="grid">
      ${d.overdue.length ? `<div class="card danger-edge"><h3>${ico("alert")} عقب‌افتاده <span class="count">${num(d.overdue.length)}</span></h3>${list(d.overdue, "")}</div>` : ""}
      <div class="card"><h3>${ico("target")} امروز <span class="count">${num(d.due_today.length)}</span></h3>${list(d.due_today, "برای امروز کاری ثبت نشده.")}</div>
      <div class="card"><h3>${ico("calendar")} هفت روز آینده <span class="count">${num(d.upcoming.length)}</span></h3>${list(d.upcoming, "موردی نیست.")}</div>
      ${d.important_undated.length ? `<div class="card"><h3>${ico("flag")} مهم بدون مهلت</h3>${list(d.important_undated, "")}</div>` : ""}
      <div class="card"><h3>${ico("projects")} پروژه‌ها</h3>${projects ? `<div class="list mini">${projects}</div>` : `<div class="empty">هنوز پروژه‌ای ثبت نشده.</div>`}</div>
      <div class="card"><h3>${ico("invoices")} صورت‌وضعیت‌های در انتظار پرداخت</h3>${invoices ? `<div class="list mini">${invoices}</div>` : `<div class="empty">موردی نیست.</div>`}</div>
    </div>`;

  view.querySelectorAll("[data-new]").forEach((b) => b.onclick = () => openForm(b.dataset.new, null));
  view.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => go(b.dataset.go));
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

// ───────────────────────── اسناد و پرسش ─────────────────────────
const docsState = { selected: new Set(), last: null, poll: null, asking: false, question: "" };
const STATUS_FA = { queued: "در صف", processing: "در حال پردازش", ready: "آماده", error: "خطا" };

// یکسان‌سازی ساده برای پررنگ کردن واژه‌ها (هم‌راستا با textnorm.normalize در سرور)
function normFa(s) {
  return String(s || "")
    .replace(/[يى]/g, "ی").replace(/ك/g, "ک").replace(/[ۀة]/g, "ه").replace(/[أإآ]/g, "ا")
    .replace(/[ً-ٰٟـ‌]/g, "")
    .replace(/[۰-۹]/g, (d) => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d))
    .toLowerCase();
}
function markTerms(text, terms) {
  if (!terms || !terms.length) return esc(text);
  return String(text).split(/(\s+)/).map((w) => {
    const n = normFa(w).replace(/[^\p{L}\p{N}]/gu, "");
    return n && terms.some((t) => n.startsWith(t)) ? `<mark>${esc(w)}</mark>` : esc(w);
  }).join("");
}

async function renderDocs(view) {
  const data = await api("/api/documents");
  const { documents: docs, stats } = data;
  const pending = docs.some((d) => d.status === "queued" || d.status === "processing");
  const semantic = { ready: "فعال", pending: "در حال آماده‌سازی", failed: "غیرفعال" }[stats.semantic];
  const answerMode = META.llm_provider ? "پاسخ نوشته‌شده با هوش مصنوعی" : "نمایش بخش‌های مرتبط از متن اسناد";

  const docItems = docs.map((d) => {
    const prog = d.status === "processing" && d.pages ? Math.round((100 * d.pages_done) / d.pages) : null;
    const badgeCls = { ready: "green", error: "red", processing: "amber", queued: "gray" }[d.status];
    return `<div class="doc ${docsState.selected.has(d.id) ? "sel" : ""}">
      <input type="checkbox" data-sel="${d.id}" ${docsState.selected.has(d.id) ? "checked" : ""} title="فقط در این سند بگرد" ${d.status !== "ready" ? "disabled" : ""}>
      <div class="body">
        <div class="title">${esc(d.title)}</div>
        <div class="meta">
          <span class="badge ${badgeCls}">${STATUS_FA[d.status] || d.status}${prog !== null ? ` ${num(prog)}٪` : ""}</span>
          ${d.pages ? `<span>${num(d.pages)} صفحه</span>` : ""}
          ${d.ocr_pages ? `<span title="صفحه‌های اسکن‌شده یا خراب که از روی تصویر خوانده شدند">🔍 ${num(d.ocr_pages)} صفحه OCR</span>` : ""}
          ${d.fixed_pages ? `<span>↔️ ${num(d.fixed_pages)} صفحه اصلاح جهت</span>` : ""}
          ${d.weak_pages ? `<span class="badge amber" title="متن این صفحه‌ها کیفیت پایینی دارد">${num(d.weak_pages)} صفحه کم‌کیفیت</span>` : ""}
          <span>${num(Math.round(d.size / 1024))} KB</span>
        </div>
        ${prog !== null ? `<div class="progress"><span style="width:${prog}%"></span></div>` : ""}
        ${d.error ? `<div class="error">${esc(d.error)}</div>` : ""}
      </div>
      <div class="doc-actions">
        <a class="btn" href="/api/documents/${d.id}/file" target="_blank" title="باز کردن فایل">📄</a>
        ${d.status === "error" || d.status === "ready" ? `<button class="btn" data-reprocess="${d.id}" title="پردازش دوباره">↻</button>` : ""}
        <button class="btn danger" data-del="${d.id}" title="حذف">🗑</button>
      </div>
    </div>`;
  }).join("");

  view.innerHTML = `
    <div class="card ask">
      <form id="ask-form">
        <textarea name="q" rows="2" placeholder="سؤالت را بنویس… مثلاً «حداقل پوشش بتن برای تیر در شرایط محیطی شدید چقدر است؟»">${esc(docsState.question)}</textarea>
        <div class="ask-row">
          <span class="muted">${docsState.selected.size ? `جستجو فقط در ${num(docsState.selected.size)} سند انتخاب‌شده · <a href="#" id="sel-clear">همه‌ی اسناد</a>` : `جستجو در همه‌ی اسناد (${num(docs.filter((d) => d.status === "ready").length)})`}</span>
          <button class="btn primary" ${docsState.asking ? "disabled" : ""}>${docsState.asking ? "در حال جستجو…" : "بپرس"}</button>
        </div>
      </form>
    </div>
    <div id="answer">${docsState.last ? answerHTML(docsState.last) : ""}</div>
    <div class="card">
      <h3>${ico("docs")} کتابخانه‌ی اسناد <span class="count">${num(stats.n)} سند · ${num(stats.pages)} صفحه</span></h3>
      <label class="drop" id="drop">
        <input type="file" id="files" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.docx,.txt,.md" hidden>
        <b>فایل‌ها را اینجا رها کنید یا کلیک کنید</b>
        <small>PDF (متنی یا اسکن‌شده)، عکس، Word و متن — می‌توانید چند فایل را با هم انتخاب کنید</small>
        <div class="progress" id="up-prog" hidden><span style="width:0"></span></div>
      </label>
      <div class="muted small">OCR: ${stats.ocr ? "فعال" : "نصب نیست (فایل‌های اسکن‌شده خوانده نمی‌شوند)"} · جستجوی معنایی: ${semantic} · حالت پاسخ: ${answerMode}</div>
      <div class="list docs">${docItems || `<div class="empty">هنوز سندی بارگذاری نشده.</div>`}</div>
    </div>`;

  // پرسش
  const form = $("#ask-form");
  form.q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const q = form.q.value.trim();
    if (!q || docsState.asking) return;
    docsState.question = q;
    docsState.asking = true;
    renderDocs(view);
    try {
      docsState.last = await api("/api/ask", { method: "POST", body: { question: q, doc_ids: [...docsState.selected] } });
    } catch (err) {
      docsState.last = { error: err.message, results: [] };
    }
    docsState.asking = false;
    if (state.tab === "docs") renderDocs(view);
  };
  const clr = $("#sel-clear");
  if (clr) clr.onclick = (e) => { e.preventDefault(); docsState.selected.clear(); renderDocs(view); };

  // بارگذاری
  const input = $("#files");
  const drop = $("#drop");
  input.onchange = () => upload(input.files, view);
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files, view); });

  // عملیات روی اسناد
  view.querySelectorAll("[data-sel]").forEach((cb) => cb.onchange = () => {
    const id = Number(cb.dataset.sel);
    cb.checked ? docsState.selected.add(id) : docsState.selected.delete(id);
    renderDocs(view);
  });
  view.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("این سند و همه‌ی متن استخراج‌شده‌اش حذف شود؟")) return;
    await api(`/api/documents/${b.dataset.del}`, { method: "DELETE" });
    docsState.selected.delete(Number(b.dataset.del));
    renderDocs(view);
  });
  view.querySelectorAll("[data-reprocess]").forEach((b) => b.onclick = async () => {
    await api(`/api/documents/${b.dataset.reprocess}/reprocess`, { method: "POST" });
    toast("دوباره در صف پردازش قرار گرفت");
    renderDocs(view);
  });
  bindAnswer(view);

  clearTimeout(docsState.poll);
  if (pending) docsState.poll = setTimeout(() => { if (state.tab === "docs" && !docsState.asking) renderDocs(view); }, 3000);
}

function answerHTML(a) {
  if (a.error && !a.results?.length) return `<div class="card error">${esc(a.error)}</div>`;
  const modeLabel = { claude: "پاسخ هوش مصنوعی (Claude)", openai: "پاسخ هوش مصنوعی", extractive: "مرتبط‌ترین جمله‌ها از متن اسناد" }[a.mode] || "";
  const ans = esc(a.answer || "").replace(/\[([0-9۰-۹]+)\]/g, (m, n) => `<a href="#src-${normFa(n)}" class="cite">[${n}]</a>`);
  const sources = (a.results || []).map((r, i) => `
    <div class="source" id="src-${i + 1}">
      <div class="src-head">
        <span class="num">${num(i + 1)}</span>
        <b>${esc(r.title)}</b> <span class="badge">صفحه ${num(r.page)}</span>
        ${r.method === "ocr" ? `<span class="badge amber" title="این صفحه از روی تصویر خوانده شده؛ اعداد را با صفحه‌ی اصلی مقایسه کنید">OCR</span>` : ""}
        ${r.semantic && !r.keyword ? `<span class="badge gray" title="بر اساس معنا پیدا شد، نه واژه‌های یکسان">معنایی</span>` : ""}
      </div>
      <div class="src-text">${markTerms(r.text, a.terms)}</div>
      <div class="src-actions">
        ${r.kind === "pdf" || r.kind === "image" ? `<button class="btn" data-page="${r.doc_id}:${r.page}:${r.kind}">🖼 دیدن صفحه‌ی اصلی</button>` : ""}
        <a class="btn" href="/api/documents/${r.doc_id}/file#page=${r.page}" target="_blank">📄 باز کردن فایل</a>
      </div>
    </div>`).join("");
  return `<div class="card answer">
      <h3>${modeLabel}</h3>
      ${a.error ? `<p class="error">${esc(a.error)}</p>` : ""}
      <div class="answer-text">${ans}</div>
    </div>
    ${sources ? `<h4 class="sources-title">منابع (${num(a.results.length)})</h4><div class="list">${sources}</div>` : ""}`;
}

function bindAnswer(view) {
  view.querySelectorAll("[data-page]").forEach((b) => b.onclick = () => {
    const [id, page, kind] = b.dataset.page.split(":");
    showPage(Number(id), Number(page), kind);
  });
}

function showPage(docId, page, kind) {
  const dlg = $("#pageview");
  const img = $("#pv-img");
  const set = (p) => {
    page = p;
    $("#pv-title").textContent = `صفحه ${faDigits(p)}`;
    img.src = `/api/documents/${docId}/page/${p}.png`;
  };
  $("#pv-prev").onclick = () => page > 1 && set(page - 1);
  $("#pv-next").onclick = () => set(page + 1);
  $("#pv-prev").hidden = $("#pv-next").hidden = kind !== "pdf";
  $("#pv-close").onclick = () => dlg.close();
  img.onerror = () => { if (page > 1) set(page - 1); };
  set(page);
  dlg.showModal();
}

// فایل‌ها یکی‌یکی فرستاده می‌شوند تا با قطع شدن اینترنت فقط همان فایل از دست برود
function uploadOne(file, onProgress) {
  return new Promise((resolve) => {
    const fd = new FormData();
    fd.append("files", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 300) resolve({ ok: false, error: data.detail || "بارگذاری ناموفق بود" });
      else if (data.errors?.length) resolve({ ok: false, error: data.errors.join("، ") });
      else resolve({ ok: true, duplicate: !!data.added?.[0]?.duplicate });
    };
    xhr.onerror = () => resolve({ ok: false, error: "اتصال قطع شد", network: true });
    xhr.send(fd);
  });
}

async function upload(fileList, view) {
  const files = [...fileList];
  if (!files.length) return;
  const prog = $("#up-prog");
  const label = $("#drop b");
  prog.hidden = false;
  let ok = 0, dup = 0;
  const failed = [];
  for (let i = 0; i < files.length; i++) {
    if (label) label.textContent = `در حال بارگذاری فایل ${num(i + 1)} از ${num(files.length)}: ${files[i].name}`;
    const r = await uploadOne(files[i], (frac) => { prog.firstElementChild.style.width = `${(100 * (i + frac)) / files.length}%`; });
    if (r.ok) { ok++; if (r.duplicate) dup++; }
    else failed.push(`${files[i].name} (${r.error})`);
  }
  let msg = `${num(ok - dup)} فایل بارگذاری شد`;
  if (dup) msg += ` · ${num(dup)} فایل تکراری بود`;
  if (failed.length) msg += ` · ناموفق: ${failed.join("، ")} — فقط همین‌ها را دوباره بارگذاری کنید`;
  toast(msg);
  renderDocs(view);
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
  $("#side-date").textContent = d;
  if (!TABS.some(([k]) => k === state.tab)) state.tab = "dashboard";
  renderTabs();
  render();
})();
