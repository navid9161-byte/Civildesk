"""یکسان‌سازی متن فارسی، تشخیص متن خراب/وارونه و ابزارهای جستجو.

PDFهای فارسی معمولاً یکی از این مشکلات را دارند:
- حروف «شکل‌دار» (Presentation Forms) به جای حروف استاندارد ← با NFKC درست می‌شود
- ترتیب دیداری (برعکس) به جای ترتیب منطقی ← با شمارش کلمات پرتکرار تشخیص و برگردانده می‌شود
- نگاشت خراب قلم (حروف لاتین/سیریلیک عجیب به جای حروف فارسی) ← امتیاز کیفیت پایین، صفحه OCR می‌شود
"""
from __future__ import annotations

import re
import unicodedata

ZWNJ = "‌"

# کلمات بسیار پرتکرار فارسی (برای سنجش کیفیت و جهت متن؛ و حذف از پرسش جستجو)
STOPWORDS = frozenset(
    """و در به از که این را با است برای آن یا می ها های هر تا بر اگر باید شود شده نیز بین پس
    کند کرد شد دارد هم ای یک دو باشد باشند نمی نه چه چون همه روی زیر بعد قبل طبق مورد موارد
    بوده شوند گردد میشود میباشد می‌باشد می‌شود بايد اين""".split()
)
QUERY_STOPWORDS = STOPWORDS | frozenset(
    """چقدر چقدره چند چیست چیه کدام کدوم چطور چگونه آیا ایا لطفا بگو بگید میخوام می‌خواهم است؟ هست
    چی کجا کی چرا مقدار میزان""".split()
)

_CHAR_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "ؤ": "و",
    "أ": "ا", "إ": "ا", "ٱ": "ا", "ٲ": "ا",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "ـ": None,  # کشیده
    " ": " ", "‏": None, "‎": None, "‪": None, "‫": None, "‬": None,
    "‭": None, "‮": None, "﻿": None,
})
_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭ]")
_PERSIAN_LETTER = re.compile(r"[ء-غف-يپچژکگیآ]")
_PERSIAN_CHARS = set("آابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیءأإؤئةيكۀ")
_TOKEN_RE = re.compile(r"[\w‌]+", re.UNICODE)
_LTR_RUN = re.compile(r"[0-9A-Za-z۰-۹٠-٩][0-9A-Za-z۰-۹٠-٩.,/:%\-]*[0-9A-Za-z۰-۹٠-٩]|[0-9A-Za-z۰-۹٠-٩]")


