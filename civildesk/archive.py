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

from . import db, followup, jalali, learning, story, textnorm

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
ANALYZE_PAGES = 12  # فقط صفحه‌های اول برای تشخیص، استخراج و خلاصه
TEXT_PAGES = 40  # متن این تعداد صفحه نگه داشته می‌شود (بخش‌های گزارش ماهانه، مثل مشکلات، در صفحه‌های بعدی‌اند)
TEXT_CHARS = 150000
ARCHIVE_VERSION = "6"  # با تغییر آن، همه‌ی اسناد یک بار دوباره تحلیل می‌شوند

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
    ("invoice", ("صورت وضعیت", "صورتوضعیت", "وضعیت شماره", "وضعیت موقت", "وضعیت قطعی")),
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
_DATE_MIR = r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2}[34]1)"  # «81/70/5041»: رقم‌های 1405/07/18 برعکس
_DATE_RE = re.compile(rf"(?<!\d){_DATE}(?!\d)|(?<!\d){_DATE_REV}(?!\d)|(?<!\d){_DATE_MIR}(?!\d)")
LBL_DATE = r"(?:تاریخ|تارخ|ناریخ|تاربخ|تاریح|تاریج|تاريخ)"
LBL_NO = r"(?:شماره|شعاره|سماره|شیماره|شماوه)"
_NOT_DOC_DATE = re.compile(r"(?:مورخ|مورّخ|تا\s*تاریخ|لغایت|الی|از\s*تاریخ|مهلت|به\s*تاریخ|ابلاغ\s*شده)[\s:]*$")
_NOT_DOC_NO = r"(?!\s*(?:تماس|حساب|ملی|ثبت|پلاک|تلفن|همراه|کارت|شبا|اقتصادی|شناسنامه|پرونده|فکس|نمابر|قرارداد|پیمان))"


def _mkdate(y: str, m: str, d: str) -> str | None:
    try:
        s = jalali.normalize(f"{y}/{m}/{d}")
    except ValueError:
        return None
    return s if s and 1350 <= int(s[:4]) <= 1460 else None


def _date_from_match(m: re.Match) -> str | None:
    if m.group(1):
        return _mkdate(m.group(1), m.group(2), m.group(3))
    if m.group(4):
        return _mkdate(m.group(6), m.group(5), m.group(4))
    return _mkdate(m.group(9)[::-1], m.group(8)[::-1], m.group(7)[::-1])


HEADER_START, HEADER_END = "سربرگ:\n", "\n[پایان سربرگ]"
# شماره‌ی نامه‌های اداری: «۱۴۰۵/۱۲۸۷/ص» (سال/ردیف/حرف)؛ «/» در OCR گاهی «ا» یا «|» خوانده می‌شود
_LETTER_NO = re.compile(r"(?<!\d)(1[34]\d\d)\s*[/ا|\\]\s*(\d{2,6})(?!\d)(?:\s*[/ا|\\]?\s*([آ-ی])(?![آ-ی]))?")
_LETTER_NO_REV = re.compile(r"(?<!\d)(\d{2,6})\s*[/ا|\\]\s*(1[34]\d\d)(?!\d)(?:\s*[/ا|\\]?\s*([آ-ی])(?![آ-ی]))?")
_DATE_LOOSE = re.compile(r"(?<!\d)(1[34]\d\d)\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})")  # رقم اضافه‌ی بعدش (نقطه‌چین) مهم نیست


def header_block(text: str) -> str | None:
    """متن سربرگ (OCR جداگانه‌ی بالای صفحه‌ی اول) اگر در ابتدای متن آمده باشد."""
    if not text.startswith(HEADER_START):
        return None
    end = text.find(HEADER_END)
    return text[len(HEADER_START): end if end != -1 else min(len(text), 2500)]


def _vote(cands: list[tuple[int, str]]) -> tuple[int, str] | None:
    """رأی‌گیری بین خوانش‌های مختلف سربرگ: پرتکرارترین مقدار (هر خوانش اشتباه متفاوتی دارد)."""
    if not cands:
        return None
    counts: dict[str, int] = {}
    for _, v in cands:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    return next((p, v) for p, v in cands if counts[v] == best)


def _year_ok(y: int) -> bool:
    cy = int(db.today_str()[:4])
    return cy - 40 <= y <= cy + 1


def _top_candidates(body: str) -> tuple[list[tuple[int, int, str, str]], list[tuple[int, str]]]:
    """شماره و تاریخ بی‌برچسب در ابتدای متن صفحه (در PDFهای Word برچسب‌ها در تصویر سربرگ‌اند و
    فقط مقدارها در متن آمده‌اند، گاهی با ترتیب برعکس «ردیف/سال/حرف»)."""
    top = body[:260]
    nos = []
    for rx, yi, qi in ((_LETTER_NO, 1, 2), (_LETTER_NO_REV, 2, 1)):
        for m in rx.finditer(top):
            y, q = int(m.group(yi)), m.group(qi)
            if not _year_ok(y) or len(q) < 3:
                continue
            if yi == 2 and _year_ok(int(q[:4])) and len(q) == 4 and abs(int(q) - int(db.today_str()[:4])) < abs(y - int(db.today_str()[:4])):
                y, q = int(q), str(y)  # هر دو شبیه سال‌اند: سال نزدیک‌تر به امسال
            nos.append((m.start(), y, q, m.group(3) or ""))
    dates = [(p, d) for p, d in _dates(top) if not _NOT_DOC_DATE.search(top[max(0, p - 16): p])][:1]
    return nos, dates


def _header_votes(block: str, body: str = "") -> tuple[tuple[int, str] | None, tuple[int, str] | None]:
    """شماره و تاریخ از چند خوانش سربرگ، با رأی‌گیری.

    خطاهای رایج OCR در کادر نقطه‌چین: صفرهای نقطه‌چین که به انتهای عدد می‌چسبند («۱۲۸۷۰......»)،
    سال نامعقول («۱۴۵۵»)، و ماه/روز خراب. برای تاریخ، اگر هیچ خوانشی کامل درست نبود، سال و ماه و روز
    جداگانه رأی‌گیری می‌شوند (و سالِ شماره‌ی نامه هم یک رأی برای سال است).
    """
    nos: list[tuple[int, int, str, str]] = []  # (جا، سال، ردیف، حرف)
    for m in re.finditer(LBL_NO, block):
        win = block[m.end(): m.end() + 50]
        nm = _LETTER_NO.search(win)
        if not nm or (len(nm.group(2)) <= 2 and re.match(r"\s*[/\-.]\s*\d", win[nm.end(2):])):
            continue  # «۱۴۰۵/۰۷/…» تاریخ است نه شماره
        seq = nm.group(2)
        if win[nm.end(2): nm.end(2) + 1] == "." and seq.rstrip("0") and len(seq.rstrip("0")) >= 2:
            seq = seq.rstrip("0")  # صفرهایی که از نقطه‌چین چسبیده‌اند
        if _year_ok(int(nm.group(1))):
            nos.append((m.start(), int(nm.group(1)), seq, nm.group(3) or ""))
    top_nos, top_dates = _top_candidates(body)
    off = len(HEADER_START) + len(block) + len(HEADER_END) if body else 0
    nos += [(p + off, y, q, sfx) for p, y, q, sfx in top_nos] * 2  # متن خود صفحه: دو رأی
    number = None
    if nos:
        votes: dict[tuple[int, str], int] = {}
        for _, y, q, _ in nos:
            votes[(y, q)] = votes.get((y, q), 0) + 1
        for (y, q), n in list(votes.items()):  # «۱۲۸۷۰» هم رأی «۱۲۸۷» است
            for (y2, q2) in votes:
                if y2 == y and q != q2 and q.startswith(q2) and set(q[len(q2):]) == {"0"}:
                    votes[(y2, q2)] += n
        (y, q) = max(votes, key=lambda k: (votes[k], -len(k[1])))
        suffix = max((x[3] for x in nos if x[1] == y and x[2].startswith(q) and x[3]), key=lambda c: sum(
            1 for x in nos if x[3] == c), default="")
        pos = next(x[0] for x in nos if x[1] == y and x[2].startswith(q))
        number = (pos, f"{y}/{q}" + (f"/{suffix}" if suffix else ""))

    full, parts, first = [], [], None
    for m in re.finditer(r"(?<!تا )(?<!از )(?<!به )" + LBL_DATE, block):
        dm = re.search(r"(?<!\d)(1[34]\d\d)\s*[/\-.]\s*(\d{1,3})\s*[/\-.]\s*(\d{1,4})", block[m.end(): m.end() + 50])
        if not dm:
            continue
        first = first if first is not None else m.start()
        y, mo, d = dm.group(1), dm.group(2)[:2], dm.group(3)[:2]
        parts.append((y, mo, d))
        if _year_ok(int(y)) and (date := _mkdate(y, mo, d)):
            full.append((m.start(), date))
    full += [(p + off, d) for p, d in top_dates] * 2
    date = _vote(full)
    if date is None and parts:
        def pick(vals: list[str], ok) -> str | None:
            vals = [v for v in vals if ok(v)]
            return max(vals, key=vals.count) if vals else None
        years = [p[0] for p in parts] + ([str(number[1][:4])] if number else [])
        y = pick(years, lambda v: _year_ok(int(v)))
        mo = pick([p[1] for p in parts], lambda v: 1 <= int(v) <= 12)
        d = pick([p[2] for p in parts], lambda v: 1 <= int(v) <= 31)
        if y and mo and d and (val := _mkdate(y, mo, d)):
            date = (first, val)
    return number, date


