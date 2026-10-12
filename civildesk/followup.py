"""پیگیری نامه‌ها (زنجیره‌ی نامه و پاسخ، نامه‌های بی‌پاسخ) و هشدارهای کنترل پروژه.

زنجیره از روی ارجاع‌های داخل متن نامه‌ها ساخته می‌شود: «بازگشت به نامه شماره …»، «عطف به …»، «پیرو …».
"""
from __future__ import annotations

from typing import Any

from . import jalali, textnorm

# جمله‌هایی که از گیرنده کاری می‌خواهند (نامه‌ی منتظر پاسخ/اقدام)
_ASK = ("خواهشمند", "مستدعی", "لازم است", "مقتضی است", "دستور فرمایید", "اعلام نمایید", "ارسال نمایید",
        "اقدام نمایید", "اقدام لازم", "پاسخ", "اعلام نظر", "بررسی و اعلام", "موظف", "ظرف مدت", "حداکثر تا")
WAIT_DAYS = 14  # بعد از این مدت بی‌پاسخ ماندن نامه هشدار داده می‌شود


def _asks(info: dict) -> bool:
    if info.get("deadline"):
        return True
    pts = (info.get("points") or {}).get("value") or ([info["gist"]["value"]] if info.get("gist") else [])
    text = textnorm.normalize(" ".join(pts))
    return any(textnorm.normalize(c) in text for c in _ASK)


def link_letters(rows: list[dict[str, Any]], infos: dict[int, dict], number_key, today: str) -> None:
    """به هر ردیف نامه اضافه می‌کند: reply_to (نامه‌هایی که این نامه پاسخ/پیرو آن‌هاست)، replies (پاسخ‌های بعدی)،
    missing (ارجاع به نامه‌ای که در بایگانی نیست)، needs_reply و waiting (روزهای بی‌پاسخ ماندن)."""
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        r.update(reply_to=[], replies=[], missing=[], needs_reply=False, waiting=None)
        key = number_key(r.get("doc_no"))
        if key:
            by_key.setdefault(key, []).append(r)

    def brief(x: dict) -> dict:
        return {"id": x["id"], "no": x.get("doc_no"), "date": x.get("doc_date"), "subject": x.get("subject") or x["title"]}

    for r in rows:
        for ref in infos.get(r["id"], {}).get("refs") or []:
            targets = [t for t in by_key.get(ref["key"], []) if t["id"] != r["id"]]
            if ref.get("date"):  # چند نامه با شماره‌ی مشابه: همانی که تاریخش می‌خواند
                targets = [t for t in targets if t.get("doc_date") == ref["date"]] or targets
            if not targets:
                r["missing"].append({"no": ref["no"], "date": ref.get("date")})
                continue
            t = targets[0]
            r["reply_to"].append(brief(t))
            if not r.get("doc_date") or not t.get("doc_date") or t["doc_date"] <= r["doc_date"]:
                t["replies"].append(brief(r))
    for r in rows:
        if r.get("category") != "letter" or r.get("status") != "ready":
            continue
        r["needs_reply"] = _asks(infos.get(r["id"], {}))
        if r["needs_reply"] and not r["replies"] and r.get("doc_date"):
            try:
                r["waiting"] = max(0, jalali.days_between(r["doc_date"], today))
            except ValueError:
                r["waiting"] = None


# ───────────────────────── هشدارهای کنترل پروژه ─────────────────────────


def _days(a: str, b: str) -> int | None:
    try:
        return jalali.days_between(a, b)
    except (ValueError, TypeError):
        return None


def _pct(a: float, b: float) -> float:
    return round(100 * a / b, 1) if b else 0.0


def fa_pct(x: float) -> str:
    return f"{x:g}٪"


