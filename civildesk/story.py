"""روایت پروژه: متنی خوانا و پیوسته از روی اسناد بایگانی (بدون هوش مصنوعی).

ساختار: در یک نگاه ← معرفی پروژه ← روند پروژه (ماه به ماه، با جمله‌های به‌هم‌پیوسته) ←
وضعیت مالی ← وضعیت امروز (پیشرفت زمانی در برابر پیشرفت کار، مهلت‌ها، ضمانت‌نامه‌ها).
هر جمله به سند منبعش پیوند دارد.
"""
from __future__ import annotations

import re
from typing import Any

from . import jalali

CAT_PLURAL = {
    "letter": "نامه", "minutes": "صورتجلسه", "invoice": "صورت‌وضعیت", "contract": "قرارداد",
    "amendment": "الحاقیه", "order": "دستور کار", "guarantee": "ضمانت‌نامه", "financial": "سند مالی",
    "report": "گزارش", "drawing": "نقشه", "photo": "عکس", "other": "سند دیگر",
}
ROLE_FA = {"supervisor": "ناظر / مشاور", "contractor": "پیمانکار", "employer": "کارفرما",
           "designer": "طراح", "site_manager": "رئیس کارگاه"}


# ───────────────────────── قالب‌بندی ─────────────────────────


def fa_money(v: int | float | None) -> str:
    """مبلغ خوانا: «۴۵٫۲۵ میلیارد ریال»."""
    if not v:
        return ""
    for div, word in ((1e12, "هزار میلیارد"), (1e9, "میلیارد"), (1e6, "میلیون")):
        if abs(v) >= div:
            x = f"{v / div:.2f}".rstrip("0").rstrip(".").replace(".", "٫")
            return f"{x} {word} ریال"
    return f"{int(v):,} ریال"


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f}".rstrip("0").rstrip(".").replace(".", "٫") + "٪"