def labeled_date(text: str) -> tuple[int, str] | None:
    """تاریخ کنار برچسب «تاریخ:» (بعد یا قبل از آن، چون OCR گاهی ترتیب را برعکس می‌کند)."""
    block = header_block(text)
    if block and (voted := _header_votes(block, strip_header(text))[1]):
        return voted
    for m in re.finditer(r"(?<!تا )(?<!از )(?<!به )" + LBL_DATE + SP + r"(?:نامه|جلسه|صدور|ثبت)?\s*[:.]?", text):
        dm = _DATE_RE.search(text[m.end(): m.end() + 45])
        if dm and (d := _date_from_match(dm)):
            return m.start(), d
        before = list(_DATE_RE.finditer(text[max(0, m.start() - 28): m.start()]))
        if before and (d := _date_from_match(before[-1])):
            return m.start(), d
    return None


def labeled_number(text: str, letter: bool) -> tuple[int, str] | None:
    """شماره‌ی نامه/سند کنار برچسب «شماره:» (بعد یا قبل از آن)."""
    block = header_block(text)
    if block and (voted := _header_votes(block, strip_header(text))[0]):
        return voted
    if not block and letter:  # بدون سربرگ OCR: شماره‌ی بی‌برچسب بالای صفحه
        top_nos = _top_candidates(text)[0]
        if top_nos:
            p, y, q, sfx = top_nos[0]
            return p, f"{y}/{q}" + (f"/{sfx}" if sfx else "")
    extra = r"(?:نامه|ثبت\s*دبیرخانه)?" if letter else r"(?:نامه|قرارداد|پیمان|صورتجلسه)?"
    value = r"([0-9][\w/\-.]{0,24}(?:\s*/\s*[\w\-.]{1,12}){0,3})"
    for m in re.finditer(LBL_NO + SP + extra + _NOT_DOC_NO + r"\s*[:.]?\s*", text):
        if re.search(r"(?:قرارداد|پیمان|وضعیت|صورتجلسه|پلاک|ثبت)\s*$", text[max(0, m.start() - 16): m.start()]):
            continue  # «قرارداد شماره …» شماره‌ی نامه نیست
        for vm in (re.match(value, text[m.end(): m.end() + 40]),
                   re.search(value + r"[\s:]*$", text[max(0, m.start() - 34): m.start()])):
            if not vm:
                continue
            v = re.sub(r"\s+", "", vm.group(1)).strip("/.-")
            # «۰۰۰۰»، «۰۰۸»: نقطه‌چین کادر سربرگ که صفر خوانده شده
            if sum(c.isdigit() and c != "0" for c in v) >= 2 and sum(c.isdigit() for c in v) >= 3 and not _DATE_RE.fullmatch(v):
                return m.start(), v
    return None


# ارجاع به نامه‌ی دیگر: «بازگشت به نامه شماره ۱۴۰۵/۱۲۳/ص مورخ ۱۴۰۵/۰۶/۰۲»، «پیرو نامه‌ی …»
_REF = re.compile(
    r"(?:(بازگشت|عطف|پیرو|در\s+پاسخ|در\s+جواب|جوابیه|پاسخ)\s*(?:به\s+)?)?نامه(?:[\s‌]*ی)?(?:\s+(?:شماره|به\s+شماره))?\s*[:.]?\s*"
    r"((?:[آ-ی]{1,4}\s*[/\-]\s*)?\d{1,6}(?:\s*[/\-]\s*(?:\d{1,6}|[آ-ی]{1,4}(?![آ-ی])))+|\d{3,7}(?![\d/]))"
    r"(?:[\s،,]*(?:مورخ|مورّخ|به\s+تاریخ|تاریخ|مورخه)\s*[:.]?\s*(\d{2,4}\s*/\s*\d{1,2}\s*/\s*\d{1,4}))?")


def number_key(no: str | None) -> str | None:
    """کلید مقایسه‌ی شماره‌ی نامه: گروه‌های رقمی (ترتیب و حروف و جداکننده مهم نیستند)."""
    groups = sorted(g.lstrip("0") or "0" for g in re.findall(r"\d+", _prep(no or "")))
    if not groups or sum(len(g) for g in groups) < 3:
        return None
    return "-".join(groups)


def letter_refs(text: str, own_no: str | None = None) -> list[dict[str, Any]]:
    """نامه‌هایی که این نامه به آن‌ها ارجاع داده (reply: «بازگشت/عطف/پیرو/در پاسخ»)."""
    own = number_key(own_no)
    out, seen = [], set()
    for m in _REF.finditer(text):
        no = re.sub(r"\s+", "", m.group(2))
        if _DATE_RE.fullmatch(no):  # «نامه مورخ ۱۴۰۵/۰۶/۰۲» تاریخ است، نه شماره
            continue
        key = number_key(no)
        if not key or key == own or key in seen:
            continue
        seen.add(key)
        date = None
        if m.group(3):
            dm = _DATE_RE.search(m.group(3))
            date = _date_from_match(dm) if dm else None
        out.append({"pos": m.start(), "no": no, "key": key, "date": date, "reply": bool(m.group(1))})
    return out[:8]


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


# ───────────────────────── صورت‌وضعیت ─────────────────────────

W = r"[\s‌]*"
_MONTHS = "|".join(jalali.MONTHS_FA)
_BIGNUM = re.compile(r"(?<![\d/\-.,])\(?(\d{1,3}(?:[,.]\d{3})+|\d{6,})\)?(?![\d/\-]|[.,]\d)")
# برچسب ردیف‌های برگ خلاصه‌ی صورت‌وضعیت (به ترتیب رایج)
INVOICE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("work_total", "جمع کارکرد تا این صورت‌وضعیت",
     r"(?:مبلغ|جمع)" + W + r"(?:کل" + W + r")?(?:کارکرد|کار" + W + r"انجام" + W + r"شده)" + W +
     r"(?:تجمعی|کل|تا" + W + r"(?:این|پایان|کنون|تاریخ|آخر))"),
    ("work_prev", "کارکرد صورت‌وضعیت قبلی",
     r"(?:کارکرد|صورت" + W + r"وضعیت)" + W + r"(?:ها" + W + r"ی" + W + r")?(?:قبلی|قبل|گذشته|پیشین)"),
    ("work_period", "کارکرد این دوره",
     r"کارکرد" + W + r"(?:این" + W + r"(?:دوره|صورت" + W + r"وضعیت|مرحله|ماه)|(?:در" + W + r")?دوره(?!" + W + r"(?:قبل|ی" + W + r"قبل)))"),
    ("adjustment", "تعدیل", r"(?:مبلغ" + W + r")?تعدیل"),
    ("deductions", "جمع کسورات", r"(?:جمع" + W + r")?کسور(?:ات)?"),
    ("net", "خالص قابل پرداخت",
     r"(?:خالص" + W + r"(?:قابل" + W + r")?پرداخت|مبلغ" + W + r"قابل" + W + r"پرداخت|خالص" + W + r"صورت" + W + r"وضعیت)"),
)
INVOICE_LABELS = {k: lbl for k, lbl, _ in INVOICE_ROWS}


