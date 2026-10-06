"""خواندن جدول‌های اسکن‌شده (مثل برگ خلاصه‌ی مالی صورت‌وضعیت) سلول به سلول.

OCR معمولی روی جدول خوب کار نمی‌کند: خطوط جدول با حروف قاطی می‌شوند و معلوم نیست هر عدد مال کدام ستون
و ردیف است. اینجا:
1. صفحه صاف (deskew) می‌شود،
2. خطوط افقی و عمودی جدول پیدا و شبکه‌ی سلول‌ها ساخته می‌شود (سلول‌های ادغام‌شده هم تشخیص داده می‌شوند)،
3. متن هر سلول جداگانه خوانده می‌شود (همه‌ی سلول‌ها در یک تصویر و یک بار اجرای Tesseract)،
4. خروجی: فهرست سلول‌ها با شماره‌ی ردیف و ستون، برای تفسیر در archive.
فقط numpy و Pillow لازم است.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Any

import numpy as np
from PIL import Image

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# ───────────────────────── پیش‌پردازش تصویر ─────────────────────────


def _binary(gray: np.ndarray) -> np.ndarray:
    """سیاه/سفید با آستانه‌ی Otsu."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_all = np.dot(np.arange(256), hist)
    w_b = sum_b = 0.0
    best, thr = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0 or w_b == total:
            continue
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / (total - w_b)
        between = w_b * (total - w_b) * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return gray < min(thr, 200)


def deskew(img: Image.Image, max_angle: float = 2.0) -> Image.Image:
    """صاف کردن صفحه‌ی کج اسکن‌شده با بیشینه کردن تیزی نیم‌رخ افقی (خطوط جدول و سطرها)."""
    small = img.convert("L")
    scale = 1000 / max(small.size)
    if scale < 1:
        small = small.resize((int(small.width * scale), int(small.height * scale)))
    best, best_a = -1.0, 0.0
    for a in np.arange(-max_angle, max_angle + 0.01, 0.1):
        rot = np.asarray(small.rotate(float(a), fillcolor=255, resample=Image.BILINEAR))
        prof = (rot < 128).sum(axis=1).astype(float)
        score = float(np.sum(np.diff(prof) ** 2))
        if score > best:
            best, best_a = score, float(a)
    return img.rotate(best_a, fillcolor=255, resample=Image.BICUBIC) if abs(best_a) >= 0.05 else img


def _runs_mask(b: np.ndarray, length: int, axis: int) -> np.ndarray:
    """پیکسل‌هایی که جزو یک رشته‌ی پیوسته‌ی سیاه به طول ≥ length در جهت axis هستند (تکه‌تکه، برای حافظه‌ی کم)."""
    a = b if axis == 1 else b.T
    if a.shape[0] > 400:
        out = np.vstack([_runs_mask(a[i: i + 400], length, 1) for i in range(0, a.shape[0], 400)])
        return out if axis == 1 else out.T
    c = np.cumsum(np.pad(a.astype(np.int32), ((0, 0), (1, 0))), axis=1)
    full = (c[:, length:] - c[:, :-length]) == length  # پنجره‌هایی که کامل سیاه‌اند
    out = np.zeros_like(a, dtype=bool)
    # هر پنجره‌ی کامل، همه‌ی پیکسل‌هایش را علامت می‌زند
    idx = np.nonzero(full)
    if idx[0].size:
        mark = np.zeros((a.shape[0], a.shape[1] + 1), dtype=np.int32)
        np.add.at(mark, (idx[0], idx[1]), 1)
        np.add.at(mark, (idx[0], idx[1] + length), -1)
        out = np.cumsum(mark, axis=1)[:, :-1] > 0
    return out if axis == 1 else out.T


