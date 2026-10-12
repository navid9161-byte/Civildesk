// ───────────────────────── معاملات ارز دیجیتال (ژورنال به سبک Trade City Pro Journal) ─────────────────────────
// از app.js: $، api، esc، num، faDigits، toast، ico، META، state، render، MONTHS_FA
const cryptoState = { view: "dash", data: null, filter: { symbol: "", result: "" } };
const CR_VIEWS = [["dash", "📊 داشبورد"], ["journal", "📒 ژورنال معاملات"], ["transfers", "💸 واریز و برداشت"], ["calc", "🧮 ماشین‌حساب"], ["settings", "⚙️ تنظیمات"]];
const faUsd = new Intl.NumberFormat("fa-IR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// علامت منفی با «\u200e» تا در متن راست‌به‌چپ سمت چپ عدد بماند
const usd = (v) => (v === null || v === undefined ? "—" : `\u200e${v < 0 ? "−" : ""}${faUsd.format(Math.abs(v))}`);
const price = (v) => (v === null || v === undefined ? "—" : new Intl.NumberFormat("fa-IR", { maximumSignificantDigits: 8 }).format(v));
const pc = (v, d = 1) => (v === null || v === undefined ? "—" : `\u200e${v < 0 ? "−" : ""}${new Intl.NumberFormat("fa-IR", { maximumFractionDigits: d }).format(Math.abs(v))}٪`);
const rr = (v) => (v === null || v === undefined ? "—" : `\u200e${v < 0 ? "−" : ""}${new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 2 }).format(Math.abs(v))}R`);
const signCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const RESULT = { win: ["سود", "green"], loss: ["ضرر", "red"], be: ["سربه‌سر", "gray"] };

function jdate(t, withTime = true) {
  // «2026-10-12 14:30» ← «۲۰ مهر ۱۴۰۵، ۱۴:۳۰»
  if (!t) return "—";
  const d = new Date(t.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return esc(t);
  const fmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", { day: "numeric", month: "short", year: "numeric", ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}) });
  return fmt.format(d);
}
const dur = (m) => (m == null ? "—" : m < 60 ? `${num(m)} دقیقه` : m < 1440 ? `${num(Math.round(m / 6) / 10)} ساعت` : `${num(Math.round(m / 144) / 10)} روز`);

async function renderCrypto(view) {
  const d = await api("/api/crypto");
  cryptoState.data = d;
  view.innerHTML = `
    <div class="toolbar">
      <button class="btn primary" id="cr-add">+ معامله‌ی جدید</button>
      <label class="btn" id="cr-scan-l">📷 ثبت از روی عکس<input type="file" id="cr-scan" accept="image/*" hidden></label>
      <button class="btn" id="cr-tr">± واریز / برداشت</button>
    </div>
    <div class="chips">${CR_VIEWS.map(([k, l]) => `<button class="chip ${cryptoState.view === k ? "active" : ""}" data-crv="${k}">${l}</button>`).join("")}</div>
    <div id="cr-body"></div>`;
  const body = $("#cr-body");
  ({ dash: crDash, journal: crJournal, transfers: crTransfers, calc: crCalc, settings: crSettings })[cryptoState.view](body, d);
  view.querySelectorAll("[data-crv]").forEach((b) => b.onclick = () => { cryptoState.view = b.dataset.crv; renderCrypto(view); });
  $("#cr-add").onclick = () => tradeForm(null);
  $("#cr-tr").onclick = () => { cryptoState.view = "transfers"; renderCrypto(view).then(() => $("#tr-amount")?.focus()); };
  $("#cr-scan").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    toast("⏳ در حال خواندن عکس…");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const res = await fetch("/api/crypto/scan", { method: "POST", body: fd });
      const r = await res.json();
      if (!res.ok) throw new Error(r.detail || "خطای سرور");
      tradeForm(null, r);
    } catch (err) { toast(err.message); }
    e.target.value = "";
  };
}