def invoice_fields(text: str, flat: str) -> dict[str, Any]:
    """اطلاعات برگ خلاصه‌ی صورت‌وضعیت: نوع، شماره، دوره، کارکرد قبلی/این دوره/تجمعی، تعدیل، کسورات، خالص، پیشرفت.

    مبالغ جدول معمولاً «ریال» ندارند؛ برای هر برچسب، اولین عدد بزرگ استفاده‌نشده بعد از آن (یا اگر نبود، قبل از آن)
    برداشته می‌شود. برچسب‌ها به ترتیب جایشان در متن پردازش می‌شوند تا ستون اعداد جدا از ستون شرح هم درست جفت شود.
    """
    info: dict[str, Any] = {}
    km = re.search(r"(?:صورت" + W + r")?وضعیت" + W + r"(?:شماره" + W + r"\d{1,3}" + W + r")?[-–:]?" + W +
                   r"(موقت|قطعی|تعدیل|نهایی)", flat)
    if km:
        info["invoice_kind"] = {"pos": km.start(), "value": "قطعی" if km.group(1) == "نهایی" else km.group(1)}
    im = (re.search(r"(?:صورت" + W + r")?وضعیت" + W + r"(?:موقت|قطعی|تعدیل)?" + W +
                    r"(?:شماره|ش\.?|No\.?)" + W + r"[:.]?" + W + r"(\d{1,3})(?!\d)", flat)
          or re.search(r"شماره" + W + r"(?:صورت" + W + r")?وضعیت" + W + r"[:.]?" + W + r"(\d{1,3})(?!\d)", flat)
          or re.search(r"صورت" + W + r"وضعیت" + W + r"(?:موقت|قطعی)?" + W + r"(\d{1,3})(?![\d/])", flat))
    if im:
        info["invoice_no"] = {"pos": im.start(), "value": str(int(im.group(1)))}  # «۰۶» ← «6»

    # دوره‌ی کارکرد
    for pm in re.finditer(r"(?:دوره|از" + W + r"تاریخ|کارکرد" + W + r"از|مدت" + W + r"کارکرد)", flat):
        ds = [(p + pm.end(), d) for p, d in _dates(flat[pm.end(): pm.end() + 90])]
        if len(ds) >= 2 and ds[0][1] < ds[1][1]:
            info["period"] = {"pos": pm.start(), "value": f"{ds[0][1]} تا {ds[1][1]}", "from": ds[0][1], "to": ds[1][1]}
            break
    if "period" not in info:
        mm = re.search(rf"(?:ماه|دوره|کارکرد)[^\n\d]{{0,12}}?({_MONTHS})(?:" + W + r"(?:تا|الی|لغایت|-)" + W +
                       rf"({_MONTHS}))?" + W + r"(?:ماه" + W + r")?(?:سال" + W + r")?(1[34]\d\d)", flat)
        if mm:
            label = f"{mm.group(1)}{' تا ' + mm.group(2) if mm.group(2) else ''} {mm.group(3)}"
            info["period"] = {"pos": mm.start(), "value": label}
    if "period" not in info:
        te = _date_after(flat, r"(?:تا|لغایت)" + W + r"(?:تاریخ|پایان)", 30)
        if te:
            info["period"] = {"pos": te[0], "value": f"تا {te[1]}", "to": te[1]}

    # مبالغ برگ خلاصه
    found = []
    for key, _, pat in INVOICE_ROWS:
        m = re.search(pat, flat)
        if m:
            found.append((m.start(), m.end(), key))
    used: set[int] = set()
    for start, end, key in sorted(found):
        val = None
        for nm in _BIGNUM.finditer(flat, end, min(len(flat), end + 320)):
            v = int(re.sub(r"\D", "", nm.group(1)))
            if nm.start() not in used and v >= 100000:
                val, pos = v, nm.start()
                break
        if val is None:  # جدول با ترتیب برعکس: عدد قبل از شرح
            before = [nm for nm in _BIGNUM.finditer(flat, max(0, start - 140), start)
                      if nm.start() not in used and int(re.sub(r"\D", "", nm.group(1))) >= 100000]
            if before:
                val, pos = int(re.sub(r"\D", "", before[-1].group(1))), before[-1].start()
        if val is not None:
            used.add(pos)
            info[key] = {"pos": start, "value": val}
    tot, prev, per = (info.get(k, {}).get("value") for k in ("work_total", "work_prev", "work_period"))
    if per is None and tot and prev is not None and tot > prev:
        info["work_period"] = {"pos": info["work_total"]["pos"], "value": tot - prev, "computed": True}
    elif tot is None and per and prev is not None:
        info["work_total"] = {"pos": info["work_period"]["pos"], "value": per + prev, "computed": True}
    elif tot and prev is not None and per and abs(tot - prev - per) > 0.02 * tot:
        info["work_check"] = {"pos": 0, "value": False}  # جمع‌ها نمی‌خواند؛ احتمالاً یک عدد اشتباه خوانده شده

    pg = re.search(r"(?:درصد" + W + r"پیشرفت|پیشرفت" + W + r"(?:فیزیکی|کار|کل|واقعی)?)[^\d\n]{0,15}?"
                   r"(\d{1,3}(?:[./]\d{1,2})?)" + W + r"(?:%|٪|درصد)", flat)
    if pg and float(pg.group(1).replace("/", ".")) <= 100:
        info["progress"] = {"pos": pg.start(), "value": float(pg.group(1).replace("/", "."))}
    return info


SHEET_ROWS: tuple[tuple[str, str], ...] = (
    ("claimed_period", "درخواستی پیمانکار طی دوره"),
    ("approved_period", "تأییدشده‌ی مشاور طی دوره"),
    ("claimed_total", "درخواستی پیمانکار تاکنون"),
    ("approved_total", "تأییدشده‌ی مشاور تاکنون"),
    ("contract_total", "مبلغ کل قرارداد (طبق صورت‌وضعیت)"),
)


def best_sheet(sheets: list[dict]) -> dict | None:
    """برگ خلاصه‌ی اصلی: آنکه «خلاصه/کل» در عنوانش هست، وگرنه بزرگ‌ترین مبلغ قرارداد."""
    if not sheets:
        return None
    def score(sh: dict) -> tuple:
        title = sh.get("title") or ""
        return ("خلاصه" in title or " کل" in title, (sh.get("sum") or {}).get("contract") or 0,
                len(sh.get("by_role") or {}))
    return max(sheets, key=score)


def apply_sheets(info: dict[str, Any], sheets: list[dict]) -> None:
    """مبالغ صورت‌وضعیت از جدول خوانده‌شده (دقیق‌تر از جست‌وجوی برچسب در متن)."""
    sh = best_sheet(sheets)
    if not sh:
        return
    page, total, roles = sh["page"], sh.get("sum") or {}, sh.get("by_role") or {}
    cons, contr = roles.get("مشاور") or {}, roles.get("پیمانکار") or {}
    best_role = cons or contr or roles.get("کارفرما") or {}

    def put(key: str, value: Any) -> None:
        if value not in (None, 0):
            info[key] = {"value": value, "page": page, "source": "table"}

    put("work_total", total.get("total") or best_role.get("total"))
    put("work_prev", total.get("prev") if "prev" in total else best_role.get("prev"))
    period = total.get("period") or (total["total"] - total["prev"] if total.get("total") and "prev" in total else None) \
        or best_role.get("period")
    put("work_period", period)
    put("claimed_period", contr.get("period"))
    put("claimed_total", contr.get("total"))
    put("approved_period", cons.get("period"))
    put("approved_total", cons.get("total"))
    contract = total.get("contract") or sum(s_["contract"] or 0 for s_ in sh.get("sections", [])) or None
    put("contract_total", contract)
    if contract and info.get("work_total"):
        info["progress_fin"] = {"value": round(100 * info["work_total"]["value"] / contract, 2), "page": page}
    info.pop("work_check", None)
    info["sheets"] = {"value": [{k: v for k, v in x.items() if k != "groups"} for x in sheets[:6]], "page": page}
    if any(x.get("corrected") for x in sheets):
        info["sheet_fixes"] = {"value": [c for x in sheets for c in x.get("corrected", [])], "page": page}


# ───────────────────────── خلاصه‌ی استخراجی ─────────────────────────

_CUES = ("خواهشمند", "مقرر", "ابلاغ", "لازم", "موظف", "تمدید", "تاخیر", "پرداخت", "درخواست", "اعلام", "تایید",
         "موافقت", "الزام", "جریمه", "خسارت", "تحویل", "شروع", "پایان", "اتمام", "مبلغ", "مدت", "تعهد", "اصلاح",
         "shall", "amend", "extension", "payment", "completion")
_HEADER_START = re.compile(r"^(شماره|تاریخ|پیوست|موضوع|رونوشت|جناب|سرکار|امضا|نام\s|ردیف|صفحه|مدیرعامل|ریاست|"
                           r"نشانی|آدرس|تلفن|کد\s+پستی|حاضرین|غایبین|مدعوین|دستور\s+جلسه)")
_SALUTE = re.compile(r"^(با\s+سلام(\s+و\s+(احترام|ادب)[^\s،,]*)?|سلام\s+علیکم|احتراما[ًٌء]?)[\s،,:.]*")
_LIST_START = re.compile(r"^\s*(\d{1,2}\s*[-.)]|[-•*]|(ماده|بند|تبصره)\s*\d{1,3}\s*[-–:.)]?)\s*")


def _numeric_share(text: str) -> float:
    toks = text.split()
    return sum(1 for t in toks if not re.search(r"[^\W\d_]", t)) / max(1, len(toks))