def _line_positions(mask: np.ndarray, axis: int, min_frac: float) -> list[int]:
    """مختصات خطوط (مرکز هر خوشه) از نیم‌رخ ماسک خط."""
    prof = mask.sum(axis=1 if axis == 0 else 0)
    span = mask.shape[1 if axis == 0 else 0]
    rows = np.nonzero(prof >= min_frac * span)[0]
    out, start = [], None
    for i, r in enumerate(rows):
        if start is None:
            start = r
        if i + 1 == len(rows) or rows[i + 1] > r + 3:
            out.append(int((start + r) // 2))
            start = None
    return out


# ───────────────────────── شبکه‌ی سلول‌ها ─────────────────────────


def find_cells(gray: np.ndarray) -> list[dict[str, Any]]:
    """سلول‌های جدول (ادغام‌شده‌ها یکی می‌شوند): r0, r1, c0, c1 (اندیس شبکه) و x0, y0, x1, y1 (پیکسل)."""
    b = _binary(gray)
    h, w = b.shape
    hmask = _runs_mask(b, max(30, w // 40), axis=1)
    vmask = _runs_mask(b, max(20, h // 60), axis=0)
    ys = _line_positions(hmask, 0, 0.12)
    xs = _line_positions(vmask, 1, 0.04)
    if len(ys) < 3 or len(xs) < 3:
        return []
    # فقط خطوط داخل محدوده‌ی جدول
    def has_h(y: int, x0: int, x1: int) -> bool:
        band = hmask[max(0, y - 3): y + 4, x0 + 2: max(x0 + 3, x1 - 2)].any(axis=0)
        return band.mean() > 0.6 if band.size else False

    def has_v(x: int, y0: int, y1: int) -> bool:
        band = vmask[y0 + 2: max(y0 + 3, y1 - 2), max(0, x - 3): x + 4].any(axis=1)
        return band.mean() > 0.6 if band.size else False

    nr, nc = len(ys) - 1, len(xs) - 1
    parent = list(range(nr * nc))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for r in range(nr):
        for c in range(nc):
            if c + 1 < nc and not has_v(xs[c + 1], ys[r], ys[r + 1]):
                parent[find(r * nc + c)] = find(r * nc + c + 1)
            if r + 1 < nr and not has_h(ys[r + 1], xs[c], xs[c + 1]):
                parent[find(r * nc + c)] = find((r + 1) * nc + c)
    groups: dict[int, list[tuple[int, int]]] = {}
    for r in range(nr):
        for c in range(nc):
            groups.setdefault(find(r * nc + c), []).append((r, c))
    cells = []
    for members in groups.values():
        r0, r1 = min(m[0] for m in members), max(m[0] for m in members)
        c0, c1 = min(m[1] for m in members), max(m[1] for m in members)
        # ناحیه‌ای که خطی دورش نیست (بیرون جدول) سلول حساب نمی‌شود
        if (r1 - r0 + 1) * (c1 - c0 + 1) > 0.5 * nr * nc:
            continue
        cells.append({"r0": r0, "r1": r1, "c0": c0, "c1": c1,
                      "x0": xs[c0], "y0": ys[r0], "x1": xs[c1 + 1], "y1": ys[r1 + 1]})
    return cells


# ───────────────────────── خواندن متن سلول‌ها ─────────────────────────


def _crop(gray: np.ndarray, c: dict, pad: int = 4) -> np.ndarray | None:
    sub = gray[c["y0"] + pad: c["y1"] - pad, c["x0"] + pad: c["x1"] - pad]
    if sub.size == 0:
        return None
    ink = sub < 140
    if ink.sum() < 12:
        return None
    ys, xs = np.nonzero(ink)
    return sub[max(0, ys.min() - 3): ys.max() + 4, max(0, xs.min() - 3): xs.max() + 4]


def ocr_cells(gray: np.ndarray, cells: list[dict], lang: str = "fas") -> None:
    """متن همه‌ی سلول‌ها با یک بار اجرای Tesseract: سلول‌ها زیر هم در یک تصویر چیده می‌شوند."""
    crops = []
    for c in cells:
        c["text"] = ""
        im = _crop(gray, c)
        if im is not None:
            crops.append((c, im))
    if not crops:
        return
    target_h = 34  # ارتفاع مناسب حروف برای Tesseract
    pieces, y = [], 20
    for c, im in crops:
        lines = max(1, round(im.shape[0] / 40)) if im.shape[0] > 60 else 1
        scale = min(3.0, max(1.0, target_h * lines / max(im.shape[0], 1)))
        pim = Image.fromarray(im).resize((int(im.shape[1] * scale), int(im.shape[0] * scale)), Image.BICUBIC)
        pieces.append((c, pim, y))
        y += pim.height + 28
    width = max(p.width for _, p, _ in pieces) + 80
    canvas = Image.new("L", (width, y + 20), 255)
    for _, p, top in pieces:
        canvas.paste(p, (width - 40 - p.width, top))  # راست‌چین
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        canvas.save(tmp.name)
    try:
        res = subprocess.run(["tesseract", tmp.name, "stdout", "-l", lang, "--psm", "6", "tsv"],
                             capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(tmp.name)
    words = []
    for row in res.stdout.splitlines()[1:]:
        f = row.split("\t")
        if len(f) >= 12 and f[0] == "5" and f[11].strip() and float(f[10]) >= 0:
            x, yy, w, hh = (int(v) for v in f[6:10])
            words.append((yy + hh / 2, -(x + w), f[11].strip()))  # راست به چپ
    for c, p, top in pieces:
        mine = sorted((x, t) for yc, x, t in words if top - 8 <= yc <= top + p.height + 8)
        c["text"] = " ".join(t for _, t in mine)


def read_table(img: Image.Image, lang: str = "fas") -> list[dict[str, Any]]:
    """تصویر صفحه → سلول‌های جدول با متن."""
    img = deskew(img.convert("L"))
    gray = np.asarray(img)
    cells = find_cells(gray)
    if cells:
        ocr_cells(gray, cells, lang)
    return cells


# ───────────────────────── اعداد ─────────────────────────


def parse_amount(text: str) -> int | None:
    """«۱,۵۰۱,۸۱۷,۵۲۶,۴۹۷» → 1501817526497. ارقام فارسی، جداکننده‌های , ٬ ، و صفرهایی که نقطه خوانده شده‌اند."""
    t = text.translate(_DIGITS).replace("٬", ",").replace("،", ",").replace("'", ",").replace(" ", "")
    t = t.strip("()-–+")
    if not t or "%" in t or "٪" in t or "/" in t:
        return None
    if re.fullmatch(r"0+", t):
        return 0
    cands = [t]
    if "." in t and "," in t:
        cands = [t.replace(".", "0")]  # صفر فارسی (۰) گاهی نقطه خوانده می‌شود
    elif "." in t:
        cands = [t.replace(".", ","), t.replace(".", "0")]
    for c in cands:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", c) or re.fullmatch(r"\d{4,}", c):
            return int(c.replace(",", ""))
    return None


def parse_percent(text: str) -> float | None:
    t = text.translate(_DIGITS).replace("٫", ".").replace("/", ".").replace(" ", "")
    m = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*[%٪]|[%٪]\s*(\d{1,3}(?:\.\d{1,2})?)", t)
    if not m:
        return None
    v = float(m.group(1) or m.group(2))
    return v if v <= 100 else None


# ───────────────────────── تفسیر برگ مالی صورت‌وضعیت ─────────────────────────

ROLES = ("پیمانکار", "مشاور", "کارفرما")
_NORM = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "‌": " ", "ـ": "", "ۀ": "ه"})


def _n(t: str) -> str:
    return " ".join((t or "").translate(_NORM).translate(_DIGITS).split())


def _covering(cells: list[dict], r: int, c: int) -> dict | None:
    for x in cells:
        if x["r0"] <= r <= x["r1"] and x["c0"] <= c <= x["c1"]:
            return x
    return None


def _role(t: str) -> str | None:
    t = _n(t).replace("ز", "ر")  # «کارفزما» در OCR
    for r in ROLES:
        if r in t or (r == "کارفرما" and "رفرما" in t) or (r == "پیمانکار" and "مانکار" in t):
            return r
    return None


def parse_sheet(cells: list[dict], gray: np.ndarray | None = None) -> dict[str, Any] | None:
    """جدول «برگ مالی» صورت‌وضعیت ← مبالغ قرارداد، تا دوره‌ی قبل، طی دوره و تاکنون، برای هر بخش و هر رکن.

    ستون‌ها از روی سرستون‌ها («پیشرفت تجمعی کار تا دوره قبل»، «پیشرفت کار طی دوره»، «… تاکنون»،
    «مبلغ قرارداد») و ستون مبلغ هر گروه از زیرسرستون «مبلغ (ریال)» پیدا می‌شود؛ ردیف‌ها از ستون «ارکان»
    (پیمانکار/مشاور/کارفرما) و ستون «بخش/شرح» (سلول ادغام‌شده = یک بخش).
    """
    if not cells:
        return None
    groups: dict[str, tuple[int, int, int]] = {}  # نام ← (c0, c1, r1)
    head_r1 = -1
    role_col = label_col = None
    for x in cells:
        t = _n(x["text"])
        if not t:
            continue
        key = None
        if "قبل" in t and ("دوره" in t or "تجمعی" in t or "کارکرد" in t):
            key = "prev"
        elif ("طی دوره" in t or "این دوره" in t or "دوره جاری" in t) and "قبل" not in t:
            key = "period"
        elif "تاکنون" in t or "تا کنون" in t or ("تجمعی" in t and "قبل" not in t):
            key = "total"
        elif ("پیمان" in t or "قرارداد" in t) and "مبلغ" in t and "درصد" not in t:
            key = "contract"
        if key and (key not in groups or x["r0"] < groups[key][2]):
            groups[key] = (x["c0"], x["c1"], x["r1"])
            head_r1 = max(head_r1, x["r1"])
        if "ارکان" in t:
            role_col, head_r1 = x["c0"], max(head_r1, x["r1"])
        if ("بخش" in t or "شرح" in t or t == "عنوان") and len(t) <= 14 and "درصد" not in t:
            label_col, head_r1 = x["c0"], max(head_r1, x["r1"])
    if len(groups) < 2:
        return None
    # زیرسرستون‌ها («مبلغ (ریال)»، «درصد …») هم جزو سرستون‌اند
    for x in cells:
        t = _n(x["text"])
        if x["r0"] <= head_r1 + 1 and ("ریال" in t or "مبلغ" in t or "درصد" in t) and not any(ch.isdigit() for ch in t):
            head_r1 = max(head_r1, x["r1"])
    amount_col: dict[str, int] = {}
    for key, (c0, c1, _) in groups.items():
        subs = [x for x in cells if x["r0"] <= head_r1 and c0 <= x["c0"] and x["c1"] <= c1 and (x["c0"], x["c1"]) != (c0, c1)
                and ("مبلغ" in _n(x["text"]) or "ریال" in _n(x["text"]))]
        amount_col[key] = subs[0]["c0"] if subs else c0 if c0 == c1 else -1
    data = [x for x in cells if x["r0"] > head_r1]
    if not data:
        return None
    nrows = max(x["r1"] for x in data)
    if role_col is None:
        cols: dict[int, int] = {}
        for x in data:
            if _role(x["text"]):
                cols[x["c0"]] = cols.get(x["c0"], 0) + 1
        role_col = max(cols, key=cols.get) if cols else None
    # ستون مبلغ گروه‌هایی که زیرسرستون نداشتند: ستونی از گروه که بیشترین عدد بزرگ را دارد
    for key, (c0, c1, _) in groups.items():
        if amount_col[key] == -1:
            counts = {c: sum(1 for x in data if x["c0"] == c and len(re.sub(r"\D", "", _n(x["text"]))) >= 6) for c in range(c0, c1 + 1)}
            amount_col[key] = max(counts, key=counts.get)
    if label_col is None:  # ستونی (غیر از ارکان) که بیشترین سلول متنی فارسی را دارد
        counts = {}
        for x in data:
            if x["c0"] != role_col and re.search(r"[آ-ی]{3}", _n(x["text"])) and not _role(x["text"]):
                counts[x["c0"]] = counts.get(x["c0"], 0) + 1
        label_col = max(counts, key=counts.get) if counts else None

    from . import digits

    cache: dict[int, tuple[int | None, float]] = {}

    def amount(x: dict | None) -> tuple[int | None, float]:
        if x is None:
            return None, 0.0
        if id(x) not in cache:
            val, conf = None, 0.0
            if gray is not None and digits.available():
                crop = _crop(gray, x)
                if crop is not None:
                    txt, conf = digits.read_number_conf(crop, amount=True)
                    val = parse_amount(txt)
            if val is None:
                val = parse_amount(_n(x["text"]).replace(" ", ""))
                conf = 99.0
            cache[id(x)] = (val, conf)
        return cache[id(x)]

    rows, sections = [], []
    for r in range(head_r1 + 1, nrows + 1):
        lab = _covering(data, r, label_col) if label_col is not None else None
        rc = _covering(data, r, role_col) if role_col is not None else None
        label = " ".join(_n(lab["text"]).split()).strip(" \"'.،:-|") if lab else ""
        role = _role(rc["text"]) if rc else None
        is_sum = any("جمع" in _n(x["text"]) for x in data if x["r0"] <= r <= x["r1"] and x["r1"] - x["r0"] < 2)
        vals: dict[str, Any] = {}
        confs: dict[str, float] = {}
        for key in ("prev", "period", "total"):
            if key in amount_col:
                cell = _covering(data, r, amount_col[key])
                if cell is not None and cell["r0"] == r:
                    v, cf = amount(cell)
                    if v is not None:
                        vals[key], confs[key] = v, cf
        if "contract" in amount_col:
            cell = _covering(data, r, amount_col["contract"])
            if cell is not None and cell["r0"] == r:
                v, _ = amount(cell)
                if v is not None:
                    vals["contract"] = v
        row_conf = confs
        if not vals and not is_sum:
            continue
        row = {"section": label or None, "role": role, "sum": is_sum, **vals, "_conf": row_conf}
        rows.append(row)
        if label and not is_sum and (not sections or sections[-1]["name"] != label):
            sections.append({"name": label, "contract": vals.get("contract")})
        elif sections and "contract" in vals and sections[-1]["contract"] is None and not is_sum:
            sections[-1]["contract"] = vals["contract"]
    if not rows:
        return None
    total_row = next((x for x in rows if x["sum"]), None)
    if total_row is None:  # «جمع» خوانده نشد: ردیف آخرِ بی‌رکن که مبلغ قراردادش جمع بقیه است
        last = rows[-1]
        parts = [x.get("contract", 0) for x in rows[:-1]]
        if last["role"] is None and last.get("contract") and abs(sum(parts) - last["contract"]) <= last["contract"] * 0.002:
            last["sum"], total_row = True, last
            if sections and sections[-1]["name"] == last["section"]:
                sections.pop()
    corrected = _reconcile(rows, total_row)
    corrected += _fix_sum_row(rows, total_row)
    for x in rows:
        x.pop("_conf", None)
    by_role: dict[str, dict[str, int]] = {}
    for x in rows:
        if x["role"] and not x["sum"]:
            agg = by_role.setdefault(x["role"], {})
            for k in ("prev", "period", "total"):
                if k in x:
                    agg[k] = agg.get(k, 0) + x[k]
    return {"groups": sorted(groups), "rows": rows, "sections": sections, "sum": {k: v for k, v in (total_row or {}).items() if k in ("contract", "prev", "period", "total")},
            "by_role": by_role, "corrected": corrected}


def read_sheet(img: Image.Image, lang: str = "fas") -> dict[str, Any] | None:
    """تصویر صفحه ← برگ مالی تفسیرشده (یا None اگر جدول مالی نبود)."""
    img = deskew(img.convert("L"))
    gray = np.asarray(img)
    cells = find_cells(gray)
    if len(cells) < 8:
        return None
    ocr_cells(gray, cells, lang)
    return parse_sheet(cells, gray)


def _digit_diff(a: int, b: int) -> int:
    sa, sb = str(a), str(b)
    return sum(x != y for x, y in zip(sa, sb)) + abs(len(sa) - len(sb)) if b >= 0 else 99


def _sum_ok(rows: list[dict], total_row: dict | None, key: str) -> bool | None:
    """آیا مقدار ردیف «جمع» در ستون key با جمع بخش‌ها (از هر بخش یکی از ارکان) می‌خواند؟"""
    if not total_row or key not in total_row:
        return None
    options: dict[str, set[int]] = {}
    for x in rows:
        if not x["sum"]:
            options.setdefault(x["section"] or "", {0}).add(x.get(key, 0))
    sums = {0}
    for opts in options.values():
        sums = {a + b for a in sums for b in opts}
        if len(sums) > 20000:
            return None
    target = total_row[key]
    return any(abs(v - target) <= max(2, target * 1e-6) for v in sums)


def _reconcile(rows: list[dict], total_row: dict | None) -> list[dict]:
    """کنترل «قبل + این دوره = تاکنون» در هر ردیف. اگر نخواند، از سه عدد آن که اصلاحش (۱) جمع ستون‌ها را
    با ردیف «جمع» جور کند، (۲) کمترین رقم را عوض کند و (۳) نامطمئن‌تر خوانده شده باشد، اصلاح می‌شود."""
    fixes = []
    for x in rows:
        if not all(k in x for k in ("prev", "period", "total")) or not x["total"]:
            continue
        if abs(x["prev"] + x["period"] - x["total"]) <= max(2, x["total"] * 1e-6):
            continue
        expected = {"total": x["prev"] + x["period"], "prev": x["total"] - x["period"], "period": x["total"] - x["prev"]}
        best = None
        for key, val in expected.items():
            if val < 0:
                continue
            old = x[key]
            x[key] = val
            ok = sum(1 for k in ("prev", "period", "total") if _sum_ok(rows, total_row, k))
            x[key] = old
            score = (-ok, _digit_diff(old, val), -x["_conf"].get(key, 0))
            if best is None or score < best[0]:
                best = (score, key, val)
        if best and best[0][1] <= 3:
            _, key, val = best
            fixes.append({"row": " · ".join(filter(None, (x["section"], x["role"]))), "field": key, "read": x[key], "fixed": val})
            x[key] = val
        else:
            x["check_failed"] = True
    return fixes


def _fix_sum_row(rows: list[dict], total_row: dict | None) -> list[dict]:
    """اگر عدد ردیف «جمع» با هیچ ترکیبی از بخش‌ها نخواند ولی با یک یا دو رقم تفاوت بخواند، عدد جمع اصلاح می‌شود."""
    fixes = []
    if not total_row:
        return fixes
    for key in ("prev", "period", "total"):
        if _sum_ok(rows, total_row, key) is not False:
            continue
        options: dict[str, set[int]] = {}
        for x in rows:
            if not x["sum"]:
                options.setdefault(x["section"] or "", {0}).add(x.get(key, 0))
        sums = {0}
        for opts in options.values():
            sums = {a + b for a in sums for b in opts}
        target = total_row[key]
        cand = min(sums, key=lambda v: (_digit_diff(target, v), abs(v - target)))
        if _digit_diff(target, cand) <= 2 and len(str(cand)) == len(str(target)):
            fixes.append({"row": "جمع", "field": key, "read": target, "fixed": cand})
            total_row[key] = cand
    return fixes


def looks_financial(text: str) -> bool:
    """متن صفحه شبیه برگ مالی/خلاصه‌ی صورت‌وضعیت است؟"""
    t = _n(text)
    return ("برگ" in t and "مالی" in t) or ("پیشرفت" in t and "مبلغ" in t) or ("کارکرد" in t and "مبلغ" in t) \
        or ("خلاصه" in t and "وضعیت" in t)


def sheet_title(text: str) -> str | None:
    for line in (text or "").split("\n"):
        ln = _n(line)
        if "برگ" in ln and len(ln) < 60:
            return ln.strip(" :-|")
    return None