def clean_display(text: str) -> str:
    """متن برای نمایش: حروف استاندارد، بدون کنترل‌های جهت؛ ارقام و نیم‌فاصله حفظ می‌شوند."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({
        "ي": "ی", "ى": "ی", "ك": "ک", "ـ": None, " ": " ", "‏": None, "‎": None,
        "﻿": None, "‪": None, "‫": None, "‬": None, "‭": None, "‮": None,
    }))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize(text: str) -> str:
    """متن یکسان‌شده برای نمایه‌سازی و جستجو (نه برای نمایش)."""
    text = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP)
    text = text.replace("آ", "ا")
    text = _DIACRITICS.sub("", text)
    text = text.replace(ZWNJ, " ").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


# ───────────────────────── کیفیت و جهت متن ─────────────────────────


_FA_DIGIT_SET = set("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")


def _classify(tok: str) -> str:
    letters = [c for c in tok if c.isalpha()]
    has_fa_digit = any(c in _FA_DIGIT_SET for c in tok)
    has_en_digit = any(c in "0123456789" for c in tok)
    if has_fa_digit and has_en_digit:
        return "bad"  # ترکیب ارقام فارسی و لاتین در یک کلمه: نشانه‌ی OCR خراب
    if not letters:
        return "num"
    if (has_fa_digit and all(c.isascii() for c in letters)) or (
        has_en_digit and len(letters) > 1 and all(c in _PERSIAN_CHARS for c in letters)
    ):
        return "bad"
    if all(c in _PERSIAN_CHARS for c in letters):
        return "fa"
    if all(c.isascii() for c in letters):
        return "en"
    return "bad"


def quality(text: str) -> dict[str, float]:
    """امتیاز کیفیت متن استخراج‌شده (۰ تا ۱) به‌همراه آمار آن."""
    norm = unicodedata.normalize("NFKC", text).translate(_CHAR_MAP).replace(ZWNJ, " ")
    toks = [t for t in re.split(r"[^\w]+", norm) if t]
    # ارقام خالص در متن عادی عادی‌اند، اما در OCR متن نامربوط ارقام تبدیل‌شده زیادند
    kinds = [_classify(t) for t in toks]
    raw_kinds = [_classify(t) for t in re.split(r"\s+", text) if t.strip()]
    letter_toks = [t for t, k in zip(toks, kinds) if k != "num"]
    n = len(letter_toks)
    if n == 0:
        return {"score": 0.0, "words": 0, "bad": 0.0, "stop": 0.0, "stop_rev": 0.0, "fa": 0.0}
    bad = min(1.0, (sum(1 for k in kinds if k == "bad") + sum(1 for k in raw_kinds if k == "bad")) / (2 * n))
    num_ratio = sum(1 for k in kinds if k == "num") / len(kinds)
    fa_toks = [t for t, k in zip(toks, kinds) if k == "fa"]
    fa_ratio = len(fa_toks) / n
    stop = sum(1 for t in fa_toks if t in STOPWORDS) / max(1, len(fa_toks))
    stop_rev = sum(1 for t in fa_toks if t[::-1] in STOPWORDS and t not in STOPWORDS) / max(1, len(fa_toks))
    # میانگین طول کلمه‌ی فارسی؛ متن‌های خراب اغلب کلمات یک‌حرفی زیاد دارند
    single = sum(1 for t in fa_toks if len(t) == 1 and t not in ("و",)) / max(1, len(fa_toks))
    score = (1 - bad) * (1 - min(single, 0.5))
    if num_ratio > 0.4 and len(kinds) >= 10:  # بیشتر «کلمات» فقط عدد و نشانه‌اند
        score *= 1 - (num_ratio - 0.4)
    if len(fa_toks) >= 8:
        # متن فارسی سالم معمولاً ۱۵ تا ۳۵ درصد کلمه‌ی پرتکرار دارد
        score *= 0.4 + 0.6 * min(1.0, stop / 0.12)
    return {"score": round(score, 3), "words": n, "bad": round(bad, 3), "stop": round(stop, 3),
            "stop_rev": round(stop_rev, 3), "fa": round(fa_ratio, 3)}


def _reverse_line(line: str) -> str:
    """برعکس کردن یک سطر با ترتیب دیداری، بدون برعکس شدن اعداد و کلمات لاتین."""
    rev = line[::-1]
    return _LTR_RUN.sub(lambda m: m.group(0)[::-1], rev)


def fix_direction(text: str) -> tuple[str, bool]:
    """اگر متن با ترتیب دیداری (برعکس) استخراج شده باشد، آن را درست می‌کند."""
    text = unicodedata.normalize("NFKC", text)
    q = quality(text)
    if q["stop_rev"] > max(0.06, q["stop"] * 2):
        return "\n".join(_reverse_line(line) for line in text.split("\n")), True
    return text, False


def is_persian(text: str) -> bool:
    return len(_PERSIAN_LETTER.findall(text)) > len(re.findall(r"[A-Za-z]", text))


# ───────────────────────── پرسش جستجو ─────────────────────────

_SUFFIXES = ("هایی", "های", "ترین", "ها", "تر", "ان", "ات", "ی")


def stem(tok: str) -> str:
    """ریشه‌یابی بسیار سبک (فقط پسوندهای رایج) برای جستجوی پیشوندی."""
    for suf in _SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def query_terms(query: str) -> list[str]:
    seen, out = set(), []
    for t in tokens(query):
        if t in QUERY_STOPWORDS or (len(t) < 2 and not t.isdigit()):
            continue
        s = stem(t)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fts_query(query: str) -> str | None:
    """ساخت پرسش FTS5: همه‌ی واژه‌ها با OR و جستجوی پیشوندی."""
    terms = query_terms(query)
    if not terms:
        return None
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in terms)


def highlight_terms(query: str) -> list[str]:
    return query_terms(query)