// ───────── داشبورد ─────────
function crDash(el, d) {
  const s = d.stats;
  if (!d.trades.length && !d.transfers.length) {
    el.innerHTML = `<div class="card empty-state"><h3>هنوز معامله‌ای ثبت نشده</h3>
      <p>اول در «⚙️ تنظیمات» موجودی اولیه و تاریخ شروع را وارد کنید. بعد هر معامله را دستی یا با «📷 ثبت از روی عکس» اضافه کنید؛
      آمار، سود و زیان، منحنی موجودی و تحلیل‌ها خودکار ساخته می‌شوند.</p></div>`;
    return;
  }
  const tile = (v, l, cls = "") => `<div class="stat ${cls}"><div><div class="v">${v}</div><div class="l">${l}</div></div></div>`;
  const best = d.trades.find((t) => t.id === s.best), worst = d.trades.find((t) => t.id === s.worst);
  const table = (title, rows) => (rows.length ? `<div class="card fin-card"><h3>${title}</h3><div class="table-wrap"><table class="fin-table">
      <thead><tr><th></th><th>تعداد</th><th>نرخ برد</th><th>سود/زیان خالص ($)</th><th>میانگین R</th></tr></thead>
      <tbody>${rows.map((g) => `<tr><td>${esc(g.key)}</td><td>${num(g.n)}</td><td>${pc(g.win_rate, 0)}</td>
        <td class="${signCls(g.net)}">${usd(g.net)}</td><td>${rr(g.avg_r)}</td></tr>`).join("")}</tbody></table></div></div>` : "");
  el.innerHTML = `
    <div class="stats">
      ${tile(`$${usd(s.balance)}`, "موجودی فعلی")}
      ${tile(`<span class="${signCls(s.realized)}">$${usd(s.realized)}</span>`, `سود/زیان خالص${s.return_pct != null ? ` (${pc(s.return_pct)})` : ""}`, s.realized >= 0 ? "good" : "bad")}
      ${tile(pc(s.win_rate, 0), `نرخ برد (${num(s.wins)} سود / ${num(s.losses)} ضرر / ${num(s.breakeven)} سربه‌سر)`)}
      ${tile(s.profit_factor != null ? num(Math.round(s.profit_factor * 100) / 100) : "—", "ضریب سود (Profit Factor)")}
      ${tile(rr(s.avg_r), "میانگین R هر معامله")}
      ${tile(pc(s.max_drawdown), "بیشترین افت از قله")}
      ${tile(`$${usd(s.fees)}`, "جمع کارمزدها")}
      ${tile(`${num(s.closed)} / ${num(s.open)}`, "معامله‌ی بسته / باز")}
    </div>
    <div class="card fin-card"><h3>📈 منحنی موجودی</h3>${equityChart(d.equity)}</div>
    ${monthlyPnl(s.by_month)}
    <div class="card fin-card"><h3>🏅 نکته‌ها</h3><ul class="points">
      ${best ? `<li>بهترین معامله: ${esc(best.symbol)} ${best.side === "long" ? "لانگ" : "شورت"} (${jdate(best.opened_at, false)}) با <b class="pos">$${usd(best.net)}</b> (${rr(best.actual_r)})</li>` : ""}
      ${worst ? `<li>بدترین معامله: ${esc(worst.symbol)} ${worst.side === "long" ? "لانگ" : "شورت"} (${jdate(worst.opened_at, false)}) با <b class="neg">$${usd(worst.net)}</b> (${rr(worst.actual_r)})</li>` : ""}
      <li>میانگین سود معاملات برنده $${usd(s.avg_win)} و میانگین ضرر معاملات بازنده $${usd(s.avg_loss)}؛ امید ریاضی هر معامله $${usd(s.expectancy)}</li>
      <li>بیشترین برد پشت سر هم ${num(s.win_streak)} و بیشترین باخت پشت سر هم ${num(s.loss_streak)}؛ میانگین مدت معامله ${dur(s.avg_duration_min)}</li>
      <li>واریز $${usd(s.deposits)} · برداشت $${usd(s.withdrawals)} · موجودی اولیه $${usd(s.start_balance)}</li>
    </ul></div>
    <div class="cr-grid">
      ${table("💱 به تفکیک جفت‌ارز", s.by_symbol)}
      ${table("🧭 به تفکیک استراتژی", s.by_strategy)}
      ${table("↕️ لانگ / شورت", s.by_side)}
      ${table("📅 روز هفته‌ی ورود", s.by_weekday)}
      ${table("🚪 نوع خروج", s.by_exit)}
      ${table("🧠 حالت ذهنی هنگام ورود", s.by_mood)}
      ${table("⚠️ اشتباهات", s.by_mistake)}
      ${table("🏦 صرافی", s.by_exchange)}
    </div>`;
}

