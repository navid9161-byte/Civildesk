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

from . import db, jalali, story, textnorm

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
ARCHIVE_VERSION = "3"  # با تغییر آن، همه‌ی اسناد یک بار دوباره تحلیل می‌شوند

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
        info["invoice_no"] = {"pos": im.start(), "value": im.group(1)}

    # دوره‌ی کارکرد
    for pm in re.finditer(r"(?:دوره|از" + W + r"تاریخ|کارکرد" + W + r"از|مدت" + W + r"کارکرد)", flat):
        ds = [(p + pm.end(), d) for p, d in _dates(flat[pm.end(): pm.end() + 90])]
        if len(ds) >= 2 and ds[0][1] < ds[1][1]:
            info["period"] = {"pos": pm.start(), "value": f"{ds[0][1]} تا {ds[1][1]}", "from": ds[0][1], "to": ds[1][1]}
            break
    if "period" not in info:
        mm = re.search(rf"(?:ماه|دوره|کارکرد)[^\n\d]{{0,12}}?({_MONTHS})(?:" + W + rf"(?:تا|الی|لغایت|-)" + W +
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


def _units(text: str) -> list[str]:
    """تبدیل متن (با سطرهای شکسته‌ی PDF یا OCR) به جمله‌ها و بندها."""
    units = []
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
    return out


def summarize(text: str, n: int = 3) -> list[str]:
    """خلاصه‌ی استخراجی: مهم‌ترین جمله‌ها به ترتیب متن (بدون سرنامه، جدول و متن خراب OCR)."""
    cands = []
    for i, u in enumerate(_units(text)):
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
    dm = _date_after(head, r"(?<!تا )(?<!از )تاریخ" + SP + r"(?:نامه|قرارداد|جلسه)?", 40)
    guessed = not dm
    if not dm:
        ds = _dates(head)
        dm = ds[0] if ds else None
    if dm:
        info["doc_date"] = {"pos": dm[0], "value": dm[1], "guessed": guessed}

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

    # خلاصه: مهم‌ترین جمله‌های سند (برای نامه‌ها، از متن بعد از سلام)
    sm = re.search(r"(احتراما[ًٌء]?|با\s+سلام[^\n]{0,30}?(?:احترام|\n)|سلام\s+علیکم)", text[:2000])
    summary = [] if category == "invoice" else summarize(text[sm.end():] if sm and category in ("letter", "order") else text,
                        {"contract": 6, "amendment": 5, "report": 5, "minutes": 4, "invoice": 2}.get(category, 4))
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
        row = conn.execute("SELECT head_text FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row and row["head_text"]:
            page_texts = json.loads(row["head_text"])
        else:  # اسناد قدیمی: از روی بندهای نمایه‌شده (سطرها به هم چسبیده‌اند)
            page_texts = [(r["page"], r["text"]) for r in conn.execute(
                "SELECT page, text FROM doc_chunks WHERE doc_id = ? AND page <= ? ORDER BY page, seq",
                (doc_id, ANALYZE_PAGES),
            )]
    else:
        keep, size = [], 0
        for page, raw in page_texts[: ANALYZE_PAGES + 2]:
            keep.append((page, raw[:20000]))
            size += len(raw)
            if size > 80000:
                break
        page_texts = keep
        conn.execute("UPDATE documents SET head_text = ? WHERE id = ?", (json.dumps(keep, ensure_ascii=False), doc_id))
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
        n = conn.execute("UPDATE documents SET status='queued' WHERE status IN ('ready','error') AND pages <= 40").rowcount
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
        add("درصد پیشرفت", value=f"{info['progress']['value']:g}٪" if info.get("progress") else None)
        if info.get("work_check"):
            rows.append({"label": "⚠️ کنترل جمع", "value": "کارکرد قبلی + این دوره با جمع کارکرد نمی‌خواند؛ اعداد را با سند چک کنید"})
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
            "progress": (i.get("progress") or {}).get("value"), "check_failed": bool(i.get("work_check")),
            **_src(d, i.get("work_total") or i.get("net") or i.get("amount")),
        })
    last_invoice = invoices[-1] if invoices else None
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
        "timeline": timeline, "story": narrative, "today": today,
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