def fragmented(text: str) -> bool:
    """متن تکه‌تکه (PDF Word): بیشتر سطرها تکه‌های کوتاه یک جمله‌اند."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return len(lines) >= 8 and sum(len(ln.split()) <= 3 for ln in lines) > 0.4 * len(lines)


def _units(text: str, reflow: bool = False) -> list[str]:
    """تبدیل متن (با سطرهای شکسته‌ی PDF یا OCR) به جمله‌ها و بندها."""
    units = []
    if reflow or fragmented(text):  # تکه‌ها به هم وصل و فقط با نقطه جدا می‌شوند
        text = re.sub(r"\s+", " ", text)
    for para in re.split(r"\n\s*\n", text):
        buf: list[str] = []
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        for line in lines:
            if buf and (_LIST_START.match(line) or _HEADER_START.match(line)):
                units.append(" ".join(buf))
                buf = []
            # سطر کوتاه تنها = تیتر یا سرنامه؛ سطر کوتاه وسط بند = ادامه‌ی جمله‌ای که شکسته شده
            if not buf and len(line.split()) < 6:
                units.append(line)
                continue
            buf.append(line)
            if line.endswith((".", "؟", "!", ":", "؛")):
                units.append(" ".join(buf))
                buf = []
        if buf:
            units.append(" ".join(buf))
    out = []
    for u in units:
        out += [x.strip() for x in re.split(r"(?<=[.؟!؛])\s+(?=\D)", u) if x.strip()]
    if reflow or fragmented(text):
        # نامه‌های Word اغلب جمله را با نقطه تمام نمی‌کنند؛ بعد از فعل‌های پایانی هم جدا می‌شود
        ends = re.compile(r"(?<![آ-ی])(گردد|گردید|باشد|باشند|نمایید|فرمایید|نماید|فرماید|داریم|دارد|است|شود|شد|نمود|کنید)"
                          r"\s+(?=[آ-ی])(?!(?:که|تا|و|چون|زیرا|ولی|اما)(?![آ-ی]))")  # «است که …» ادامه‌ی جمله است
        out = [y.strip() for x in out for y in ends.sub(r"\1\n", x).split("\n") if y.strip()]
    return out


def summarize(text: str, n: int = 3, reflow: bool = False) -> list[str]:
    """خلاصه‌ی استخراجی: مهم‌ترین جمله‌ها به ترتیب متن (بدون سرنامه، جدول و متن خراب OCR)."""
    cands = []
    for i, u in enumerate(_units(text, reflow)):
        u = _SALUTE.sub("", _LIST_START.sub("", u)).strip(" -–:،,")
        words = u.split()
        if not 6 <= len(words) <= 80 or _HEADER_START.match(u) or _numeric_share(u) > 0.3:
            continue
        if textnorm.line_plausibility(u) < 0.6 or textnorm.quality(u)["score"] < 0.7:
            continue
        cands.append((i, u))
    if not cands:
        return []
    terms = [[textnorm.stem(t) for t in textnorm.tokens(u)
              if len(t) > 2 and t not in textnorm.STOPWORDS and t not in textnorm.EN_STOP and not t.isdigit()]
             for _, u in cands]
    tf: dict[str, int] = {}
    for ts in terms:
        for t in set(ts):
            tf[t] = tf.get(t, 0) + 1
    scored = []
    for k, ((i, u), ts) in enumerate(zip(cands, terms)):
        uniq = set(ts)
        base = sum(tf[t] for t in uniq) / (len(uniq) ** 0.6) if uniq else 0
        norm = textnorm.normalize(u)
        cue = min(3, sum(1 for c in _CUES if c in norm))
        scored.append((base * (1 + 0.35 * cue) * (1.25 - 0.4 * k / len(cands)), i, u))
    best = sorted(scored, reverse=True)[:n]
    return [u if len(u) <= 280 else u[:280].rsplit(" ", 1)[0] + "…" for _, _, u in sorted(best, key=lambda x: x[1])]


# ───────────────────────── گزارش ماهانه ─────────────────────────

# کد سند پروژه، مثل «CHGL-MG-MR-013» (گزارش) یا «CHGL-MG-INV-C-006» (صورت‌وضعیت)
_DOC_CODE = re.compile(r"(?<![A-Za-z0-9\-])([A-Z]{2,8}(?:-[A-Z0-9]{1,8}){2,6})(?![A-Za-z0-9\-])")
REPORT_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("done", "اقدامات انجام‌شده", r"(?:خلاصه\s+)?اقدامات\s+انجام\s*شده|(?:مهمترین|مهم\s*ترین)\s+فعالیت\s*های\s+انجام\s*شده"),
    ("problems", "مشکلات و موانع", r"مشکلات\s+و\s+موانع"),
    ("next", "برنامه‌ی ماه آینده", r"(?:پیش\s*بینی|برنامه)\s+فعالیت(?:\s*های)?\s+ماه\s+[آا]تی"),
)


def doc_code(text: str) -> tuple[int, str] | None:
    """کد سند (مثل CHGL-MG-MR-013)؛ ترجیحاً بعد از «شماره گزارش / شماره سند»، وگرنه پرتکرارترین کد."""
    codes = [(m.start(1), m.group(1)) for m in _DOC_CODE.finditer(text)]
    codes = [(p, c) for p, c in codes if re.search(r"\d", c)]
    if not codes:
        return None
    for p, c in codes:
        if re.search(LBL_NO + r"\s*(?:گزارش|سند|مدرک|صورت\s*وضعیت)?\s*[:：]?[^\n]{0,40}\n?[^\n]{0,30}$", text[max(0, p - 80): p]):
            return p, c
    best = max({c for _, c in codes}, key=lambda c: sum(1 for _, x in codes if x == c))
    return next((p, c) for p, c in codes if c == best)


def month_end(year: int, month: int) -> str:
    if month <= 6:
        day = 31
    elif month <= 11:
        day = 30
    else:  # اسفند: ۳۰ روزه فقط در سال کبیسه
        y2, m2, d2 = jalali.gregorian_to_jalali(*jalali.jalali_to_gregorian(year, 12, 30))
        day = 30 if (m2, d2) == (12, 30) else 29
    return f"{year}/{month:02d}/{day:02d}"


def report_period(text: str) -> tuple[int, str, str] | None:
    """دوره‌ی گزارش ماهانه («منتهی به شهریور ماه 1405») ← (موقعیت، «شهریور 1405»، تاریخ پایان ماه)."""
    cands = [(m.start(), m.group(1), int(m.group(2))) for m in
             re.finditer(rf"({_MONTHS}){W}(?:ماه)?{W}(?:سال{W})?(1[34]\d\d)(?!\d)", text)]
    if not cands:  # Excel: «منتهی به» و سال و نام ماه در سطرهای جدا
        mm = re.search(rf"منتهی{W}به[\s\S]{{0,400}}?({_MONTHS}){W}ماه", text)
        yy = re.search(r"منتهی\s*به\s*\n?\s*(1[34]\d\d)(?!\d)", text)
        if not (mm and yy):
            return None
        cands = [(mm.start(1), mm.group(1), int(yy.group(1)))]
    def weight(k: tuple[str, int]) -> int:  # «منتهی به …» قبل از آن نشانه‌ی قطعی دوره‌ی گزارش است
        return sum(1 + 10 * bool(re.search(rf"منتهی{W}به{W}$", text[max(0, c[0] - 20): c[0]]))
                   for c in cands if (c[1], c[2]) == k)
    key = max({(c[1], c[2]) for c in cands}, key=weight)
    pos = next(c[0] for c in cands if (c[1], c[2]) == key)
    month = jalali.MONTHS_FA.index(key[0]) + 1
    return pos, f"{key[0]} {key[1]}", month_end(key[1], month)


def _boilerplate(pages: list[tuple[int, str]]) -> set[str]:
    """کلمه‌های سطرهایی که در سربرگ بیشتر صفحه‌ها تکرار شده‌اند (نام پروژه، «شماره قرارداد»…)."""
    seen: dict[str, set[int]] = {}
    for no, t in pages:
        for ln in t.split("\n"):
            ln = " ".join(ln.split())
            if ln:
                seen.setdefault(ln, set()).add(no)
    need = max(3, len(pages) // 3)
    words: set[str] = set()
    for ln, ps in seen.items():
        if len(ps) >= need:
            words.update(textnorm.normalize(ln).split())
    return words


def _fix_chainage(s: str) -> str:
    # کیلومتراژ در خروجی Excel راست‌به‌چپ برعکس می‌آید: «000 + 15» یعنی «15+000»
    return re.sub(r"(?<![\d+])(\d{3})\s*\+\s*(\d{1,3})(?![\d+])", r"\2+\1", s)


def _section_items(page: str, title: re.Pattern, boiler: set[str]) -> list[str]:
    items: list[str] = []
    for ln in page.split("\n"):
        ln = " ".join(ln.split())
        if not ln:
            continue
        if re.fullmatch(r"[\d+\-/.,]+|(?:الی|تا|از)\s*[\d+]+", ln):
            # تکه‌ی کیلومتراژ سطر قبل («000» «+» «0» «الی000»…)؛ عدد تنها در غیر این صورت شماره‌ی ردیف است
            prev = items[-1] if items else ""
            if prev and (not ln[0].isdigit() or prev.endswith("+") or (ln == "+" and prev[-1].isdigit())):
                items[-1] = _fix_chainage(f"{prev} {ln}")
            continue
        norm = textnorm.normalize(ln)
        words = [w for w in norm.split() if re.search(r"[آ-ی]", w)]
        if len(words) < 3 or title.search(norm) or any(re.search(p, norm) for _, _, p in REPORT_SECTIONS):
            continue
        if sum(1 for w in words if w in boiler) >= 0.6 * len(words):
            continue
        if re.match(r"(ردیف|شرح|توضیحات|عنوان|برنامه زمانی|تاریخ)\b", norm) and len(words) <= 4:
            continue
        ln = re.sub(r"^\d{1,2}\s+(?=[آ-ی])", "", ln)  # شماره‌ی ردیف
        ln = re.sub(r"\s+(ادامه|جدید|اتمام)$", r" (\1)", ln)  # ستون توضیحات کنار شرح
        if ln not in items:
            items.append(ln)
    return items[:12]


def apply_report(info: dict[str, Any], rep: dict[str, Any]) -> None:
    """افزودن اطلاعات گزارش ماهانه به اطلاعات عمومی سند (تاریخ = پایان ماه گزارش؛ خلاصه = اقدامات و مشکلات)."""
    info.update(rep)
    if "report_no" in rep:
        info["doc_no"] = dict(rep["report_no"])
    per = rep.get("period")
    if per:
        # تاریخ گزارش = پایان ماه آن (تاریخ‌های برچسب‌دار گزارش، مثل «تاریخ شروع به کار»، تاریخ خود گزارش نیستند)
        info["doc_date"] = {"page": per["page"], "value": per["to"], "guessed": True}
        info["subject"] = {"page": per["page"], "value": f"گزارش ماهانه {per['value']}"}
    points = []
    for key, label in (("done", "اقدامات این ماه"), ("problems", "مشکلات"), ("next", "برنامه‌ی ماه آینده")):
        items = (rep.get(key) or {}).get("value") or []
        if items:
            points.append(f"{label}: " + "؛ ".join(items[:4]) + ("…" if len(items) > 4 else ""))
    if points:
        info["points"] = {"pos": 0, "value": points}
        info["gist"] = {"pos": 0, "value": " ".join(points[:2])}
        info.pop("table", None)


def report_fields(pages: list[tuple[int, str]]) -> dict[str, Any]:
    """گزارش ماهانه‌ی پیشرفت کار: کد و دوره‌ی گزارش و فهرست اقدامات، مشکلات و برنامه‌ی ماه بعد."""
    info: dict[str, Any] = {}
    pages = [(no, strip_header(t)) for no, t in pages]
    text = "\n\n".join(t for _, t in pages)
    page_at = []
    pos = 0
    for no, t in pages:
        page_at.append((pos, no))
        pos += len(t) + 2

    def page_of(p: int) -> int:
        return next((no for start, no in reversed(page_at) if start <= p), 1)

    code = doc_code(text[:20000])
    if code:
        info["report_no"] = {"page": page_of(code[0]), "value": code[1]}
    per = report_period(text)
    if per:
        info["period"] = {"page": page_of(per[0]), "value": per[1], "to": per[2]}
    boiler = _boilerplate(pages)
    for key, label, pattern in REPORT_SECTIONS:
        title = re.compile(pattern)
        for no, t in pages:
            norm = textnorm.normalize(t)
            if not title.search(norm) or "فهرست" in norm or sum(bool(re.search(p, norm)) for _, _, p in REPORT_SECTIONS) >= 2:
                continue  # فهرست مطالب همه‌ی عنوان‌ها را دارد
            items = _section_items(t, title, boiler)
            if items:
                info[key] = {"page": no, "value": items, "label": label}
                break
    for no, t in pages:  # جمع نفر-روز نیروی انسانی در ماه (و میانگین روزانه)
        m = re.search(r"((?:\d[\d,]*\s+){1,4})جمع\s+نفرات", t)
        if not m:
            continue
        nums = [int(x.replace(",", "")) for x in m.group(1).split()]
        total = max(nums)
        avg = next((x for x in nums if x and 25 <= total / x <= 32), None)
        info["manpower"] = {"page": no, "value": total, **({"avg": avg} if avg else {})}
        break
    return info


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
    # «شماره قرارداد»، «مبلغ قرارداد»… در سربرگ برچسب‌اند، نه عنوان سند
    top = re.sub(r" (?:شماره|مبلغ|تاریخ|مدت|نوع) (?:قرارداد|پیمان) ", " ", _norm_padded(head_raw[:260]))
    found = []
    for cat, ws in _HEAD_TITLES:
        hits = [top.find(f" {w}") for w in ws if _count(top, w)]
        if not hits:
            continue
        if cat == "contract" and letter_like and pages <= 3:
            continue  # «شماره قرارداد» در سرنامه‌ی یک نامه
        if cat in ("invoice", "report", "drawing", "financial") and salut:
            continue  # نامه‌ی ارسال صورت‌وضعیت/گزارش هنوز «نامه» است
        found.append((min(hits), cat))
    if found:  # اولین عنوان در سطرهای اول
        return min(found)[1]
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

    # شماره و تاریخ سند: کادر سربرگ ممکن است در متن OCR جلوتر یا عقب‌تر بیاید، پس بخش بزرگ‌تری جست‌وجو می‌شود
    top = text[:3000]
    nm = labeled_number(top, category in ("letter", "order"))
    if nm:
        info["doc_no"] = {"pos": nm[0], "value": nm[1]}
    dm = labeled_date(top)
    guessed = not dm
    if not dm:  # تاریخ بی‌برچسب: اولین تاریخی که به نامه‌ی دیگر یا مهلت اشاره نمی‌کند
        dm = next(((p, d) for p, d in _dates(top) if not _NOT_DOC_DATE.search(top[max(0, p - 16): p])), None)
    if dm:
        info["doc_date"] = {"pos": dm[0], "value": dm[1], "guessed": guessed}

    # موضوع
    body_top = strip_header(text)
    off = len(text) - len(body_top)
    sub = _line_after(body_top[:2500], r"موضوع" + SP + r"(?:قرارداد|پیمان|جلسه|نامه)?\s*(?:عبارت\s+است\s+از)?")
    if sub and category in ("letter", "order"):
        # موضوع نامه گاهی چند سطر است (در PDFهای Word تکه‌تکه): تا «با سلام» یا خط خالی ادامه دارد
        sm1 = re.search(r"موضوع" + SP + r"(?:نامه)?\s*[:\-–]?\s*", body_top[:2500])
        if sm1:
            seg = body_top[sm1.end(): sm1.end() + 450]
            cut = re.search(r"\n\s*\n|با\s+سلام|سلام\s+علیکم|احتراما|با\s+احترام|^\s*(?:جناب|سرکار)", seg, re.M)
            full_sub = " ".join(seg[: cut.start() if cut else len(seg)].split()).strip(" :.-–،")
            full_sub = re.sub(r"\s+([)»\"])", r"\1", re.sub(r"([(«])\s+", r"\1", full_sub))
            if len(full_sub) > len(sub[1]):
                sub = (sub[0], full_sub[:220] + ("…" if len(full_sub) > 220 else ""))
    if sub:
        info["subject"] = {"pos": sub[0] + off, "value": sub[1]}
    elif category in ("letter", "order"):
        # نامه‌ی بی‌برچسب «موضوع»: سطر پررنگ قبل از «با سلام» (بعد از نام و سمت گیرنده)
        sm0 = re.search(r"(?m)^\s*(?:با\s+سلام|سلام\s+علیکم|با\s+اهدای\s+سلام)", body_top[:2500])
        if sm0:
            prev = [ln.strip() for ln in body_top[:sm0.start()].split("\n") if ln.strip()]
            if prev:
                cand = prev[-1]
                words = cand.split()
                if 3 <= len(words) <= 25 and not re.search(r"محترم|جناب|سرکار|ریاست|مدیریت|مدیرعامل|مدیر\s*عامل", cand):
                    info["subject"] = {"pos": off + body_top.rfind(cand, 0, sm0.start()), "value": cand.strip(" :.-")[:160]}

    # گیرنده و فرستنده‌ی نامه
    rm = re.search(r"(?m)^\s*((?:جناب|سرکار|ریاست\s+محترم|مدیر\s*عامل\s+محترم|مدیریت\s+محترم|اداره\s+کل)[^\n]{3,120})",
                   body_top[:1500])
    if rm:
        to = rm.group(1).strip()
        # PDFهای Word: «جناب مهندس» و نام در سطرهای جدا
        for ln in body_top[rm.end(1): rm.end(1) + 120].split("\n")[1:4]:
            ln = ln.strip()
            if not ln:
                continue
            if len(to.split()) >= 4 or len(ln.split()) > 3 or re.match(r"(مدیر|موضوع|با\s|ریاست|احترام|سلام)", ln):
                break
            to += " " + ln
        info["to"] = {"pos": rm.start(1) + off, "value": to}
    for key, word in (("to", "به"), ("from", "از")):
        fm = re.search(rf"(?m)^\s*{word}\s*:\s*([^\n]{{3,100}})", body_top[:1500])
        if fm and key not in info:
            info[key] = {"pos": fm.start(1) + off, "value": fm.group(1).strip()}

    # خلاصه: مهم‌ترین جمله‌های سند (برای نامه‌ها، از متن بعد از سلام)
    # (فقط متن خود صفحه؛ سربرگِ OCRشده فقط برای شماره و تاریخ است)
    sm = re.search(r"(احتراما[ًٌء]?|با\s+سلام[^\n]{0,30}?(?:احترام|\n)|سلام\s+علیکم)", body_top[:2000])
    summary = [] if category == "invoice" else summarize(body_top[sm.end():] if sm and category in ("letter", "order") else body_top,
                        {"contract": 6, "amendment": 5, "report": 5, "minutes": 4, "invoice": 2}.get(category, 4),
                        reflow=fragmented(body_top))
    if summary:
        info["gist"] = {"pos": 0, "value": " ".join(summary[:3])}
        info["points"] = {"pos": 0, "value": summary}
    elif _numeric_share(text) > 0.35:
        info["table"] = {"pos": 0, "value": True}  # بیشتر جدول و عدد است (مثلاً صورت‌وضعیت)

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
            if (info.get("doc_date") or {}).get("guessed") and info["doc_date"]["value"] == vu[1]:
                del info["doc_date"]  # تاریخ سررسید، تاریخ صدور نیست
    if category == "invoice":
        info.update(invoice_fields(text, flat))
        to = (info.get("period") or {}).get("to")
        if to and (info.get("doc_date") or {}).get("guessed", True):  # تاریخ صورت‌وضعیت = پایان دوره
            info["doc_date"] = {"pos": info["period"]["pos"], "value": to, "guessed": True}
        if "net" in info:  # مبلغ اصلی صورت‌وضعیت = خالص قابل پرداخت (یا کارکرد این دوره)
            info["amount"] = {"pos": info["net"]["pos"], "value": info["net"]["value"], "context": "خالص قابل پرداخت"}
        elif "work_period" in info:
            info["amount"] = {"pos": info["work_period"]["pos"], "value": info["work_period"]["value"], "context": "کارکرد این دوره"}

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

    if category in ("letter", "order", "minutes"):
        refs = letter_refs(strip_header(text)[:6000], (info.get("doc_no") or {}).get("value"))
        if refs:
            info["refs"] = refs

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


def _doc_pages(conn, doc_id: int, page_texts: list[tuple[int, str]] | None) -> list[tuple[int, str]]:
    """متن صفحه‌های نگه‌داشته‌شده؛ ترجیحاً متن اصلی صفحه (با سطرهای دست‌نخورده)، وگرنه از روی بندهای نمایه‌شده."""
    if page_texts is None:
        row = conn.execute("SELECT head_text FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row and row["head_text"]:
            return [tuple(x) for x in json.loads(row["head_text"])]
        # اسناد قدیمی: از روی بندهای نمایه‌شده (سطرها به هم چسبیده‌اند)
        return [(r["page"], r["text"]) for r in conn.execute(
            "SELECT page, text FROM doc_chunks WHERE doc_id = ? AND page <= ? ORDER BY page, seq",
            (doc_id, ANALYZE_PAGES),
        )]
    keep, size = [], 0
    for page, raw in page_texts[: TEXT_PAGES + 2]:
        keep.append((page, raw[:20000]))
        size += len(raw)
        if size > TEXT_CHARS:
            break
    conn.execute("UPDATE documents SET head_text = ? WHERE id = ?", (json.dumps(keep, ensure_ascii=False), doc_id))
    return keep


def _join_pages(page_texts: list[tuple[int, str]]) -> tuple[str, list[int], list[int]]:
    parts, starts, pages, pos = [], [], [], 0
    for page, raw in page_texts:
        t = _prep(raw) + "\n\n"
        starts.append(pos)
        pages.append(page)
        parts.append(t)
        pos += len(t)
    return "".join(parts), starts, pages


def _analysis_pages(all_pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    head = [p for p in all_pages if p[0] <= ANALYZE_PAGES]
    return head + [p for p in all_pages if p[0] > ANALYZE_PAGES][-2:]


def analyze(doc_id: int, page_texts: list[tuple[int, str]] | None = None,
            sheets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    with db.connect() as conn:
        d = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not d:
            raise db.NotFound("سند پیدا نشد")
        all_pages = _doc_pages(conn, doc_id, page_texts)
        text, starts, pages = _join_pages(_analysis_pages(all_pages))
        if sheets is None:
            sheets = json.loads(d["sheets"] or "[]")
        else:
            conn.execute("UPDATE documents SET sheets = ? WHERE id = ?", (json.dumps(sheets, ensure_ascii=False), doc_id))
        edited = set(json.loads(d["edited"] or "[]"))
        category = d["category"] if "category" in edited else classify(d["title"], text, d["kind"], d["pages"] or 1)
        if "category" not in edited and category in ("other", "photo"):
            # دسته‌ی نامطمئن: عنوانی که کاربر قبلاً دسته‌اش را تعیین کرده (سربرگ مشترک نامه‌ها را عوض نمی‌کند)
            category = learning.category_for(text) or category
        info = extract(text, category) if text.strip() else {}
        if category == "invoice" and sheets:
            apply_sheets(info, sheets)
        if category == "report" and all_pages:
            apply_report(info, report_fields([(p, _prep(t)) for p, t in all_pages]))
        if category in ("invoice", "report") and "report_no" not in info and (code := doc_code(text[:6000])):
            info["doc_no"] = {"pos": code[0], "value": code[1]}  # کد سند، نه شماره‌ی قرارداد در سربرگ
        if text.strip():
            learning.apply(text, info, category)  # الگوهایی که از اصلاح‌های کاربر یاد گرفته شده
        if category == "invoice" and "invoice_no" not in info:  # «وضعیت شماره ۳.pdf»
            tm = re.search(r"(\d{1,3})(?!\d)", _prep(d["title"]))
            if tm:
                info["invoice_no"] = {"pos": 0, "value": tm.group(1)}
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


def upgrade() -> None:
    """پس از بهبود خواندن و تحلیل، اسناد موجود یک بار (برای هر نسخه) دوباره خوانده یا تحلیل می‌شوند."""
    with db.connect() as conn:
        if db.kv_get(conn, "archive_version") == ARCHIVE_VERSION:
            return
        # اسناد تا ۴۰ صفحه یک بار کامل دوباره خوانده می‌شوند (OCR بهتر + متن صفحه برای خلاصه)؛
        # اسناد بزرگ‌تر فقط دوباره تحلیل می‌شوند تا پردازنده‌ی سرور مدت طولانی مشغول نماند
        n = conn.execute("UPDATE documents SET status='queued', pages_done=0, attempts=0 "
                         "WHERE status IN ('ready','error') AND pages <= 40").rowcount
        if n:
            log.info("%d سند برای خواندن دوباره در صف قرار گرفت", n)
        conn.execute("UPDATE documents SET analyzed_at = NULL")
        db.kv_set(conn, "archive_version", ARCHIVE_VERSION)


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


def update_meta(doc_id: int, data: dict[str, Any]) -> list[str]:
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
            return []
        old = conn.execute("SELECT doc_no, doc_date, category FROM documents WHERE id = ?", (doc_id,)).fetchone()
        conn.execute(
            f"UPDATE documents SET {''.join(f'{k}=?, ' for k in sets)}edited=?, updated_at=? WHERE id = ?",
            (*sets.values(), json.dumps(sorted(edited)), db.now_str(), doc_id),
        )
    # یادگیری: فقط مقدارهایی که واقعاً عوض شده‌اند
    changes = {k: sets[k] for k in ("doc_no", "doc_date", "category") if sets.get(k) and sets[k] != old[k]}
    if not changes:
        return []
    text = "\n\n".join(t for p, t in head_pages_for(doc_id) if p <= 3)[:20000]
    learned = learning.learn(text, changes, sets.get("category") or old["category"]) if text.strip() else []
    if learned:
        learning.reapply_async(exclude=doc_id)
    return learned


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


def doc_brief(d: dict) -> list[dict[str, Any]]:
    """خلاصه‌ی ساخت‌یافته‌ی یک سند (برچسب، مقدار) بسته به نوعش؛ برای کارت خلاصه‌ی سند."""
    info, cat = d["info"], d.get("category") or "other"
    rows: list[dict[str, Any]] = []

    def add(label: str, key: str | None = None, value: Any = None, money: bool = False, **extra: Any) -> None:
        item = info.get(key) if key else None
        v = value if value is not None else (item or {}).get("value")
        if v not in (None, "", []):
            rows.append({"label": label, "value": v, "money": money, "page": (item or {}).get("page"), **extra})

    add("نوع سند", value=CATEGORIES.get(cat))
    if cat == "invoice":
        add("نوع صورت‌وضعیت", "invoice_kind")
        add("شماره‌ی صورت‌وضعیت", "invoice_no")
        add("دوره‌ی کارکرد", "period")
        for key, label, _ in INVOICE_ROWS:
            add(label, key, money=True, computed=bool((info.get(key) or {}).get("computed")))
        for key, label in SHEET_ROWS:
            add(label, key, money=True)
        if info.get("progress_fin"):
            add("پیشرفت مالی (کارکرد ÷ مبلغ قرارداد)", value=f"{info['progress_fin']['value']:g}٪")
        if info.get("sheet_fixes"):
            rows.append({"label": "🔧 اصلاح خودکار", "value": "؛ ".join(
                f"{f['row']} ({ {'prev': 'تا دوره قبل', 'period': 'طی دوره', 'total': 'تاکنون'}.get(f['field'], f['field']) }): "
                f"{f['read']:,} ← {f['fixed']:,}" for f in info["sheet_fixes"]["value"])})
        add("درصد پیشرفت", value=f"{info['progress']['value']:g}٪" if info.get("progress") else None)
        if info.get("work_check"):
            rows.append({"label": "⚠️ کنترل جمع", "value": "کارکرد قبلی + این دوره با جمع کارکرد نمی‌خواند؛ اعداد را با سند چک کنید"})
    elif cat == "report":
        add("شماره‌ی گزارش", value=d.get("doc_no"))
        add("دوره‌ی گزارش", "period")
        add("تاریخ", value=d.get("doc_date"))
        mp = info.get("manpower")
        if mp:
            add("نیروی انسانی", "manpower", value=f"{mp['value']:,} نفر-روز در ماه" + (f" (میانگین {mp['avg']} نفر در روز)" if mp.get("avg") else ""))
        for key, label in (("employer", "کارفرما"), ("contractor", "پیمانکار"), ("consultant", "مشاور")):
            add(label, key)
    else:
        add("شماره", value=d.get("doc_no"))
        add("تاریخ", value=d.get("doc_date"))
        add("موضوع", value=d.get("subject"))
        add("گیرنده", "to")
        add("فرستنده", "from")
        for key, label in (("employer", "کارفرما"), ("contractor", "پیمانکار"), ("consultant", "مشاور")):
            add(label, key)
        add("مبلغ", "amount", money=True)
        add("مدت", "duration")
        add("تاریخ شروع", "start_date")
        add("تاریخ پایان", "end_date")
        add("اعتبار تا", "valid_until")
        dl = info.get("deadline")
        if dl:
            add("مهلت", value=dl.get("date") or dl["value"])
    return rows


def _doc_row(r) -> dict[str, Any]:
    d = dict(r)
    d.pop("head_text", None)
    d["info"] = json.loads(d.get("info") or "{}")
    d["edited"] = json.loads(d.get("edited") or "[]")
    d["date"] = d.get("doc_date") or (d.get("created_at") or "")[:10]
    d["brief"] = doc_brief(d)
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
            "SELECT p.id, p.name, p.status, COUNT(d.id) AS docs, SUM(d.category IN ('letter', 'order')) AS letters, "
            "MAX(COALESCE(d.doc_date, substr(d.created_at,1,10))) AS last "
            "FROM projects p LEFT JOIN documents d ON d.project_id = p.id GROUP BY p.id "
            "ORDER BY CASE p.status WHEN 'active' THEN 0 WHEN 'tender' THEN 1 WHEN 'on_hold' THEN 2 ELSE 3 END, p.id DESC"
        ).fetchall()
        loose = conn.execute("SELECT COUNT(*) FROM documents WHERE project_id IS NULL").fetchone()[0]
        loose_letters = conn.execute("SELECT COUNT(*) FROM documents WHERE project_id IS NULL "
                                     "AND category IN ('letter', 'order')").fetchone()[0]
        cur = conn.execute("SELECT id, title, pages, pages_done, updated_at FROM documents WHERE status='processing' LIMIT 1").fetchone()
        queued = conn.execute("SELECT COUNT(*) FROM documents WHERE status='queued'").fetchone()[0]
    return {"projects": [{**dict(r), "letters": r["letters"] or 0} for r in rows], "unassigned": loose,
            "unassigned_letters": loose_letters, "categories": CATEGORIES,
            "queue": {"current": dict(cur) if cur else None, "queued": queued}}


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

    def inv_key(d: dict) -> tuple:
        n = (d["info"].get("invoice_no") or {}).get("value")
        return (int(n) if n and str(n).isdigit() else 10**6, d["date"], d["id"])

    invoices = []
    for d in sorted(by_cat.get("invoice", []), key=inv_key):
        i = d["info"]
        invoices.append({
            "date": d["date"], "no": (i.get("invoice_no") or {}).get("value"),
            "kind_fa": (i.get("invoice_kind") or {}).get("value"), "period": (i.get("period") or {}).get("value"),
            **{k: (i.get(k) or {}).get("value") for k, _, _ in INVOICE_ROWS},
            **{k: (i.get(k) or {}).get("value") for k, _ in SHEET_ROWS},
            "progress": (i.get("progress") or {}).get("value"), "check_failed": bool(i.get("work_check")),
            **_src(d, i.get("work_total") or i.get("net") or i.get("amount")),
        })
    last_invoice = invoices[-1] if invoices else None
    inv_contract = next((x for x in reversed(invoices) if x.get("contract_total")), None)
    if inv_contract and not any(f["key"] == "contract_amount" for f in facts):
        # مبلغ قرارداد در خود قرارداد پیدا نشد؛ از جدول صورت‌وضعیت
        facts.append({"key": "contract_amount", "label": "مبلغ قرارداد (طبق صورت‌وضعیت)", "value": inv_contract["contract_total"],
                      "money": True, **{k: inv_contract[k] for k in ("doc_id", "doc_title", "page", "kind")}})
    contract_amount = next((f["value"] for f in facts if f["key"] == "contract_amount"), None) or (project or {}).get("contract_amount")
    for x in invoices:
        x["total_pct"] = round(100 * x["work_total"] / contract_amount, 1) if x.get("work_total") and contract_amount else None

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

    letter_rows = _letter_rows(docs, today)
    reports = sorted(by_cat.get("report", []), key=lambda d: d["date"])
    fdict = {f["key"]: f["value"] for f in facts}
    for k in ("start_date", "end_date", "contract_amount"):
        fdict.setdefault(k, (project or {}).get(k))
    alerts = followup.alerts(fdict, invoices, guarantees, deadlines, letter_rows, reports, today)
    chart = followup.progress_chart(fdict, invoices, today)

    narrative = story.build(project, ready, contract, {f["key"]: f["value"] for f in facts},
                            deadlines, guarantees, invoices, today, _src) if project else []

    timeline = [{
        "id": d["id"], "date": d["date"], "category": d.get("category") or "other", "title": d["title"],
        "doc_no": d.get("doc_no"), "subject": d.get("subject"), "gist": (d["info"].get("gist") or {}).get("value"),
    } for d in ready[:15]]

    return {
        "project": project, "documents": docs, "counts": counts, "categories": CATEGORIES,
        "facts": facts, "amendments": amendments, "last_invoice": last_invoice, "invoices": invoices,
        "guarantees": guarantees, "deadlines": deadlines[:12], "minutes": minutes, "suggestions": suggestions,
        "timeline": timeline, "story": narrative, "today": today, "alerts": alerts, "chart": chart,
        "unanswered": [x for x in letter_rows if x.get("waiting") is not None][:20],
        "pending": sum(1 for d in docs if d["status"] in ("queued", "processing")),
        "last_update": max((d.get("analyzed_at") or "" for d in docs), default="") or None,
    }


def to_invoice_record(doc_id: int) -> dict[str, Any]:
    """ثبت صورت‌وضعیتِ بایگانی‌شده در بخش «صورت‌وضعیت‌ها» (برای پیگیری تأیید و پرداخت)."""
    d = next((x for x in list_docs_by_id(doc_id)), None)
    if not d:
        raise db.NotFound("سند پیدا نشد")
    if d.get("category") != "invoice":
        raise db.ValidationError("این سند صورت‌وضعیت نیست")
    if not d.get("project_id"):
        raise db.ValidationError("اول پروژه‌ی این سند را مشخص کنید")
    i = d["info"]
    val = lambda k: (i.get(k) or {}).get("value")  # noqa: E731
    gross = (val("work_period") or 0) + (val("adjustment") or 0) or val("net")
    if not gross:
        raise db.ValidationError("مبلغ کارکرد این دوره از سند خوانده نشد؛ صورت‌وضعیت را دستی ثبت کنید")
    number = str(val("invoice_no") or d["title"])
    period = i.get("period") or {}
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM invoices WHERE project_id = ? AND number = ?", (d["project_id"], number)).fetchone():
            raise db.ValidationError(f"صورت‌وضعیت شماره‌ی {number} برای این پروژه قبلاً ثبت شده است")
        return db.create(conn, "invoices", {
            "project_id": d["project_id"], "number": number,
            "kind": {"موقت": "interim", "قطعی": "final", "تعدیل": "adjustment"}.get(val("invoice_kind"), "interim"),
            "period_start": period.get("from"), "period_end": period.get("to"),
            "gross_amount": gross, "deductions": val("deductions") or 0, "net_amount": val("net"),
            "status": "submitted", "submit_date": d.get("doc_date"),
            "notes": f"از بایگانی: {d['title']}",
        })


def list_docs_by_id(doc_id: int) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchall()
    return [_doc_row(r) for r in rows]


# ───────────────────────── دفتر نامه‌ها ─────────────────────────

LETTER_CATS = ("letter", "order")
_ASK = ("خواهشمند", "لازم است", "مقرر", "ابلاغ", "درخواست", "اعلام می", "دستور", "موظف", "ضروری", "اقدام")
# جمله‌هایی که نکته‌ی اصلی نامه‌اند (مشکل، تأخیر، مطالبات…) بر جمله‌ی تعارفی «به پیوست … ارسال می‌گردد» مقدم‌اند
_NOTABLE = ("شایان", "لازم به ذکر", "تاخیر", "تأخیر", "کمبود", "مشکل", "عدم", "توقف", "مطالبات", "خسارت", "تمدید",
            "فوری", "اخطار", "تذکر", "جریمه", "متاسفانه", "متأسفانه", "مغایرت", "اعتراض")
_BOILERPLATE = re.compile(r"^(?:با\s+احترام\s+)?(?:بدین\s*وسیله\s+)?(?:به\s+پیوست|ضمن\s+ارسال)")


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip("،,:؛") + "…"


def letter_summary(d: dict, limit: int = 230) -> str:
    """خلاصه‌ی حدوداً دوخطی نامه: جمله‌ی زمینه (اولین جمله‌ی مهم) و جمله‌ی درخواست/دستور آن."""
    info = d.get("info") or {}
    pts = (info.get("points") or {}).get("value") or ([info["gist"]["value"]] if info.get("gist") else [])
    pts = [p for p in pts if p]
    if not pts:
        return ""
    pts = [_pretty(p) for p in pts]
    first = pts[0]
    norm = [textnorm.normalize(p) for p in pts]
    ask = next((p for p, n in zip(pts[1:], norm[1:]) if any(textnorm.normalize(c) in n for c in _NOTABLE)), None) \
        or next((p for p, n in zip(pts[1:], norm[1:]) if any(c in n for c in _ASK)), None)
    if ask is None and len(pts) > 1 and (len(first) < limit * 0.6 or _BOILERPLATE.match(first)):
        ask = pts[1]
    if ask is None:
        return _cut(first, limit)
    # جمله‌ی تعارفی اول کوتاه می‌شود تا نکته‌ی اصلی جا شود
    head = _cut(first, max(70, limit - len(ask) - 1) if not _BOILERPLATE.match(first) else 95)
    return _cut(f"{head} {ask}", limit + (40 if _BOILERPLATE.match(first) else 0))


def _pretty(t: str) -> str:
    """فاصله‌های متن PDF: «وCG2» ← «و CG2»، «" احداث» ← «"احداث»."""
    t = textnorm._GLUED.sub(" ", t)
    t = re.sub(r'(["«(])\s+', r"\1", t)
    t = re.sub(r'\s+([")»،,.])', r"\1", t)
    return " ".join(t.split())


def letters_register(project_id: int | None) -> dict[str, Any]:
    """نامه‌ها و ابلاغیه‌های یک پروژه، تازه‌ترین اول، با خلاصه‌ی دوخطی."""
    project = None
    if project_id:
        with db.connect() as conn:
            project = db.get(conn, "projects", project_id)
    today = db.today_str()
    letters = _letter_rows(list_docs(project_id), today)
    return {"project": project, "letters": letters, "today": today,
            "pending": sum(1 for x in letters if x["status"] in ("queued", "processing")),
            "unanswered": sum(1 for x in letters if x.get("waiting") is not None)}


def _letter_rows(docs: list[dict], today: str) -> list[dict[str, Any]]:
    letters, infos = [], {}
    for d in docs:
        if d.get("category") not in LETTER_CATS:
            continue
        info = d["info"]
        due = None
        dl = info.get("deadline")
        if dl:
            due = dl.get("date")
            if not due and dl.get("days") and d.get("doc_date"):
                try:
                    due = jalali.add_days(d["doc_date"], dl["days"])
                except ValueError:
                    due = None
        letters.append({
            "id": d["id"], "title": d["title"], "kind": d["kind"], "category": d["category"], "status": d["status"],
            "project_id": d["project_id"], "doc_date": d.get("doc_date"),
            "pages": d["pages"], "pages_done": d["pages_done"], "ocr": bool(d.get("ocr_pages")),
            "date": d["date"], "dated": bool(d.get("doc_date")), "doc_no": d.get("doc_no"),
            "to": (info.get("to") or {}).get("value"), "from": (info.get("from") or {}).get("value"),
            "subject": d.get("subject"), "summary": letter_summary(d),
            "deadline": due, "late": bool(due and due < today), "error": d.get("error"),
        })
        infos[d["id"]] = info
    followup.link_letters(letters, infos, number_key, today)
    return letters


def letters_csv(project_id: int | None) -> tuple[str, bytes]:
    """دفتر نامه‌ها برای اکسل (CSV با BOM تا فارسی درست نمایش داده شود)."""
    import csv
    import io

    reg = letters_register(project_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ردیف", "تاریخ", "شماره", "نوع", "گیرنده", "فرستنده", "موضوع", "خلاصه", "مهلت", "فایل"])
    for i, x in enumerate(sorted(reg["letters"], key=lambda x: (x["date"], x["id"])), 1):
        w.writerow([i, x["date"], x["doc_no"] or "", CATEGORIES[x["category"]], x["to"] or "", x["from"] or "",
                    x["subject"] or "", x["summary"], x["deadline"] or "", x["title"]])
    name = f"نامه‌ها - {(reg['project'] or {}).get('name', 'بدون پروژه')}.csv"
    return name, ("\ufeff" + buf.getvalue()).encode("utf-8")


def head_pages_for(doc_id: int) -> list[tuple[int, str]]:
    """متن صفحه‌های اول یک سند (ذخیره‌شده، یا از بندهای نمایه‌شده برای اسناد قدیمی)."""
    with db.connect() as conn:
        row = conn.execute("SELECT head_text FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row and row["head_text"]:
            return [tuple(x) for x in json.loads(row["head_text"])]
        pages: dict[int, list[str]] = {}
        for r in conn.execute("SELECT page, text FROM doc_chunks WHERE doc_id = ? AND page <= ? ORDER BY page, seq",
                              (doc_id, ANALYZE_PAGES)):
            pages.setdefault(r["page"], []).append(r["text"])
    return [(p, "\n".join(t)) for p, t in sorted(pages.items())]


def with_header(pages: list[tuple[int, str]], header: str) -> list[tuple[int, str]]:
    """افزودن متن سربرگ (OCR جداگانه‌ی بالای صفحه‌ی اول) به ابتدای صفحه‌ی اول."""
    pages = [(p, strip_header(t) if p == 1 else t) for p, t in pages]
    header = header.strip()
    if not header:
        return pages
    block = f"{HEADER_START}{header}{HEADER_END}"
    if pages and pages[0][0] == 1:
        return [(1, f"{block}\n\n{pages[0][1]}")] + list(pages[1:])
    return [(1, block)] + list(pages)


def strip_header(text: str) -> str:
    """حذف سربرگ قبلی (برای خواندن دوباره‌ی آن)؛ سربرگ‌های نسخه‌ی قبل علامت پایان نداشتند."""
    if not text.startswith(HEADER_START):
        return text
    end = text.find(HEADER_END)
    if end != -1:
        return text[end + len(HEADER_END):].lstrip("\n")
    rest = text[len(HEADER_START):]
    i = rest.find("\n\n")
    return rest[i + 2:] if i != -1 else ""
