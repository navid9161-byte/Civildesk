"""بایگانی زنده‌ی پروژه‌ها: دسته‌بندی خودکار اسناد، استخراج اطلاعات کلیدی و خلاصه‌ی به‌روز هر پروژه.

بدون هیچ سرویس هوش مصنوعی کار می‌کند: از روی واژه‌ها و قالب‌های رایج نامه‌ها و قراردادهای فارسی
(«شماره: … تاریخ: … پیوست: …»، «موضوع: …»، «مبلغ … ریال»، «مدت قرارداد … ماه» و …).
نتیجه‌ها «پیشنهاد» هستند؛ کاربر می‌تواند هر فیلد را اصلاح کند و اصلاح او در پردازش دوباره حفظ می‌شود.
"""
from __future__ import annotations

import bisect
import json
import logging
import re
import unicodedata
from typing import Any

from . import db, jalali, textnorm

log = logging.getLogger(__name__)

CATEGORIES: dict[str, str] = {
    "contract": "قرارداد",
    "amendment": "الحاقیه / تمدید",
    "letter": "نامه",
    "minutes": "صورتجلسه",
    "order": "دستور کار / ابلاغیه",
    "invoice": "صورت‌وضعیت",
    "guarantee": "ضمانت‌نامه / بیمه",
    "financial": "فاکتور / مالی",
    "report": "گزارش",
    "drawing": "نقشه",
    "photo": "عکس",
    "other": "سایر",
}
EDITABLE = ("category", "doc_date", "doc_no", "subject")
ANALYZE_PAGES = 6  # فقط صفحه‌های اول برای تشخیص و استخراج

# واژه‌های هر دسته (به شکل یکسان‌شده‌ی textnorm.normalize)
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "contract": ("قرارداد", "پیمان", "موافقتنامه", "شرایط عمومی", "شرایط خصوصی", "طرفین", "موضوع قرارداد",
                 "مبلغ قرارداد", "مدت قرارداد"),
    "amendment": ("الحاقیه", "متمم", "اصلاحیه", "تمدید", "افزایش مقادیر", "کاهش مقادیر"),
    "minutes": ("صورتجلسه", "صورت جلسه", "حاضرین", "مصوبات", "دستور جلسه", "مدعوین"),
    "order": ("دستور کار", "ابلاغیه", "دستورالعمل", "دستور تغییرات"),
    "invoice": ("صورت وضعیت", "صورتوضعیت", "کارکرد", "فهرست بها", "صورت وضعیت موقت", "صورت وضعیت قطعی"),
    "guarantee": ("ضمانت نامه", "ضمانتنامه", "بیمه نامه", "بیمه‌نامه", "حسن انجام کار", "ضمانت"),
    "financial": ("فاکتور", "پیش فاکتور", "صورتحساب", "رسید", "فیش", "واریز", "چک"),
    "report": ("گزارش",),
    "drawing": ("نقشه", "پلان", "مقطع", "دیتیل", "مقیاس", "نما"),
}
# عنوان‌های قوی: اگر در عنوان فایل یا سطرهای اول آمده باشند، تکلیف روشن است
_HEAD_TITLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("minutes", ("صورتجلسه", "صورت جلسه")),
    ("amendment", ("الحاقیه", "متمم قرارداد", "اصلاحیه قرارداد")),
    ("guarantee", ("ضمانت نامه", "ضمانتنامه", "بیمه نامه")),
    ("order", ("دستور کار", "ابلاغیه")),
    ("invoice", ("صورت وضعیت", "صورتوضعیت")),
    ("contract", ("قرارداد", "موافقتنامه", "پیمان")),
    ("financial", ("پیش فاکتور", "فاکتور", "صورتحساب")),
    ("report", ("گزارش",)),
    ("drawing", ("نقشه",)),
)
_SALUTATION = ("با سلام", "احتراما", "سلام علیکم", "با اهدای سلام", "با تقدیم سلام")

# ───────────────────────── آماده‌سازی متن ─────────────────────────

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬", "01234567890123456789,")