function equityChart(eq) {
  const pts = eq.filter((p) => p.at && p.at !== "0000-00-00 00:00");
  if (pts.length < 2) return `<p class="muted">برای نمودار دست‌کم یک معامله‌ی بسته یا انتقال لازم است.</p>`;
  const W = Math.round(Math.min(760, Math.max(300, window.innerWidth - 56))), H = 220, L = 52, R = 12, T = 12, B = 26;
  const vals = pts.map((p) => p.balance);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const X = (i) => L + ((W - L - R) * i) / (pts.length - 1);
  const Y = (v) => T + ((H - T - B) * (hi - v)) / (hi - lo);
  const ticks = [lo + pad, (lo + hi) / 2, hi - pad].map((v) => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="ch-grid"/>
    <text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end" class="ch-ax">${new Intl.NumberFormat("fa-IR", { maximumFractionDigits: 0 }).format(v)}</text>`).join("");
  const line = `<polyline class="ch-line" points="${pts.map((p, i) => `${X(i)},${Y(p.balance)}`).join(" ")}"/>`;
  const dots = pts.map((p, i) => `<g class="ch-pt" tabindex="0"><circle cx="${X(i)}" cy="${Y(p.balance)}" r="10" class="ch-hit"/>
    <circle cx="${X(i)}" cy="${Y(p.balance)}" r="${pts.length > 60 ? 2.5 : 4}" class="ch-dot ${p.kind === "transfer" ? "tr" : ""}"/>
    <title>${jdate(p.at)}\n${p.kind === "transfer" ? "انتقال" : p.kind === "exit" ? "خروج معامله" : "شروع"}${p.change != null ? `: ${usd(p.change)}$` : ""}\nموجودی: ${usd(p.balance)}$</title></g>`).join("");
  const ends = `<text x="${X(0)}" y="${H - 6}" class="ch-ax" text-anchor="start">${jdate(pts[0].at, false)}</text>
    <text x="${X(pts.length - 1)}" y="${H - 6}" class="ch-ax" text-anchor="end">${jdate(pts[pts.length - 1].at, false)}</text>`;
  return `<div class="ch-legend"><span><i class="lg-line"></i>موجودی حساب ($) پس از هر خروج یا انتقال</span><span><i class="lg-box tr-bg"></i>واریز/برداشت</span></div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="منحنی موجودی" direction="ltr">${ticks}${line}${dots}${ends}</svg>`;
}

function monthlyPnl(months) {
  if (!months.length) return "";
  const W = Math.round(Math.min(760, Math.max(300, window.innerWidth - 56))), H = 200, T = 14, B = 28;
  const max = Math.max(...months.map((m) => Math.abs(m.net)), 1);
  const zero = T + (H - T - B) / 2, half = (H - T - B) / 2;
  const band = (W - 16) / months.length, bw = Math.min(34, band * 0.6);
  const label = (k) => new Intl.DateTimeFormat("fa-IR-u-ca-persian", { month: "short", year: "2-digit" }).format(new Date(`${k}-15T12:00`));
  const bars = months.map((m, i) => {
    const cx = W - 8 - band * (i + 0.5), h = (half * Math.abs(m.net)) / max, y = m.net >= 0 ? zero - h : zero;
    return `<rect x="${cx - bw / 2}" y="${y}" width="${bw}" height="${Math.max(1, h)}" rx="3" class="${m.net >= 0 ? "mb-inc" : "mb-exp"}" tabindex="0">
      <title>${label(m.key)}: ${usd(m.net)}$ · ${num(m.n)} معامله · برد ${pc(m.win_rate, 0)}</title></rect>
      <text x="${cx}" y="${H - 8}" text-anchor="middle" class="ch-ax">${label(m.key)}</text>`;
  }).join("");
  return `<div class="card fin-card"><h3>📅 سود و زیان ماهانه ($)</h3>
    <div class="ch-legend"><span><i class="lg-box mb-inc-bg"></i>سود</span><span><i class="lg-box mb-exp-bg"></i>ضرر</span></div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="سود و زیان ماهانه"><line x1="8" x2="${W - 8}" y1="${zero}" y2="${zero}" class="ch-grid"/>${bars}</svg>
    <div class="table-wrap"><table class="fin-table"><thead><tr><th>ماه (میلادی)</th><th>معامله</th><th>نرخ برد</th><th>سود/زیان ($)</th><th>میانگین R</th></tr></thead>
    <tbody>${months.map((m) => `<tr><td>${label(m.key)} <small class="muted">${faDigits(m.key)}</small></td><td>${num(m.n)}</td><td>${pc(m.win_rate, 0)}</td><td class="${signCls(m.net)}">${usd(m.net)}</td><td>${rr(m.avg_r)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

// ───────── ژورنال ─────────
function crJournal(el, d) {
  const f = cryptoState.filter;
  const symbols = [...new Set(d.trades.map((t) => t.symbol))].sort();
  const rows = d.trades.filter((t) => (!f.symbol || t.symbol === f.symbol)
    && (!f.result || (f.result === "open" ? t.status === "open" : t.result === f.result)));
  el.innerHTML = `<div class="card">
    <div class="toolbar">
      <select id="jf-sym"><option value="">همه‌ی جفت‌ارزها</option>${symbols.map((x) => `<option ${x === f.symbol ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
      <select id="jf-res"><option value="">همه‌ی نتیجه‌ها</option>${[["win", "سود"], ["loss", "ضرر"], ["be", "سربه‌سر"], ["open", "باز"]].map(([k, l]) => `<option value="${k}" ${k === f.result ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="muted small">${num(rows.length)} معامله · روی هر ردیف بزنید تا جزئیات و ویرایش باز شود</span>
    </div>
    ${rows.length ? `<div class="table-wrap"><table class="fin-table cr-table">
      <thead><tr><th class="hm">#</th><th>ورود</th><th>جفت‌ارز</th><th>جهت</th><th class="hm">اهرم</th><th class="hm">قیمت ورود</th><th class="hm">میانگین خروج</th><th class="hm">ریسک حساب</th><th class="hm">R</th><th>سود/زیان ($)</th><th>نتیجه</th><th class="hm">موجودی</th></tr></thead>
      <tbody>${rows.map((t) => `<tr data-tid="${t.id}">
        <td class="hm">${num(t.id)}</td><td>${jdate(t.opened_at)}</td><td><b>${esc(t.symbol)}</b>${t.strategy ? `<br><small class="muted">${esc(t.strategy)}</small>` : ""}</td>
        <td><span class="badge ${t.side === "long" ? "green" : "red"}">${t.side === "long" ? "لانگ" : "شورت"}</span></td>
        <td class="hm">${num(t.leverage)}x</td><td class="hm">${price(t.entry_price)}</td><td class="hm">${price(t.exit_price)}</td><td class="hm">${pc(t.acc_risked, 2)}</td>
        <td class="hm">${rr(t.actual_r)}</td><td class="${signCls(t.net)}"><b>${t.net == null ? "—" : usd(t.net)}</b>${t.status === "open" && t.closed_pct ? `<br><small class="muted">${pc(t.closed_pct, 0)} بسته</small>` : ""}</td>
        <td>${t.status === "open" ? `<span class="badge amber">باز</span>` : `<span class="badge ${RESULT[t.result][1]}">${RESULT[t.result][0]}</span>`}</td>
        <td class="hm">${t.new_balance != null ? usd(t.new_balance) : "—"}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">معامله‌ای با این فیلتر نیست.</div>`}</div>`;
  $("#jf-sym").onchange = (e) => { f.symbol = e.target.value; crJournal(el, d); };
  $("#jf-res").onchange = (e) => { f.result = e.target.value; crJournal(el, d); };
  el.querySelectorAll("[data-tid]").forEach((r) => r.onclick = () => tradeForm(d.trades.find((t) => t.id === Number(r.dataset.tid))));
}

// ───────── فرم معامله ─────────
function tradeForm(t, scan = null) {
  const d = cryptoState.data, s = d.settings, sc = scan?.fields || {};
  const v = t || { opened_at: sc.opened_at, symbol: sc.symbol, side: sc.side || "long", entry_type: sc.entry_type || "futures",
    exchange: sc.exchange, entry_price: sc.entry_price, margin: sc.margin, leverage: sc.leverage || 1, sl: sc.sl, tp: sc.tp,
    entry_fee: s.fees[0], exits: sc.exit_price ? [{ price: sc.exit_price, pct: 100, type: "manual" }] : [], shots: scan ? [scan.shot] : [], checklist: [] };
  const dl = (id, items) => `<datalist id="${id}">${(items || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist>`;
  const local = (x) => (x ? x.replace(" ", "T") : "");
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const inp = (name, label, val, attrs = "") => `<label>${label}<input name="${name}" value="${val ?? ""}" ${attrs}></label>`;
  const dlg = $("#ctrade");
  $("#ct-title").textContent = t ? `معامله‌ی #${faDigits(t.id)} · ${t.symbol}` : scan ? "معامله‌ی جدید از روی عکس" : "معامله‌ی جدید";
  $("#ct-body").innerHTML = `
    ${scan ? `<div class="notice">📷 اطلاعات خوانده‌شده از عکس پر شد؛ لطفاً با عکس مقایسه و جاهای خالی را کامل کنید.
      ${sc.pnl != null ? `سود/زیان نوشته‌شده در عکس: <b>${usd(sc.pnl)}$</b>` : ""}
      <details><summary>متن خوانده‌شده</summary><pre class="ocr-text">${esc(scan.text || "(متنی خوانده نشد)")}</pre></details></div>` : ""}
    <h4>ورود</h4>
    <div class="form-grid">
      ${inp("opened_at", "زمان ورود (میلادی)", local(v.opened_at) || now, 'type="datetime-local" required')}
      ${inp("symbol", "جفت‌ارز", v.symbol, 'list="dl-pairs" required placeholder="BTCUSDT"')}
      <label>جهت<select name="side"><option value="long" ${v.side !== "short" ? "selected" : ""}>لانگ (خرید)</option><option value="short" ${v.side === "short" ? "selected" : ""}>شورت (فروش)</option></select></label>
      <label>نوع<select name="entry_type"><option value="futures" ${v.entry_type !== "spot" ? "selected" : ""}>فیوچرز</option><option value="spot" ${v.entry_type === "spot" ? "selected" : ""}>اسپات</option></select></label>
      ${inp("exchange", "صرافی", v.exchange, 'list="dl-ex"')}
      ${inp("strategy", "استراتژی", v.strategy, 'list="dl-st"')}
      ${inp("entry_price", "قیمت ورود", v.entry_price, 'inputmode="decimal" required')}
      ${inp("margin", "مارجین ($)", v.margin, 'inputmode="decimal" required')}
      ${inp("leverage", "اهرم", v.leverage, 'inputmode="decimal"')}
      <label>نوع سفارش<select name="order_type">${["", "Market", "Limit"].map((x) => `<option ${x === (v.order_type || "") ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      ${inp("time_frame", "تایم‌فریم", v.time_frame, 'list="dl-tf"')}
      ${inp("entry_fee", "کارمزد ورود (٪)", v.entry_fee, 'inputmode="decimal" list="dl-fee"')}
      ${inp("entry_mood", "حالت ذهنی ورود", v.entry_mood, 'list="dl-mood"')}
      ${inp("tag", "برچسب", v.tag)}
    </div>
    <h4>مدیریت ریسک</h4>
    <div class="form-grid">
      ${inp("sl", "حد ضرر", v.sl, 'inputmode="decimal"')}
      ${inp("tp", "حد سود", v.tp, 'inputmode="decimal"')}
    </div>
    <div class="cr-live" id="ct-live"></div>
    ${s.check_items.length ? `<h4>چک‌لیست ورود</h4><div class="cr-checks">${s.check_items.map((c) => `<label class="chk"><input type="checkbox" name="checklist" value="${esc(c)}" ${(v.checklist || []).includes(c) ? "checked" : ""}> ${esc(c)}</label>`).join("")}</div>` : ""}
    <h4>خروج‌ها <small class="muted">(خروج پله‌ای: هر بخش با درصدی از پوزیشن)</small></h4>
    <div id="ct-exits"></div>
    <button type="button" class="btn" id="ct-addexit">+ خروج</button>
    <h4>بعد از معامله</h4>
    <div class="form-grid">
      <label>امتیاز به اجرای معامله<select name="rating">${["", 1, 2, 3, 4, 5].map((x) => `<option value="${x}" ${String(x) === String(v.rating || "") ? "selected" : ""}>${x ? "★".repeat(x) : ""}</option>`).join("")}</select></label>
      ${inp("mistake", "اشتباه‌ها (با «،» جدا کنید)", v.mistake, 'list="dl-mis"')}
      ${inp("tracking", "پیگیری", v.tracking, 'list="dl-trk"')}
      <label class="wide">یادداشت<textarea name="memo">${esc(v.memo || "")}</textarea></label>
    </div>
    <h4>عکس‌ها</h4>
    <div class="cr-shots" id="ct-shots"></div>
    <label class="btn">+ افزودن عکس<input type="file" id="ct-shot" accept="image/*" hidden></label>
    ${dl("dl-pairs", s.pairs)}${dl("dl-ex", s.exchanges)}${dl("dl-st", s.strategies)}${dl("dl-tf", s.time_frames)}${dl("dl-fee", s.fees)}
    ${dl("dl-mood", s.mental_states)}${dl("dl-mis", s.mistakes)}${dl("dl-trk", s.trackings)}`;
  $("#ct-error").textContent = "";
  $("#ct-delete").style.display = t ? "" : "none";
  let exits = (v.exits || []).map((e) => ({ ...e }));
  let shots = [...(v.shots || [])];
  const form = $("#ct-form");
  const val = (n) => form.querySelector(`[name="${n}"]`)?.value;
  const fnum = (n) => { const x = parseFloat(String(val(n) || "").replace(/[٬,]/g, "").replace(/[۰-۹]/g, (c) => "۰۱۲۳۴۵۶۷۸۹".indexOf(c))); return Number.isFinite(x) ? x : null; };
  const drawExits = () => {
    $("#ct-exits").innerHTML = exits.map((e, i) => `<div class="cr-exit" data-i="${i}">
      <label>قیمت خروج<input data-k="price" value="${e.price ?? ""}" inputmode="decimal"></label>
      <label>٪ پوزیشن<input data-k="pct" value="${e.pct ?? 100}" inputmode="decimal"></label>
      <label>نوع<select data-k="type">${Object.entries(d.exit_types).map(([k, l]) => `<option value="${k}" ${k === e.type ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>کارمزد (٪)<input data-k="fee" value="${e.fee ?? ""}" inputmode="decimal" placeholder="${val("entry_fee") || ""}"></label>
      <label>زمان<input data-k="at" type="datetime-local" value="${local(e.at)}"></label>
      <label>حالت ذهنی<input data-k="mood" value="${esc(e.mood || "")}" list="dl-mood"></label>
      <button type="button" class="btn danger" data-del="${i}" title="حذف این خروج">✕</button></div>`).join("") || `<p class="muted">هنوز خروجی ثبت نشده (معامله باز است).</p>`;
    $("#ct-exits").querySelectorAll("[data-k]").forEach((x) => x.oninput = () => {
      exits[Number(x.closest(".cr-exit").dataset.i)][x.dataset.k] = x.value;
      live();
    });
    $("#ct-exits").querySelectorAll("[data-del]").forEach((b) => b.onclick = () => { exits.splice(Number(b.dataset.del), 1); drawExits(); live(); });
  };
  const drawShots = () => {
    $("#ct-shots").innerHTML = shots.map((n, i) => `<div class="cr-shot"><a href="/api/crypto/shots/${n}" target="_blank"><img src="/api/crypto/shots/${n}" alt="عکس معامله"></a>
      <button type="button" class="btn" data-sdel="${i}" title="برداشتن">✕</button></div>`).join("");
    $("#ct-shots").querySelectorAll("[data-sdel]").forEach((b) => b.onclick = () => { shots.splice(Number(b.dataset.sdel), 1); drawShots(); });
  };
  const live = () => {
    // محاسبه‌ی زنده مثل ستون‌های اکسل
    const entry = fnum("entry_price"), margin = fnum("margin"), lev = fnum("leverage") || 1, sl = fnum("sl"), tp = fnum("tp"), fee = (fnum("entry_fee") || 0) / 100;
    const bal = d.stats.balance;
    if (!entry || !margin) { $("#ct-live").innerHTML = ""; return; }
    const value = margin * (val("entry_type") === "spot" ? 1 : lev), qty = value / entry;
    const slp = sl ? Math.abs(entry - sl) / entry : null;
    const risk = slp ? value * slp + value * fee : null;
    const side = val("side") === "short" ? -1 : 1;
    let net = null;
    const ex = exits.filter((e) => parseFloat(e.price));
    if (ex.length) {
      net = ex.reduce((a, e) => {
        const part = (qty * (parseFloat(e.pct) || 100)) / 100, p = parseFloat(e.price);
        const fo = (e.fee !== "" && e.fee != null ? parseFloat(e.fee) : (fnum("entry_fee") || 0)) / 100;
        return a + part * (p - entry) * side - part * p * fo - (value * fee * (parseFloat(e.pct) || 100)) / 100;
      }, 0);
    }
    $("#ct-live").innerHTML = `<span>ارزش پوزیشن <b>$${usd(value)}</b></span><span>مقدار <b>${price(qty)}</b></span>
      ${slp ? `<span>فاصله‌ی حد ضرر <b>${pc(100 * slp, 2)}</b></span><span>ریسک <b>$${usd(risk)}</b>${bal > 0 ? ` (<b>${pc((100 * risk) / bal, 2)}</b> حساب)` : ""}</span>` : ""}
      ${sl && tp ? `<span>R:R برنامه‌ریزی‌شده <b>${rr(Math.abs(tp - entry) / Math.abs(entry - sl))}</b></span>` : ""}
      ${bal > 0 ? `<span>سهم از حساب <b>${pc((100 * margin) / bal)}</b></span>` : ""}
      ${net != null ? `<span>سود/زیان خالص <b class="${signCls(net)}">$${usd(net)}</b>${risk ? ` (${rr(net / risk)})` : ""}</span>` : ""}`;
  };
  drawExits();
  drawShots();
  live();
  form.oninput = (e) => { if (!e.target.closest(".cr-exit")) live(); };
  $("#ct-addexit").onclick = () => {
    const done = exits.reduce((a, e) => a + (parseFloat(e.pct) || 0), 0);
    exits.push({ price: "", pct: Math.max(0, 100 - done) || 100, type: "manual", at: now });
    drawExits();
  };
  $("#ct-shot").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    const res = await fetch("/api/crypto/shots", { method: "POST", body: fd });
    const r = await res.json();
    if (res.ok) { shots.push(r.shot); drawShots(); } else toast(r.detail || "خطا");
    e.target.value = "";
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const n of ["opened_at", "symbol", "side", "entry_type", "exchange", "strategy", "entry_price", "margin", "leverage", "order_type",
      "time_frame", "entry_fee", "entry_mood", "tag", "sl", "tp", "rating", "mistake", "tracking", "memo"]) body[n] = val(n);
    body.checklist = [...form.querySelectorAll('[name="checklist"]:checked')].map((x) => x.value);
    body.exits = exits.filter((x) => String(x.price).trim());
    body.shots = shots;
    try {
      await api(t ? `/api/crypto/trades/${t.id}` : "/api/crypto/trades", { method: t ? "PATCH" : "POST", body });
      dlg.close();
      toast("ذخیره شد ✔");
      render();
    } catch (err) { $("#ct-error").textContent = err.message; }
  };
  $("#ct-cancel").onclick = () => dlg.close();
  $("#ct-delete").onclick = async () => {
    if (!t || !confirm("این معامله حذف شود؟")) return;
    await api(`/api/crypto/trades/${t.id}`, { method: "DELETE" });
    dlg.close();
    toast("حذف شد");
    render();
  };
  dlg.showModal();
}

// ───────── واریز و برداشت ─────────
function crTransfers(el, d) {
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  el.innerHTML = `<div class="card">
    <h3>± واریز / برداشت جدید</h3>
    <form class="form-grid" id="tr-form">
      <label>زمان<input name="at" type="datetime-local" value="${now}"></label>
      <label>مبلغ ($) — برداشت را منفی بنویسید<input name="amount" id="tr-amount" inputmode="decimal" required placeholder="مثلاً 200 یا -50"></label>
      <label>صرافی<input name="exchange" list="tr-ex"></label>
      <label>توضیح<input name="note"></label>
      <datalist id="tr-ex">${d.settings.exchanges.map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
      <div class="modal-actions"><button class="btn primary">ثبت</button></div>
    </form></div>
    <div class="card">${d.transfers.length ? `<div class="table-wrap"><table class="fin-table"><thead><tr><th>زمان</th><th>مبلغ ($)</th><th>صرافی</th><th>توضیح</th><th></th></tr></thead>
      <tbody>${d.transfers.map((x) => `<tr><td>${jdate(x.at)}</td><td class="${signCls(x.amount)}"><b>${usd(x.amount)}</b> ${x.amount > 0 ? "واریز" : "برداشت"}</td>
        <td>${esc(x.exchange || "")}</td><td>${esc(x.note || "")}</td><td><button class="btn danger" data-trdel="${x.id}">✕</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">انتقالی ثبت نشده است.</div>`}</div>`;
  $("#tr-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try { await api("/api/crypto/transfers", { method: "POST", body }); toast("ثبت شد ✔"); render(); } catch (err) { toast(err.message); }
  };
  el.querySelectorAll("[data-trdel]").forEach((b) => b.onclick = async () => {
    if (!confirm("حذف شود؟")) return;
    await api(`/api/crypto/transfers/${b.dataset.trdel}`, { method: "DELETE" });
    render();
  });
}

// ───────── ماشین‌حساب حجم پوزیشن (برگه‌ی Calculator اکسل) ─────────
function crCalc(el, d) {
  const s = d.settings;
  el.innerHTML = `<div class="card"><h3>🧮 ماشین‌حساب حجم پوزیشن</h3>
    <p class="muted small">با ریسک ثابت هر معامله، حجم و مارجین مناسب را حساب می‌کند (مثل برگه‌ی Calculator فایل اکسل).</p>
    <form class="form-grid" id="calc">
      <label>موجودی ($)<input name="bal" value="${Math.round(d.stats.balance * 100) / 100}" inputmode="decimal"></label>
      <label>ریسک هر معامله (٪)<input name="risk" value="${s.risk_per_trade}" inputmode="decimal"></label>
      <label>قیمت ورود<input name="entry" inputmode="decimal"></label>
      <label>حد ضرر<input name="sl" inputmode="decimal"></label>
      <label>اهرم<input name="lev" value="10" inputmode="decimal"></label>
      <label>کارمزد متوسط (٪)<input name="fee" value="${s.fees[0] ?? 0.05}" inputmode="decimal"></label>
      <label>ریسک به ریوارد (R)<input name="rr" value="2" inputmode="decimal"></label>
    </form>
    <div id="calc-out" class="calc-out"></div></div>`;
  const calc = () => {
    const g = (n) => parseFloat(String($(`#calc [name="${n}"]`).value).replace(/[٬,]/g, "")) || 0;
    const bal = g("bal"), risk = (bal * g("risk")) / 100, entry = g("entry"), sl = g("sl"), lev = g("lev") || 1, fee = g("fee") / 100, R = g("rr");
    if (!entry || !sl || entry === sl) { $("#calc-out").innerHTML = `<p class="muted">قیمت ورود و حد ضرر را وارد کنید.</p>`; return; }
    const long = sl < entry, slp = Math.abs(entry - sl) / entry;
    const value = risk / (slp + 2 * fee), qty = value / entry, margin = value / lev;  // ضرر در حد ضرر + کارمزد ورود و خروج = ریسک
    const tp = long ? entry + Math.abs(entry - sl) * R : entry - Math.abs(entry - sl) * R;
    const profit = qty * Math.abs(tp - entry) - value * fee - qty * tp * fee;
    const row = (l, x) => `<div><span>${l}</span><b>${x}</b></div>`;
    $("#calc-out").innerHTML = row("جهت", long ? "لانگ" : "شورت") + row("فاصله‌ی حد ضرر", pc(100 * slp, 2)) + row("ریسک (ضرر)", `$${usd(risk)}`)
      + row("ارزش سفارش", `$${usd(value)}`) + row("مقدار (QTY)", price(qty)) + row("مارجین لازم", `$${usd(margin)}`)
      + row("سهم مارجین از حساب", pc((100 * margin) / (bal || 1))) + row(`حد سود در ${faDigits(R)}R`, price(tp))
      + row("سود تقریبی (منهای کارمزد)", `<span class="pos">$${usd(profit)}</span>`) + row("کارمزد ورود + حد ضرر", `$${usd(value * fee + qty * sl * fee)}`)
      + (margin > bal ? `<p class="badge red">مارجین لازم از موجودی بیشتر است؛ اهرم را بالا ببرید یا ریسک را کم کنید.</p>` : "");
  };
  $("#calc").oninput = calc;
  calc();
}

// ───────── تنظیمات (برگه‌ی Variables اکسل) ─────────
function crSettings(el, d) {
  const s = d.settings;
  const lists = [["pairs", "جفت‌ارزها"], ["exchanges", "صرافی‌ها"], ["fees", "کارمزدها (٪)"], ["strategies", "استراتژی‌ها"], ["mental_states", "حالت‌های ذهنی"],
    ["mistakes", "اشتباهات"], ["trackings", "پیگیری‌ها"], ["check_items", "موارد چک‌لیست ورود"], ["time_frames", "تایم‌فریم‌ها"]];
  el.innerHTML = `<div class="card"><h3>⚙️ تنظیمات حساب</h3>
    <form class="form-grid" id="cs-form">
      <label>موجودی اولیه ($)<input name="start_balance" value="${s.start_balance}" inputmode="decimal"></label>
      <label>تاریخ شروع<input name="start_date" type="datetime-local" value="${(s.start_date || "").replace(" ", "T")}"></label>
      <label>ریسک هر معامله (٪)<input name="risk_per_trade" value="${s.risk_per_trade}" inputmode="decimal"></label>
      ${lists.map(([k, l]) => `<label class="wide">${l} <small class="muted">(هر مورد در یک سطر)</small><textarea name="${k}" rows="3">${esc((s[k] || []).join("\n"))}</textarea></label>`).join("")}
      <div class="modal-actions"><button class="btn primary">ذخیره</button></div>
    </form></div>`;
  $("#cs-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try { await api("/api/crypto/settings", { method: "PUT", body }); toast("ذخیره شد ✔"); render(); } catch (err) { toast(err.message); }
  };
}