def alerts(facts: dict[str, Any], invoices: list[dict], guarantees: list[dict], deadlines: list[dict],
           letters: list[dict], reports: list[dict], today: str) -> list[dict[str, Any]]:
    """فهرست هشدارها، مهم‌ترین اول: {level: red|amber|info, text, doc_id?, page?}."""
    out: list[dict[str, Any]] = []

    def add(level: str, text: str, src: dict | None = None) -> None:
        out.append({"level": level, "text": text,
                    **({k: src.get(k) for k in ("doc_id", "doc_title", "page", "kind")} if src and src.get("doc_id") else {})})

    amount = facts.get("contract_amount")
    start, end = facts.get("start_date"), facts.get("end_date")
    last = invoices[-1] if invoices else None
    work = (last or {}).get("work_total")

    # زمان در برابر کارکرد
    if start and end and (total := _days(start, end)) and total > 0:
        passed = _days(start, today)
        if passed is not None and passed > 0:
            time_pct = min(100.0, _pct(passed, total))
            if passed > total:
                add("red", f"مدت قرارداد {end} تمام شده و {passed - total:,} روز از آن گذشته است؛ "
                           "اگر تمدید شده، الحاقیه‌اش را بارگذاری کنید.")
            elif total - passed <= 60:
                add("amber", f"فقط {total - passed:,} روز تا پایان مدت قرارداد ({end}) مانده است.")
            if work and amount:
                fin = _pct(work, amount)
                gap = time_pct - fin
                if gap >= 15:
                    add("red" if gap >= 30 else "amber",
                        f"{fa_pct(time_pct)} از زمان قرارداد گذشته ولی کارکرد تأییدشده {fa_pct(fin)} مبلغ قرارداد است "
                        f"({fa_pct(round(gap, 1))} عقب‌تر از برنامه‌ی خطی).", last)
                elif gap <= -10:
                    add("info", f"کارکرد ({fa_pct(fin)} مبلغ قرارداد) از زمان سپری‌شده ({fa_pct(time_pct)}) جلوتر است.", last)
    # صورت‌وضعیت‌ها
    if last:
        cp, ap = last.get("claimed_period"), last.get("approved_period")
        if cp and ap and ap < cp and (cut := _pct(cp - ap, cp)) >= 3:
            add("amber", f"در صورت‌وضعیت {last.get('no') or 'آخر'} مشاور {fa_pct(cut)} کمتر از درخواست پیمانکار "
                         f"تأیید کرده است ({cp - ap:,} ریال).", last)
        if last.get("check_failed"):
            add("amber", f"جمع اعداد صورت‌وضعیت {last.get('no') or 'آخر'} با هم نمی‌خواند؛ اعداد را با سند چک کنید.", last)
        if last.get("date") and (age := _days(last["date"], today)) is not None and age > 60:
            add("amber", f"{age:,} روز است صورت‌وضعیت جدیدی بارگذاری نشده (آخرین: {last['date']}).", last)
        prog = last.get("progress")
        if prog and work and amount and abs(prog - _pct(work, amount)) >= 15:
            add("info", f"پیشرفت فیزیکی صورت‌وضعیت ({fa_pct(prog)}) با پیشرفت مالی ({fa_pct(_pct(work, amount))}) "
                        "فاصله‌ی زیادی دارد.", last)
    # ضمانت‌نامه‌ها
    for g in guarantees:
        if g.get("state") == "expired":
            add("red", f"ضمانت‌نامه‌ی «{g['subject'][:60]}» در {g['valid_until']} منقضی شده است.", g)
        elif g.get("state") == "soon":
            add("amber", f"ضمانت‌نامه‌ی «{g['subject'][:60]}» تا {g['valid_until']} اعتبار دارد؛ تمدیدش را پیگیری کنید.", g)
    # مهلت‌ها و نامه‌ها
    late = [d for d in deadlines if d.get("late") and (_days(d["due"], today) or 0) <= 60]
    if late:
        add("red", f"{len(late)} مهلت در دو ماه اخیر گذشته است (مثلاً {late[0]['due']}).", late[0])
    soon = [d for d in deadlines if not d.get("late") and (_days(today, d["due"]) or 99) <= 7]
    if soon:
        add("amber", f"{len(soon)} مهلت در هفته‌ی پیش رو است (اولین: {soon[0]['due']}).", soon[0])
    waiting = sorted((x for x in letters if (x.get("waiting") or 0) >= WAIT_DAYS), key=lambda x: -x["waiting"])
    if waiting:
        w = waiting[0]
        add("amber", f"{len(waiting)} نامه‌ی درخواست‌دار بیش از {WAIT_DAYS} روز است پاسخی در بایگانی ندارد "
                     f"(قدیمی‌ترین: «{(w.get('subject') or w['title'])[:60]}»، {w['waiting']:,} روز).",
            {"doc_id": w["id"], "doc_title": w["title"], "page": 1, "kind": w.get("kind")})
    # مشکلات گزارش ماهانه‌ی آخر
    if reports:
        r = reports[-1]
        probs = (r["info"].get("problems") or {}).get("value") or []
        if probs:
            add("info", "مشکلات گزارش ماهانه‌ی آخر: " + "؛ ".join(probs[:3]),
                {"doc_id": r["id"], "doc_title": r["title"], "page": r["info"]["problems"].get("page"), "kind": r["kind"]})
    order = {"red": 0, "amber": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["level"]])


def progress_chart(facts: dict[str, Any], invoices: list[dict], today: str) -> dict[str, Any] | None:
    """داده‌ی نمودار کارکرد تجمعی در برابر برنامه‌ی خطی (از شروع تا پایان، تا مبلغ قرارداد)."""
    pts = [{"date": x["date"], "value": x["work_total"], "no": x.get("no")} for x in invoices
           if x.get("work_total") and x.get("date")]
    if not pts:
        return None
    pts.sort(key=lambda p: p["date"])
    start, end = facts.get("start_date"), facts.get("end_date")
    base = start or pts[0]["date"]
    for p in pts:  # محور افقی: روز از شروع پیمان
        p["x"] = _days(base, p["date"]) or 0
    return {"start": start, "end": end, "contract": facts.get("contract_amount"), "today": today, "points": pts,
            "end_x": _days(base, end) if start and end else None, "today_x": _days(base, today)}
