"""ورود دریافت‌ها و هزینه‌های گذشته از فایل Excel یا CSV (خروجی برنامه‌های دیگر).

هر برگه‌ی فایل جدا بررسی می‌شود:
- برگه‌ای که ستون تاریخ، مبلغ و شرح دارد ← ردیف‌های «دریافت و پرداخت»؛
- برگه‌ای با «موضوع / جزئیات» (بدون مبلغ) ← یادداشت؛
- برگه‌های خلاصه (جمع روزانه، جمع دسته‌ها…) کنار گذاشته می‌شوند تا چیزی دو بار حساب نشود.
واحد پول از عنوان ستون مبلغ («مبلغ (تومان)») تشخیص داده و به واحد سایت تبدیل می‌شود. ردیف‌هایی که قبلاً
ثبت شده‌اند (همان تاریخ، مبلغ و شرح) دوباره ثبت نمی‌شوند.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections import Counter
from typing import Any

from . import db, jalali
from .config import settings

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DATE = re.compile(r"(1[34]\d\d)\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})")
# نام ستون‌ها (به ترتیب اولویت)
COLS = {
    "date": ("تاریخ",),
    "income": ("دریافتی", "واریز", "بستانکار", "درآمد"),
    "expense": ("پرداختی", "برداشت", "بدهکار"),
    "amount": ("مبلغ", "قیمت", "هزینه"),
    "description": ("شرح", "بابت", "عنوان", "موضوع"),
    "category": ("دسته",),
    "kind": ("نوع",),
    "counterparty": ("طرف حساب", "طرف", "فروشنده", "گیرنده", "پرداخت به", "دریافت از"),
    "notes": ("توضیحات", "ملاحظات", "یادداشت"),
}
_EMPTY = {"", "-", "—", "نامشخص", "none", "null"}


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).replace("‌", "‌").split())


def read_tables(data: bytes, filename: str) -> list[tuple[str, list[list[Any]]]]:
    """برگه‌های فایل: [(نام برگه، سطرها)]."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        out = []
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            out.append((ws.title, [r for r in rows if any(c not in (None, "") for c in r)]))
        wb.close()
        return out
    if name.endswith(".csv") or name.endswith(".txt"):
        for enc in ("utf-8-sig", "utf-16", "cp1256"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise db.ValidationError("رمزگذاری فایل CSV شناخته نشد")
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t") if text.strip() else csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
        return [(filename.rsplit(".", 1)[0], rows)]
    raise db.ValidationError("فقط فایل Excel (xlsx) یا CSV پشتیبانی می‌شود")


def _find_cols(header: list[Any]) -> dict[str, int]:
    cols: dict[str, int] = {}
    names = [_s(h) for h in header]
    for key, words in COLS.items():
        for w in words:
            i = next((i for i, h in enumerate(names) if h.startswith(w) or f" {w}" in f" {h}"), None)
            if i is not None and i not in cols.values():
                cols[key] = i
                break
    return cols


def _unit_factor(header_text: str) -> int | float:
    """ضریب تبدیل به واحد سایت؛ از عنوان ستون مبلغ («(تومان)» / «(ریال)»)."""
    site_rial = "ریال" in settings.currency
    if "تومان" in header_text:
        return 10 if site_rial else 1
    if "ریال" in header_text:
        return 1 if site_rial else 0.1
    return 1


def _amount(v: Any) -> int | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    t = _s(v).translate(_FA).replace("٬", "").replace(",", "").replace("،", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return int(round(float(m.group(0)))) if m else None


def _date(v: Any) -> str | None:
    if isinstance(v, (dt.date, dt.datetime)):  # تاریخ میلادی اکسل
        return jalali.to_jalali(v)
    m = _DATE.search(_s(v).translate(_FA))
    if not m:
        return None
    try:
        return jalali.normalize(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
    except ValueError:
        return None


def _kind(text: str, default: str) -> str:
    t = text.replace("‌", " ")
    if re.search(r"بازپرداخت|پرداخت|هزینه|خرید|برداشت|قسط", t):
        return "expense"
    if re.search(r"دریافت|درآمد|واریز|تأمین|تامین|قرض|وام|فروش", t):
        return "income"
    return default


def parse(data: bytes, filename: str) -> dict[str, Any]:
    """خواندن فایل بدون ثبت: ردیف‌های آماده، یادداشت‌ها، برگه‌های کنارگذاشته و مشکلات."""
    rows: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    for title, table in read_tables(data, filename):
        hi, cols = None, {}
        for i, r in enumerate(table[:6]):  # سطر عنوان ستون‌ها
            c = _find_cols(r)
            if "date" in c and ("amount" in c or "income" in c or "expense" in c) and "description" in c:
                hi, cols = i, c
                break
            if {"description"} <= c.keys() and "date" not in c and "amount" not in c and len([x for x in r if x]) >= 2:
                hi, cols = i, c
        if hi is None:
            sheets.append({"name": title, "type": "skipped", "reason": "خلاصه یا جدول بدون ستون تاریخ/مبلغ/شرح"})
            continue
        header = [_s(h) for h in table[hi]]
        body = table[hi + 1:]
        if "date" not in cols:  # یادداشت: «موضوع | جزئیات»
            other = next((i for i in range(len(header)) if i != cols["description"] and header[i]), None)
            for r in body:
                t = _s(r[cols["description"]]) if cols["description"] < len(r) else ""
                if t:
                    notes.append({"title": t, "content": _s(r[other]) if other is not None and other < len(r) else ""})
            sheets.append({"name": title, "type": "notes", "count": len(body)})
            continue
        used = set(cols.values())
        extra = [i for i, h in enumerate(header) if h and i not in used]  # مثلاً «خودرو»
        default_kind = "income" if re.search(r"دریافت|درآمد|واریز", title) else "expense"
        n0 = len(rows)
        for ln, r in enumerate(body, start=hi + 2):
            def cell(key: str) -> Any:
                i = cols.get(key)
                return r[i] if i is not None and i < len(r) else None

            desc = _s(cell("description"))
            if not desc or re.match(r"^(جمع|مجموع|جمع کل)\b", desc):
                continue
            if "income" in cols or "expense" in cols:
                inc, exp = _amount(cell("income")), _amount(cell("expense"))
                kind, raw = ("income", inc) if inc else ("expense", exp)
                unit_col = header[cols["income" if inc else "expense"]]
            else:
                raw = _amount(cell("amount"))
                kind = _kind(_s(cell("kind")), default_kind)
                unit_col = header[cols["amount"]]
            if not raw:
                continue
            amount = int(round(abs(raw) * _unit_factor(unit_col)))
            if raw < 0 and "kind" not in cols:
                kind = "expense" if kind == "income" else "income"
            raw_date = cell("date")
            date = _date(raw_date)
            parts = [desc]
            for i in extra:
                v = _s(r[i]) if i < len(r) else ""
                if v and v.lower() not in _EMPTY:
                    parts.append(f"{header[i]}: {v}")
            note = _s(cell("notes"))
            if note:
                parts.append(note)
            if _s(raw_date) and (not date or re.search(r"[آ-ی]", _s(raw_date))):  # «… یا قبل از آن؛ تاریخ دقیق نامشخص»
                parts.append(f"تاریخ در فایل: {_s(raw_date)}")
            category = _s(cell("category")) or _s(cell("kind")) or None
            rows.append({
                "sheet": title, "line": ln, "tx_date": date, "kind": kind, "amount": amount,
                "category": category, "counterparty": _s(cell("counterparty")) or None,
                "description": " — ".join(parts)[:500], "date_guessed": date is None,
            })
        sheets.append({"name": title, "type": "transactions", "count": len(rows) - n0,
                       "unit": "تومان" if "تومان" in " ".join(header) else ("ریال" if "ریال" in " ".join(header) else None)})
    # ردیف‌های بی‌تاریخ: روز قبل از اولین تاریخ فایل (متن اصلی تاریخ در شرح می‌ماند)
    known = sorted(r["tx_date"] for r in rows if r["tx_date"])
    fallback = jalali.add_days(known[0], -1) if known else db.today_str()
    for r in rows:
        if not r["tx_date"]:
            r["tx_date"] = fallback
    return {"rows": rows, "notes": notes, "sheets": sheets}


def _key(r: dict) -> tuple:
    return (r["tx_date"], r["kind"], int(r["amount"]), r["description"])


def run(data: bytes, filename: str, project_id: int | None = None, commit: bool = False) -> dict[str, Any]:
    """پیش‌نمایش (commit=False) یا ثبت. ردیف‌هایی که قبلاً ثبت شده‌اند (مثلاً بارگذاری دوباره‌ی همین فایل) رد می‌شوند."""
    parsed = parse(data, filename)
    rows = parsed["rows"]
    with db.connect() as conn:
        if project_id and not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise db.ValidationError("پروژه پیدا نشد")
        existing = Counter(_key(dict(r)) for r in conn.execute(
            "SELECT tx_date, kind, amount, description FROM transactions"))
        existing_notes = {r["title"] for r in conn.execute("SELECT title FROM notes")}
        new, dup = [], 0
        for r in rows:
            k = _key(r)
            if existing[k] > 0:  # تکراری؛ هر ردیف موجود فقط یک ردیف فایل را می‌پوشاند
                existing[k] -= 1
                dup += 1
            else:
                new.append(r)
        new_notes = [n for n in parsed["notes"] if n["title"] not in existing_notes]
        if commit:
            for r in new:
                db.create(conn, "transactions", {
                    "tx_date": r["tx_date"], "kind": r["kind"], "amount": r["amount"], "project_id": project_id,
                    "category": r["category"], "counterparty": r["counterparty"], "description": r["description"]})
            for n in new_notes:
                db.create(conn, "notes", {"title": n["title"][:200], "content": n["content"],
                                          "tags": f"ورود از فایل {filename}"[:120], "project_id": project_id})
    by_cat: dict[str, int] = {}
    for r in new:
        if r["kind"] == "expense":
            by_cat[r["category"] or "بدون دسته"] = by_cat.get(r["category"] or "بدون دسته", 0) + r["amount"]
    dates = sorted(r["tx_date"] for r in new)
    return {
        "committed": commit, "sheets": parsed["sheets"], "new": len(new), "duplicates": dup, "notes": len(new_notes),
        "income": sum(r["amount"] for r in new if r["kind"] == "income"),
        "expense": sum(r["amount"] for r in new if r["kind"] == "expense"),
        "by_category": sorted(by_cat.items(), key=lambda x: -x[1]),
        "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
        "guessed": [r for r in new if r["date_guessed"]],
        "sample": new[:12], "currency": settings.currency,
    }