def _prep(text: str) -> str:
    """متن برای استخراج: ارقام لاتین، ی/ک یکسان، بدون کشیده؛ طول و سطرها حفظ می‌شود تا شماره‌ی صفحه پیدا شود."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DIGITS).translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ـ": " ", "：": ":"}))
    return text


def _norm_padded(text: str) -> str:
    return f" {textnorm.normalize(text)} "


def _count(norm: str, word: str) -> int:
    return norm.count(f" {word}")


SP = r"[\s‌]*"
_DATE = r"(1[34]\d\d)\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})"
_DATE_REV = r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(1[34]\d\d)"  # گاهی در PDF فارسی برعکس می‌آید
_DATE_RE = re.compile(rf"(?<!\d){_DATE}(?!\d)|(?<!\d){_DATE_REV}(?!\d)")


def _mkdate(y: str, m: str, d: str) -> str | None:
    try:
        s = jalali.normalize(f"{y}/{m}/{d}")
    except ValueError:
        return None
    return s if s and 1350 <= int(s[:4]) <= 1460 else None


def _date_from_match(m: re.Match) -> str | None:
    if m.group(1):
        return _mkdate(m.group(1), m.group(2), m.group(3))
    return _mkdate(m.group(6), m.group(5), m.group(4))


def _dates(text: str) -> list[tuple[int, str]]:
    out = []
    for m in _DATE_RE.finditer(text):
        d = _date_from_match(m)
        if d:
            out.append((m.start(), d))
    return out


def _date_after(text: str, pattern: str, window: int = 60) -> tuple[int, str] | None:
    """اولین تاریخی که حداکثر `window` نویسه بعد از الگو آمده باشد."""
    for m in re.finditer(pattern, text):
        seg = text[m.end(): m.end() + window]
        dm = _DATE_RE.search(seg)
        if dm and (d := _date_from_match(dm)):
            return m.start(), d
    return None


_WORD_NUM = {
    "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
    "یازده": 11, "دوازده": 12, "پانزده": 15, "هجده": 18, "بیست": 20, "سی": 30, "چهل": 40, "چهل و پنج": 45,
    "شصت": 60, "نود": 90,
}
_NUM = r"(\d{1,4}|" + "|".join(sorted(map(re.escape, _WORD_NUM), key=len, reverse=True)) + r")"


def _to_num(s: str) -> int | None:
    s = s.strip()
    return int(s) if s.isdigit() else _WORD_NUM.get(s)


_AMOUNT_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:[,.]\d{3})+|\d{5,})(?![\d,.]*\d)\s*(ریال|تومان|ريال)")


def _amounts(text: str) -> list[dict[str, Any]]:
    out, seen = [], set()
    for m in _AMOUNT_RE.finditer(text):
        v = int(re.sub(r"\D", "", m.group(1)))
        if m.group(2) == "تومان":
            v *= 10
        if v < 10000:
            continue
        ctx = text[max(0, m.start() - 90): m.start()].replace("\n", " ")
        key = (v, ctx[-25:])
        if key in seen:
            continue
        seen.add(key)
        out.append({"pos": m.start(), "value": v, "context": ctx.strip()[-70:]})
    return out


def _sentence(text: str, pos: int, limit: int = 220) -> str:
    """جمله یا سطری که موقعیت pos در آن است."""
    start = max(text.rfind("\n", 0, pos), max(text.rfind(c, 0, pos) for c in ".؟!:")) + 1
    ends = [i for i in (text.find(c, pos) for c in ("\n", ".", "؟", "!")) if i != -1]
    end = min(ends) + 1 if ends else len(text)
    out = re.sub(r"^\d{1,2}\s*[-.)]\s*", "", " ".join(text[start:end].split())).strip(" -–")
    return out if len(out) <= limit else out[:limit] + "…"


def _line_after(text: str, pattern: str, maxlen: int = 160) -> tuple[int, str] | None:
    m = re.search(pattern + r"\s*[:\-–]?\s*([^\n]{3,400})", text)
    if not m:
        return None
    val = re.split(r"\s{3,}|\s(?:شماره|تاریخ|پیوست)\s*:", m.group(m.lastindex))[0]
    val = val.strip(" :.-–،")
    return (m.start(), val[:maxlen]) if len(val) >= 3 else None


# ───────────────────────── دسته‌بندی ─────────────────────────


def classify(title: str, text: str, kind: str, pages: int) -> str:
    tnorm = _norm_padded(title)
    for cat, words in _HEAD_TITLES:  # نام فایل معمولاً بهترین نشانه است
        if any(_count(tnorm, w) for w in words):
            return cat
    norm = _norm_padded(text)
    words = len(norm.split())
    if kind == "image" and words < 25:
        return "photo"
    head_raw = text[:500]
    head = _norm_padded(head_raw)
    salut = any(_count(_norm_padded(text[:1500]), s) for s in _SALUTATION)
    header_marks = sum(1 for w in ("شماره", "تاریخ", "پیوست") if _count(head, w))
    letter_like = salut or header_marks >= 3

    # سطرهای ابتدایی: عنوان سند
    top = _norm_padded(head_raw[:260])
    for cat, ws in _HEAD_TITLES:
        if any(_count(top, w) for w in ws):
            if cat == "contract" and letter_like and pages <= 3:
                continue  # «شماره قرارداد» در سرنامه‌ی یک نامه
            if cat in ("invoice", "report", "drawing", "financial") and salut:
                continue  # نامه‌ی ارسال صورت‌وضعیت/گزارش هنوز «نامه» است
            return cat
    if letter_like and pages <= 4:
        return "letter"

    scores = {cat: sum(_count(norm, w) for w in ws) for cat, ws in _KEYWORDS.items()}
    if pages > 3:
        scores["contract"] = scores.get("contract", 0) + 2 * _count(norm, "ماده")
    best = max(scores, key=scores.get)
    if scores[best] >= 3:
        return best
    if letter_like:
        return "letter"
    return "photo" if kind == "image" and words < 60 else "other"


# ───────────────────────── استخراج ─────────────────────────


def extract(text: str, category: str) -> dict[str, Any]:
    """اطلاعات کلیدی سند؛ هر مورد با موقعیتش در متن (pos) برای یافتن شماره‌ی صفحه."""
    info: dict[str, Any] = {}
    flat = text.replace("\n", " ")  # هم‌طول با text
    head = text[:900]

    # شماره و تاریخ سند
    m = re.search(
        r"شماره" + SP + r"(?:نامه|قرارداد|پیمان|صورتجلسه)?\s*[:.]?\s*"
        r"([0-9][\w/\-.]{0,24}(?:\s*/\s*[\w\-.]{1,12}){0,3})", head)
    if m:
        info["doc_no"] = {"pos": m.start(), "value": re.sub(r"\s+", "", m.group(1)).strip("/.-")}
    dm = _date_after(head, r"تاریخ" + SP + r"(?:نامه|قرارداد|جلسه)?", 40)
    if not dm:
        ds = _dates(head)
        dm = ds[0] if ds else None
    if dm:
        info["doc_date"] = {"pos": dm[0], "value": dm[1]}

    # موضوع
    sub = _line_after(text[:2500], r"موضوع" + SP + r"(?:قرارداد|پیمان|جلسه|نامه)?\s*(?:عبارت\s+است\s+از)?")
    if sub:
        info["subject"] = {"pos": sub[0], "value": sub[1]}

    # گیرنده و فرستنده‌ی نامه
    rm = re.search(r"(?m)^\s*((?:جناب|سرکار|ریاست\s+محترم|مدیر\s*عامل\s+محترم|مدیریت\s+محترم|اداره\s+کل)[^\n]{3,120})",
                   text[:1500])
    if rm:
        info["to"] = {"pos": rm.start(1), "value": rm.group(1).strip()}
    for key, word in (("to", "به"), ("from", "از")):
        fm = re.search(rf"(?m)^\s*{word}\s*:\s*([^\n]{{3,100}})", text[:1500])
        if fm and key not in info:
            info[key] = {"pos": fm.start(1), "value": fm.group(1).strip()}

    # خلاصه: جمله‌های اول بعد از سلام، یا اولین بند معنادار
    gist = ""
    sm = re.search(r"(احتراما[ًٌ]?|با\s+سلام[^\n]{0,30}?(?:احترام|\n)|سلام\s+علیکم)", text[:2000])
    body = text[sm.end():] if sm else text
    body = re.sub(r"^[\s،,:.\-]+", "", body)
    for para in re.split(r"\n\s*\n|(?<=[.؟!])\s", body):
        para = " ".join(para.split())
        if len(para.split()) >= 6 and not re.match(r"^(شماره|تاریخ|پیوست|موضوع)\s*:", para):
            gist += (" " if gist else "") + para
            if len(gist) > 180:
                break
    if gist:
        info["gist"] = {"pos": 0, "value": gist[:300] + ("…" if len(gist) > 300 else "")}

    # مبالغ
    amounts = _amounts(text)
    if amounts:
        info["amounts"] = amounts[:12]
        def pick(*words: str) -> dict | None:
            cands = [a for a in amounts if any(w in textnorm.normalize(a["context"]) for w in words)]
            return max(cands, key=lambda a: a["value"]) if cands else None
        main = None
        if category in ("contract", "amendment"):
            main = pick("مبلغ قرارداد", "مبلغ پیمان", "مبلغ کل", "مبلغ اولیه", "مبلغ الحاقیه", "به مبلغ")
        elif category == "invoice":
            main = pick("قابل پرداخت", "خالص", "جمع کل", "جمع")
        elif category == "guarantee":
            main = pick("مبلغ", "به مبلغ", "تا مبلغ")
        main = main or max(amounts, key=lambda a: a["value"])
        info["amount"] = {"pos": main["pos"], "value": main["value"], "context": main["context"]}

    # طرفین (قرارداد و صورتجلسه)
    for role, words in (("employer", "کارفرما"), ("contractor", "پیمانکار"), ("consultant", "(?:مهندس\\s+)?مشاور")):
        pm = re.search(rf"(?m)^\s*{words}\s*:\s*([^\n]{{3,90}})", text)
        if pm:
            info[role] = {"pos": pm.start(1), "value": pm.group(1).strip(" .،")}
    if category in ("contract", "amendment"):
        # «این قرارداد بین X … که … کارفرما نامیده می‌شود از یک طرف و Y … پیمانکار …»
        for pm in re.finditer(
            r"(?:بین|و)\s+((?:شرکت|سازمان|اداره|شهرداری|آقای|خانم|موسسه|مؤسسه|دانشگاه|بنیاد|وزارت)[^\n،,]{2,70}?)\s+"
            r"(?:به\s+نمایندگی|به\s+شماره|به\s+شناسه|به\s+نشانی|که\s)[^.]{0,300}?"
            r"(کارفرما|پیمانکار|مشاور)\s+(?:نامیده|خوانده)", flat[:6000]):
            role = {"کارفرما": "employer", "پیمانکار": "contractor", "مشاور": "consultant"}[pm.group(2)]
            info.setdefault(role, {"pos": pm.start(1), "value": pm.group(1).strip()})

    # مدت، شروع و پایان
    du = re.search(r"مدت" + SP + r"(?:قرارداد|پیمان|اجرا(?:ی\s+کار)?|انجام\s+کار)[^\n\d]{0,30}?" + _NUM + r"\s*\(?[^\n)]{0,20}\)?\s*(روز|ماه|سال)",
                   flat)
    if du and (n := _to_num(du.group(1))):
        info["duration"] = {"pos": du.start(), "value": f"{n} {du.group(2)}", "n": n, "unit": du.group(2)}
    st = _date_after(flat, r"(?:تاریخ\s+)?(?:شروع|آغاز|تحویل\s+زمین)", 60)
    if st:
        info["start_date"] = {"pos": st[0], "value": st[1]}
    en = _date_after(flat, r"(?:تاریخ\s+)?(?:پایان|خاتمه|اتمام)", 60)
    if en:
        info["end_date"] = {"pos": en[0], "value": en[1]}
    if category == "guarantee":
        vu = _date_after(flat, r"(?:اعتبار|سررسید|انقضا|معتبر|تا\s+تاریخ)", 80)
        if vu:
            info["valid_until"] = {"pos": vu[0], "value": vu[1]}
    if category == "invoice":
        im = re.search(r"صورت" + SP + r"وضعیت\s+(?:موقت\s+|قطعی\s+)?(?:شماره\s*|ش\s*)?(\d{1,3})(?!\d)", flat)
        if im:
            info["invoice_no"] = {"pos": im.start(), "value": im.group(1)}

    # مهلت انجام کار (فقط در نامه‌ها، دستورها و صورتجلسه‌ها)
    dl = None if category not in ("letter", "order", "minutes", "other") else re.search(r"(?:ظرف|حداکثر\s+ظرف|حداکثر\s+تا|طی)\s*(?:مدت\s*)?" + _NUM + r"\s*\(?[^\n)]{0,15}\)?\s*(روز|هفته|ماه)", flat)
    if dl and (n := _to_num(dl.group(1))):
        info["deadline"] = {"pos": dl.start(), "value": f"{n} {dl.group(2)}", "days": n * {"روز": 1, "هفته": 7, "ماه": 30}[dl.group(2)],
                            "context": _sentence(text, dl.start())}
    elif category in ("letter", "order", "minutes", "other"):
        dd = _date_after(flat, r"(?:حداکثر\s+تا\s+تاریخ|تا\s+تاریخ|مهلت)", 30)
        if dd:
            info["deadline"] = {"pos": dd[0], "value": dd[1], "date": dd[1],
                                "context": _sentence(text, dd[0])}

    # مصوبات صورتجلسه
    if category == "minutes":
        mm = re.search(r"(?:مصوبات|تصمیمات|نتایج)[^\n]{0,20}\n", text)
        if mm:
            items = []
            for line in text[mm.end():].split("\n"):
                line = line.strip()
                if not line:
                    continue
                if not re.match(r"^(\d{1,2}|[-•*])\s*[-.)]?\s*\S", line) or len(items) >= 8:
                    if items:
                        break
                    continue
                items.append(re.sub(r"^(\d{1,2}|[-•*])\s*[-.)]?\s*", "", line)[:200])
            if items:
                info["items"] = {"pos": mm.start(), "value": items}
    return info


# ───────────────────────── تحلیل یک سند ─────────────────────────


def _doc_text(conn, doc_id: int, page_texts: list[tuple[int, str]] | None) -> tuple[str, list[int], list[int]]:
    """متن صفحه‌های اول؛ ترجیحاً متن اصلی صفحه (با سطرهای دست‌نخورده)، وگرنه از روی بندهای نمایه‌شده."""
    if page_texts is None:
        page_texts = [(r["page"], r["text"]) for r in conn.execute(
            "SELECT page, text FROM doc_chunks WHERE doc_id = ? AND page <= ? ORDER BY page, seq",
            (doc_id, ANALYZE_PAGES),
        )]
    parts, starts, pages, pos = [], [], [], 0
    for page, raw in page_texts[: ANALYZE_PAGES * 20]:
        t = _prep(raw) + "\n\n"
        starts.append(pos)
        pages.append(page)
        parts.append(t)
        pos += len(t)
    return "".join(parts), starts, pages


def analyze(doc_id: int, page_texts: list[tuple[int, str]] | None = None) -> dict[str, Any]:
    with db.connect() as conn:
        d = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not d:
            raise db.NotFound("سند پیدا نشد")
        text, starts, pages = _doc_text(conn, doc_id, page_texts)
        edited = set(json.loads(d["edited"] or "[]"))
        category = d["category"] if "category" in edited else classify(d["title"], text, d["kind"], d["pages"] or 1)
        info = extract(text, category) if text.strip() else {}
        for v in info.values():  # موقعیت در متن ← شماره‌ی صفحه
            items = v if isinstance(v, list) else [v]
            for it in items:
                if isinstance(it, dict) and "pos" in it:
                    i = bisect.bisect_right(starts, it.pop("pos")) - 1
                    it["page"] = pages[i] if i >= 0 else 1
        values = {
            "category": category,
            "doc_date": (info.get("doc_date") or {}).get("value"),
            "doc_no": (info.get("doc_no") or {}).get("value"),
            "subject": (info.get("subject") or {}).get("value"),
        }
        sets = {k: v for k, v in values.items() if k not in edited}
        project_id, auto = d["project_id"], d["project_auto"]
        if project_id is None and "project_id" not in edited:
            project_id = match_project(conn, f"{d['title']}\n{text}")
            auto = 1 if project_id else 0
        conn.execute(
            f"UPDATE documents SET {''.join(f'{k}=?, ' for k in sets)}info=?, project_id=?, project_auto=?, analyzed_at=? "
            "WHERE id = ?",
            (*sets.values(), json.dumps(info, ensure_ascii=False), project_id, auto, db.now_str(), doc_id),
        )
    return info


def match_project(conn, text: str) -> int | None:
    """اگر نام یا کد دقیقاً یک پروژه در متن سند آمده باشد، سند به آن پروژه تعلق دارد."""
    norm = _norm_padded(text[:8000])
    hits = []
    for p in conn.execute("SELECT id, name, code FROM projects"):
        keys = [textnorm.normalize(p["name"] or "")]
        if p["code"] and len(p["code"].strip()) >= 3:
            keys.append(textnorm.normalize(p["code"]))
        if any(len(k) >= 4 and f" {k} " in norm for k in keys):
            hits.append(p["id"])
    return hits[0] if len(hits) == 1 else None


def analyze_pending() -> int:
    """تحلیل اسناد آماده‌ای که هنوز تحلیل نشده‌اند (مثلاً اسنادی که قبل از این نسخه بارگذاری شده‌اند)."""
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM documents WHERE status='ready' AND analyzed_at IS NULL")]
    for i in ids:
        try:
            analyze(i)
        except Exception:
            log.exception("تحلیل سند %s ناموفق بود", i)
    return len(ids)


def update_meta(doc_id: int, data: dict[str, Any]) -> None:
    """ویرایش دستی دسته، تاریخ، شماره، موضوع، عنوان و پروژه‌ی سند."""
    sets: dict[str, Any] = {}
    with db.connect() as conn:
        d = conn.execute("SELECT edited FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not d:
            raise db.NotFound("سند پیدا نشد")
        edited = set(json.loads(d["edited"] or "[]"))
        if "title" in data:
            title = str(data["title"] or "").strip()
            if not title:
                raise db.ValidationError("عنوان خالی است")
            sets["title"] = title
        if "category" in data:
            if data["category"] not in CATEGORIES:
                raise db.ValidationError("دسته‌ی نامعتبر")
            sets["category"] = data["category"]
        if "doc_date" in data:
            try:
                sets["doc_date"] = jalali.normalize(data["doc_date"])
            except ValueError as e:
                raise db.ValidationError(str(e)) from None
        for k in ("doc_no", "subject"):
            if k in data:
                sets[k] = str(data[k] or "").strip() or None
        if "project_id" in data:
            pid = data["project_id"]
            pid = int(pid) if pid not in (None, "", 0, "0") else None
            if pid and not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise db.ValidationError("پروژه پیدا نشد")
            sets["project_id"] = pid
            sets["project_auto"] = 0
        edited |= {k for k in sets if k in (*EDITABLE, "project_id")}
        if not sets:
            return
        conn.execute(
            f"UPDATE documents SET {''.join(f'{k}=?, ' for k in sets)}edited=?, updated_at=? WHERE id = ?",
            (*sets.values(), json.dumps(sorted(edited)), db.now_str(), doc_id),
        )


# ───────────────────────── خلاصه‌ی پروژه ─────────────────────────


def _add_months(jdate: str, months: int) -> str:
    y, m, d = (int(x) for x in jdate.split("/"))
    m += months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    d = min(d, 31 if m <= 6 else 30)
    return jalali.normalize(f"{y}/{m}/{d}") or jdate


def _end_from_duration(start: str, n: int, unit: str) -> str | None:
    try:
        if unit == "روز":
            return jalali.add_days(start, n)
        return _add_months(start, n * (12 if unit == "سال" else 1))
    except ValueError:
        return None


def _doc_row(r) -> dict[str, Any]:
    d = dict(r)
    d["info"] = json.loads(d.get("info") or "{}")
    d["edited"] = json.loads(d.get("edited") or "[]")
    d["date"] = d.get("doc_date") or (d.get("created_at") or "")[:10]
    return d


def list_docs(project_id: int | None) -> list[dict[str, Any]]:
    where, args = ("d.project_id = ?", (project_id,)) if project_id else ("d.project_id IS NULL", ())
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT d.*, p.name AS project_name FROM documents d LEFT JOIN projects p ON p.id = d.project_id "
            f"WHERE {where}", args,
        ).fetchall()
    docs = [_doc_row(r) for r in rows]
    docs.sort(key=lambda d: (d["date"], d["id"]), reverse=True)
    return docs


def overview() -> dict[str, Any]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.name, p.status, COUNT(d.id) AS docs, MAX(COALESCE(d.doc_date, substr(d.created_at,1,10))) AS last "
            "FROM projects p LEFT JOIN documents d ON d.project_id = p.id GROUP BY p.id "
            "ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'tender' THEN 1 WHEN 'on_hold' THEN 2 ELSE 3 END, p.id DESC"
        ).fetchall()
        loose = conn.execute("SELECT COUNT(*) FROM documents WHERE project_id IS NULL").fetchone()[0]
    return {"projects": [dict(r) for r in rows], "unassigned": loose, "categories": CATEGORIES}


def _src(d: dict, item: dict | None = None) -> dict[str, Any]:
    return {"doc_id": d["id"], "doc_title": d["title"], "page": (item or {}).get("page", 1), "kind": d["kind"]}


def project_summary(project_id: int | None) -> dict[str, Any]:
    """خلاصه‌ی زنده‌ی پروژه از روی همه‌ی اسناد بایگانی‌شده‌اش."""
    docs = list_docs(project_id)
    project = None
    if project_id:
        with db.connect() as conn:
            project = db.get(conn, "projects", project_id)
    ready = [d for d in docs if d["status"] == "ready"]
    by_cat: dict[str, list[dict]] = {}
    for d in ready:
        by_cat.setdefault(d.get("category") or "other", []).append(d)
    counts = {c: len(v) for c, v in by_cat.items()}

    facts: list[dict[str, Any]] = []

    def fact(key: str, label: str, value: Any, d: dict, item: dict | None = None, **extra: Any) -> None:
        if value not in (None, ""):
            facts.append({"key": key, "label": label, "value": value, **_src(d, item), **extra})

    contracts = sorted(by_cat.get("contract", []), key=lambda d: d["date"])
    contract = contracts[0] if contracts else None
    if contract:
        info = contract["info"]
        fact("contract_no", "شماره‌ی قرارداد", contract.get("doc_no"), contract, info.get("doc_no"))
        fact("contract_date", "تاریخ قرارداد", contract.get("doc_date"), contract, info.get("doc_date"))
        fact("subject", "موضوع قرارداد", contract.get("subject"), contract, info.get("subject"))
        for key, label in (("employer", "کارفرما"), ("contractor", "پیمانکار"), ("consultant", "مشاور")):
            if key in info:
                fact(key, label, info[key]["value"], contract, info[key])
        if "amount" in info:
            fact("contract_amount", "مبلغ قرارداد", info["amount"]["value"], contract, info["amount"], money=True)
        if "duration" in info:
            fact("duration", "مدت قرارداد", info["duration"]["value"], contract, info["duration"])
        if "start_date" in info:
            fact("start_date", "تاریخ شروع", info["start_date"]["value"], contract, info["start_date"])
        end = info.get("end_date")
        if end:
            fact("end_date", "تاریخ پایان", end["value"], contract, end)
        elif "duration" in info and (start := (info.get("start_date") or {}).get("value") or (project or {}).get("start_date")):
            calc = _end_from_duration(start, info["duration"]["n"], info["duration"]["unit"])
            fact("end_date", "تاریخ پایان (محاسبه از مدت)", calc, contract, info["duration"])

    amendments = [{
        "date": d["date"], "no": d.get("doc_no"), "subject": d.get("subject") or (d["info"].get("gist") or {}).get("value"),
        "amount": (d["info"].get("amount") or {}).get("value"), "duration": (d["info"].get("duration") or {}).get("value"),
        **_src(d),
    } for d in sorted(by_cat.get("amendment", []), key=lambda d: d["date"])]

    invoices = sorted(by_cat.get("invoice", []), key=lambda d: (d["date"], d["id"]))
    last_invoice = None
    if invoices:
        d = invoices[-1]
        last_invoice = {"date": d["date"], "no": (d["info"].get("invoice_no") or {}).get("value"),
                        "amount": (d["info"].get("amount") or {}).get("value"), **_src(d, d["info"].get("amount"))}

    today = db.today_str()
    guarantees = []
    for d in sorted(by_cat.get("guarantee", []), key=lambda d: d["date"]):
        vu = (d["info"].get("valid_until") or {}).get("value")
        state = None
        if vu:
            left = jalali.days_between(today, vu)
            state = "expired" if left < 0 else "soon" if left <= 30 else "ok"
        guarantees.append({"date": d["date"], "subject": d.get("subject") or d["title"],
                           "amount": (d["info"].get("amount") or {}).get("value"), "valid_until": vu, "state": state,
                           **_src(d, d["info"].get("valid_until"))})

    deadlines = []
    for d in ready:
        dl = d["info"].get("deadline")
        if not dl:
            continue
        due = dl.get("date")
        if not due and dl.get("days") and d.get("doc_date"):
            try:
                due = jalali.add_days(d["doc_date"], dl["days"])
            except ValueError:
                due = None
        if due:
            deadlines.append({"due": due, "late": due < today, "text": dl.get("context") or dl["value"],
                              "doc_date": d["date"], "doc_no": d.get("doc_no"), "category": d.get("category"),
                              **_src(d, dl)})
    # اول مهلت‌های پیش رو (نزدیک‌ترین اول)، بعد گذشته‌ها (تازه‌ترین اول)
    deadlines = sorted((x for x in deadlines if not x["late"]), key=lambda x: x["due"]) + \
        sorted((x for x in deadlines if x["late"]), key=lambda x: x["due"], reverse=True)

    minutes = [{"date": d["date"], "items": d["info"]["items"]["value"], **_src(d, d["info"]["items"])}
               for d in sorted(by_cat.get("minutes", []), key=lambda d: d["date"], reverse=True)
               if d["info"].get("items")][:3]

    # پیشنهاد تکمیل مشخصات پروژه از روی قرارداد (فقط فیلدهای خالی)
    suggestions = []
    if project:
        mapping = {"contract_no": "code", "employer": "employer", "contractor": "contractor",
                   "consultant": "consultant", "contract_amount": "contract_amount",
                   "start_date": "start_date", "end_date": "end_date"}
        labels = {f.name: f.label for f in db.ENTITIES["projects"].fields}
        for f in facts:
            field = mapping.get(f["key"])
            if field and not project.get(field):
                suggestions.append({"field": field, "label": labels[field], "value": f["value"], "money": f.get("money", False)})

    timeline = [{
        "id": d["id"], "date": d["date"], "category": d.get("category") or "other", "title": d["title"],
        "doc_no": d.get("doc_no"), "subject": d.get("subject"), "gist": (d["info"].get("gist") or {}).get("value"),
    } for d in ready[:15]]

    return {
        "project": project, "documents": docs, "counts": counts, "categories": CATEGORIES,
        "facts": facts, "amendments": amendments, "last_invoice": last_invoice, "invoices": len(invoices),
        "guarantees": guarantees, "deadlines": deadlines[:12], "minutes": minutes, "suggestions": suggestions,
        "timeline": timeline, "today": today,
        "pending": sum(1 for d in docs if d["status"] in ("queued", "processing")),
        "last_update": max((d.get("analyzed_at") or "" for d in docs), default="") or None,
    }