def join_fa(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return "، ".join(items[:-1]) + " و " + items[-1]


def quote(s: str | None, limit: int = 140) -> str:
    s = " ".join((s or "").split()).strip(" .،:؛")
    if not s:
        return ""
    return f"«{s if len(s) <= limit else s[:limit].rsplit(' ', 1)[0] + '…'}»"


def _ymd(jdate: str) -> tuple[int, int, int]:
    y, m, d = (int(x) for x in jdate.split("/"))
    return y, m, d


def day_month(jdate: str) -> str:
    _, m, d = _ymd(jdate)
    return f"{d} {jalali.MONTHS_FA[m - 1]}"


def _v(info: dict, key: str) -> Any:
    return (info.get(key) or {}).get("value")


# ───────────────────────── جمله‌ی هر سند ─────────────────────────


def event_core(d: dict, contract_amount: int | None = None) -> str:
    """هسته‌ی جمله‌ی روایی یک سند (بدون قید زمان)."""
    info, cat = d["info"], d.get("category") or "other"
    subj = quote(d.get("subject"))
    title = quote(d["title"], 80)
    amount = _v(info, "amount")
    points = _v(info, "points") or ([_v(info, "gist")] if _v(info, "gist") else [])
    no = d.get("doc_no")

    def said(prefix: str = "در آن آمده است") -> str:
        if not points:
            return ""
        first = quote(points[0], 220)
        more = f" و {quote(points[1], 160)}" if len(points) > 1 and len(points[0]) < 120 else ""
        return f"؛ {prefix}: {first}{more}"

    if cat == "letter":
        to = _v(info, "to")
        s = "نامه‌ای" + (f" به شماره‌ی {no}" if no else "") + (f" خطاب به {to}" if to else "")
        s += (f" با موضوع {subj}" if subj else (f" با عنوان {title}" if not (no or to) else "")) + " نوشته شد"
        s += said("در این نامه آمده است")
        dl = info.get("deadline")
        if dl:
            s += f" و برای انجام آن مهلت {dl.get('date') and 'تا ' + dl['date'] or dl['value']} تعیین شد"
        return s + "."
    if cat == "order":
        return f"دستور کار / ابلاغیه‌ای{' به شماره‌ی ' + no if no else ''} با موضوع {subj or title} صادر شد{said()}."
    if cat == "minutes":
        items = [i.rstrip(".؛ ") for i in (_v(info, "items") or [])][:4]
        s = "جلسه‌ای" + (f" با موضوع {subj}" if subj else "") + " برگزار شد"
        if items:
            s += " و در آن مقرر شد: " + "؛ ".join(items)
        else:
            s += said("در صورتجلسه‌ی آن آمده است")
        return s + "."
    if cat == "invoice":
        kind = _v(info, "invoice_kind")
        n = _v(info, "invoice_no")
        s = "صورت‌وضعیت" + (f" {kind}" if kind else "") + (f" شماره‌ی {n}" if n else f" {title}")
        period = _v(info, "period")
        s += (f" برای دوره‌ی {period}" if period else "") + " تهیه شد"
        parts = []
        if _v(info, "work_period"):
            parts.append(f"کارکرد این دوره {fa_money(_v(info, 'work_period'))}")
        if _v(info, "work_total"):
            t = f"جمع کارکرد تا این مرحله {fa_money(_v(info, 'work_total'))}"
            if contract_amount:
                t += f" (حدود {pct(_v(info, 'work_total'), contract_amount)} مبلغ قرارداد)"
            parts.append(t)
        if _v(info, "adjustment"):
            parts.append(f"تعدیل {fa_money(_v(info, 'adjustment'))}")
        if _v(info, "deductions"):
            parts.append(f"کسورات {fa_money(_v(info, 'deductions'))}")
        if _v(info, "net"):
            parts.append(f"خالص قابل پرداخت {fa_money(_v(info, 'net'))}")
        if _v(info, "progress"):
            parts.append(f"پیشرفت فیزیکی {_v(info, 'progress'):g}٪")
        if parts:
            s += "؛ " + join_fa(parts) + " است"
        elif amount:
            s += f"؛ مبلغ آن {fa_money(amount)} خوانده شد"
        return s + "."
    if cat == "amendment":
        du = _v(info, "duration")
        s = f"الحاقیه / تمدیدی با موضوع {subj or title} به قرارداد اضافه شد"
        extra = [f"مبلغ آن {fa_money(amount)}" if amount else "", f"مدت آن {du}" if du else ""]
        if any(extra):
            s += "؛ " + join_fa(extra) + " است"
        return s + said() + "."
    if cat == "guarantee":
        vu = _v(info, "valid_until")
        return (f"ضمانت‌نامه‌ی {subj or title}" + (f" به مبلغ {fa_money(amount)}" if amount else "")
                + (f" با اعتبار تا {vu}" if vu else "") + " صادر شد.")
    if cat == "contract":
        return f"قرارداد{' شماره‌ی ' + no if no else ''} {subj or title}" + (f" به مبلغ {fa_money(amount)}" if amount else "") + f" منعقد شد{said()}."
    if cat == "report":
        return f"گزارش {subj or title} تهیه شد{said('در این گزارش آمده است')}."
    if cat == "financial":
        return f"سند مالی {title}" + (f" به مبلغ {fa_money(amount)}" if amount else "") + " ثبت شد."
    if cat in ("drawing", "photo"):
        return f"{CAT_PLURAL[cat]} {title} به بایگانی اضافه شد."
    return f"سند {subj or title} بایگانی شد{said()}."


def _connector(prev: str | None, cur: str, i: int) -> str:
    """قید زمان جمله با توجه به جمله‌ی قبلی، تا متن پیوسته خوانده شود."""
    if prev == cur:
        return "در همان روز"
    if prev:
        gap = jalali.days_between(prev, cur)
        if gap == 1:
            return "فردای آن روز"
        if gap in (2, 3):
            return f"{'دو' if gap == 2 else 'سه'} روز بعد"
    lead = "در" if i == 0 else ("سپس در", "پس از آن، در", "و در")[(i - 1) % 3]
    return f"{lead} {day_month(cur)}"


# ───────────────────────── روایت کامل ─────────────────────────


def build(project: dict, ready: list[dict], contract: dict | None, facts: dict[str, Any],
          deadlines: list[dict], guarantees: list[dict], invoices: list[dict], today: str,
          src) -> list[dict[str, Any]]:
    """بندهای روایت. src(d) پیوند منبع یک سند را می‌سازد."""
    sections: list[dict[str, Any]] = []
    name = project["name"]
    def get(k: str) -> Any:  # از قرارداد، وگرنه از مشخصات پروژه
        return facts.get(k) or project.get(k)

    amount = get("contract_amount")
    start, end = get("start_date"), get("end_date")
    subject = facts.get("subject")
    employer, contractor = get("employer"), get("contractor")
    consultant = get("consultant")

    last_inv = invoices[-1] if invoices else None
    work_total = (last_inv or {}).get("work_total")
    progress = (last_inv or {}).get("progress") or project.get("progress") or None

    # ── در یک نگاه
    glance = []
    if start and end:
        try:
            total_days = jalali.days_between(start, end)
            elapsed = jalali.days_between(start, today)
            if total_days > 0:
                time_pct = 100 * elapsed / total_days
                if elapsed < 0:
                    glance.append(f"کار هنوز شروع نشده و {-elapsed} روز تا آغاز آن مانده است.")
                elif elapsed <= total_days:
                    glance.append(f"{pct(elapsed, total_days)} از مدت قرارداد گذشته و {total_days - elapsed} روز باقی مانده است.")
                else:
                    glance.append(f"مدت قرارداد {elapsed - total_days} روز پیش به پایان رسیده است.")
                done = progress if progress else (100 * work_total / amount if work_total and amount else None)
                if done is not None and 0 <= elapsed:
                    gap = min(time_pct, 100) - done
                    what = "پیشرفت فیزیکی" if progress else "پیشرفت مالی (کارکرد)"
                    if gap > 10:
                        glance.append(f"{what} {done:.0f}٪ است، یعنی حدود {gap:.0f} درصد از برنامه‌ی زمانی عقب‌تر.")
                    elif gap < -10:
                        glance.append(f"{what} {done:.0f}٪ است و کار از برنامه‌ی زمانی جلوتر است.")
                    else:
                        glance.append(f"{what} {done:.0f}٪ است و با برنامه‌ی زمانی هماهنگ است.")
        except ValueError:
            pass
    late = [x for x in deadlines if x["late"]]
    coming = [x for x in deadlines if not x["late"]]
    alerts = []
    if coming:
        alerts.append(f"{len(coming)} مهلت پیش رو")
    if late:
        alerts.append(f"{len(late)} مهلت گذشته")
    exp = [g for g in guarantees if g["state"] == "expired"]
    soon = [g for g in guarantees if g["state"] == "soon"]
    if exp:
        alerts.append(f"{len(exp)} ضمانت‌نامه‌ی منقضی")
    if soon:
        alerts.append(f"{len(soon)} ضمانت‌نامه‌ی نزدیک سررسید")
    if alerts:
        glance.append("نیازمند توجه: " + join_fa(alerts) + ".")
    if glance:
        sections.append({"title": "در یک نگاه", "kind": "glance", "items": [{"text": " ".join(glance)}]})

    # ── معرفی
    intro = []
    lead = f"پروژه‌ی «{name}»"
    if project.get("location"):
        lead += f" در {project['location']}"
    lead += (f" با موضوع {quote(subject, 200)} تعریف شده است." if subject else " در این بایگانی پیگیری می‌شود.")
    intro.append({"text": lead, **(src(contract) if contract else {})})
    if contract or employer or contractor:
        s = "قرارداد این کار"
        if facts.get("contract_no") or project.get("code"):
            s += f" به شماره‌ی {facts.get('contract_no') or project.get('code')}"
        if facts.get("contract_date"):
            s += f" در تاریخ {facts['contract_date']}"
        parties = []
        if employer:
            parties.append(f"{employer} به‌عنوان کارفرما")
        if contractor:
            parties.append(f"{contractor} به‌عنوان پیمانکار")
        s += (" میان " + " و ".join(parties) if parties else "") + " منعقد شده است"
        if consultant:
            s += f" و {consultant} مشاور (دستگاه نظارت) آن است"
        intro.append({"text": s + ".", **(src(contract) if contract else {})})
    terms = []
    if amount:
        terms.append(f"مبلغ قرارداد {fa_money(amount)} ({amount:,} ریال) است")
    if facts.get("duration"):
        terms.append(f"مدت اجرای آن {facts['duration']} تعیین شده")
    if terms:
        intro.append({"text": join_fa(terms) + ".", **(src(contract) if contract else {})})
    if start and end:
        intro.append({"text": f"کار از {start} آغاز شده و باید تا {end} به پایان برسد."})
    elif start or end:
        intro.append({"text": f"تاریخ شروع کار {start}." if start else f"کار باید تا {end} به پایان برسد."})
    if project.get("role") in ROLE_FA:
        intro.append({"text": f"نقش شما در این پروژه «{ROLE_FA[project['role']]}» است."})
    # جمله‌های مهم قرارداد که در معرفی گفته نشده‌اند (طرفین، مبلغ و مدت تکراری است)
    cpoints = [p for p in (_v(contract["info"], "points") or []) if not re.search(r"نامیده|مبلغ\s*کل|مدت\s*قرارداد|مبلغ\s*قرارداد", p)] if contract else []
    if cpoints:
        intro.append({"text": "در متن قرارداد همچنین آمده است: " + join_fa([quote(p, 220) for p in cpoints[:2]]) + ".", **src(contract)})
    sections.append({"title": "معرفی پروژه", "kind": "intro", "items": intro})

    # ── روند پروژه، ماه به ماه
    dated = sorted((d for d in ready if d.get("doc_date") and d is not contract), key=lambda d: (d["doc_date"], d["id"]))
    seen, month, prev, i = set(), None, None, 0
    for d in dated:
        key = (d["doc_date"], d.get("category"), d.get("doc_no") or d.get("subject") or d["id"])
        if key in seen:  # نسخه‌ی دیگری از همان سند (مثلاً اسکن و عکس)
            continue
        seen.add(key)
        y, m, _ = _ymd(d["doc_date"])
        if (y, m) != month:
            month, prev, i = (y, m), None, 0
            sections.append({"title": f"{jalali.MONTHS_FA[m - 1]} {y}", "kind": "month", "items": []})
        conn = _connector(prev, d["doc_date"], i)
        sections[-1]["items"].append({"text": f"{conn}، {event_core(d, amount)}", **src(d)})
        prev, i = d["doc_date"], i + 1

    # ── وضعیت مالی
    money = []
    if invoices:
        money.append({"text": f"تاکنون {len(invoices)} صورت‌وضعیت در بایگانی این پروژه ثبت شده است."})
        if work_total:
            which = "، ".join(x for x in (f"شماره‌ی {last_inv['no']}" if last_inv.get("no") else "",
                                          f"دوره‌ی {last_inv['period']}" if last_inv.get("period") else "") if x)
            s = "بر اساس آخرین صورت‌وضعیت" + (f" ({which})" if which else "")
            s += f"، جمع کارکرد به {fa_money(work_total)} رسیده"
            if amount:
                s += f" که حدود {pct(work_total, amount)} مبلغ قرارداد است"
                if work_total > amount:
                    s += "؛ یعنی کارکرد از مبلغ اولیه‌ی قرارداد گذشته و باید الحاقیه یا افزایش مبلغ پیگیری شود"
            money.append({"text": s + ".", **{k: last_inv[k] for k in ("doc_id", "doc_title", "page", "kind")}})
        nets = [x["net"] for x in invoices if x.get("net")]
        if len(nets) >= 2:
            money.append({"text": f"جمع خالص قابل پرداخت {len(nets)} صورت‌وضعیتی که مبلغشان خوانده شد {fa_money(sum(nets))} است."})
    amend = [d for d in ready if d.get("category") == "amendment"]
    if amend:
        total = sum(_v(d["info"], "amount") or 0 for d in amend)
        money.append({"text": f"{len(amend)} الحاقیه / تمدید به قرارداد اضافه شده" + (f" که جمع مبالغ خوانده‌شده‌ی آن‌ها {fa_money(total)} است." if total else ".")})
    if guarantees:
        s = f"{len(guarantees)} ضمانت‌نامه / بیمه‌نامه در بایگانی هست"
        if exp:
            s += f"؛ {len(exp)} مورد منقضی شده"
        if soon:
            s += f"؛ {len(soon)} مورد تا یک ماه دیگر ({soon[0]['valid_until']}) سررسید می‌شود و باید تمدید شود"
        money.append({"text": s + "."})
    if money:
        sections.append({"title": "وضعیت مالی", "kind": "money", "items": money})

    # ── وضعیت امروز
    now = []
    if coming:
        c = coming[0]
        now.append({"text": f"نزدیک‌ترین مهلت {c['due']} است: {quote(c['text'], 160)}",
                    **{k: c[k] for k in ("doc_id", "doc_title", "page", "kind")}})
    if late:
        now.append({"text": f"مهلت {len(late)} مورد از نامه‌ها و صورتجلسه‌ها گذشته است (آخرین آن‌ها {late[0]['due']})؛ "
                            "اگر انجام شده‌اند جای نگرانی نیست، وگرنه پیگیری کنید."})
    counts: dict[str, int] = {}
    for d in ready:
        c = d.get("category") or "other"
        counts[c] = counts.get(c, 0) + 1
    if ready:
        parts = [f"{n} {CAT_PLURAL.get(c, c)}" for c, n in sorted(counts.items(), key=lambda x: -x[1])]
        text = f"در مجموع {len(ready)} سند در بایگانی این پروژه هست ({join_fa(parts)})."
        last = max(dated, key=lambda d: (d["doc_date"], d["id"])) if dated else None
        if last:
            text += f" آخرین رویداد ثبت‌شده، {CAT_PLURAL.get(last.get('category'), 'سند')} {quote(last.get('subject') or last['title'], 100)} مورخ {last['doc_date']} است."
        now.append({"text": text, **(src(last) if last else {})})
    if now:
        sections.append({"title": f"وضعیت امروز ({today})", "kind": "now", "items": now})

    undated = [d for d in ready if not d.get("doc_date") and d is not contract]
    if undated:
        sections.append({"title": "اسناد بدون تاریخ مشخص", "kind": "month", "items": [
            {"text": event_core(d, amount), **src(d)} for d in sorted(undated, key=lambda d: d["id"])]})
    return sections
