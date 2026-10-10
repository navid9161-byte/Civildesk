"""کتابخانه‌ی اسناد: دریافت فایل، استخراج متن (با OCR در صورت نیاز)، نمایه‌سازی و جستجوی ترکیبی.

مسیر پردازش هر صفحه‌ی PDF:
1. استخراج لایه‌ی متنی ← درست کردن حروف شکل‌دار و متن برعکس
2. سنجش کیفیت؛ اگر صفحه اسکن‌شده، کم‌متن یا خراب بود ← OCR فارسی/انگلیسی با Tesseract
3. انتخاب بهترین نسخه، تقسیم به بندهای کوتاه با شماره‌ی صفحه
4. نمایه‌ی کلیدواژه‌ای (FTS5) + بردار معنایی (در صورت وجود مدل)

جستجو: ترکیب رتبه‌ی کلیدواژه‌ای و معنایی (Reciprocal Rank Fusion).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, BinaryIO, Iterator

import numpy as np

from . import archive, db, embedder, tables, textnorm
from .config import settings

log = logging.getLogger(__name__)

# Tesseract به‌طور پیش‌فرض چند رشته (thread) می‌سازد؛ روی سرور با نیم پردازنده این رشته‌ها با هم رقابت
# می‌کنند و کار چند برابر کند می‌شود و حافظه‌ی بیشتری می‌گیرد
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
MAX_ATTEMPTS = 3  # اگر پردازش یک صفحه این‌قدر بار نیمه‌کاره ماند (مثلاً کمبود حافظه)، آن صفحه رد می‌شود
MAX_SHEET_PAGES = 6


def release_memory() -> None:
    """برگرداندن حافظه‌ی آزادشده به سیستم بعد از هر صفحه (مهم روی سرور کم‌حافظه).

    PyMuPDF تصویرهای اسکن‌شده‌ی هر صفحه را در حافظه‌ی نهان خودش نگه می‌دارد (تا ۲۵۶ مگابایت)؛ روی سرور
    ۵۱۲ مگابایتی همین باعث پر شدن حافظه، بسته شدن برنامه و شروع دوباره‌ی پردازش از اول می‌شد.
    """
    import gc

    try:
        import pymupdf

        pymupdf.TOOLS.store_shrink(100)
    except Exception:
        pass
    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
TEXT_EXT = {".txt", ".md"}
DOCX_EXT = {".docx"}
ALLOWED_EXT = PDF_EXT | IMAGE_EXT | TEXT_EXT | DOCX_EXT

CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
OCR_DPI = 300
OCR_MAX_PIXELS = 3500  # بیشترین طول تصویر برای OCR (برای نقشه‌های بزرگ؛ و حافظه‌ی کمتر)
GOOD_SCORE = 0.97


def docs_dir() -> Path:
    d = Path(os.getenv("CIVILDESK_DOCS_DIR") or (settings.db_path.parent / "docs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── OCR ─────────────────────────


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None and os.getenv("CIVILDESK_NO_OCR") is None


def _tesseract(path: str, langs: str, psm: str = "3") -> str:
    res = subprocess.run(
        ["tesseract", path, "stdout", "-l", langs, "--psm", psm],
        capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:300] or "خطای OCR")
    return res.stdout


def _tesseract_lines(path: str, langs: str) -> list[dict[str, Any]]:
    """سطرهای OCR با کادر و میانگین اطمینان کلمات (خروجی TSV تسرکت)."""
    res = subprocess.run(
        ["tesseract", path, "stdout", "-l", langs, "--psm", "3", "tsv"],
        capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:300] or "خطای OCR")
    lines: dict[tuple, dict[str, Any]] = {}
    for row in res.stdout.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or c[0] != "5" or not c[11].strip() or float(c[10]) < 0:
            continue
        key = (int(c[2]), int(c[3]), int(c[4]))
        x, y, w, h = (int(v) for v in c[6:10])
        ln = lines.setdefault(key, {"words": [], "confs": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h})
        ln["words"].append(c[11].strip())
        ln["confs"].append(float(c[10]))
        ln["x0"], ln["y0"] = min(ln["x0"], x), min(ln["y0"], y)
        ln["x1"], ln["y1"] = max(ln["x1"], x + w), max(ln["y1"], y + h)
    out = []
    for ln in lines.values():
        text = " ".join(ln["words"])
        ln["text"] = text
        ln["score"] = (sum(ln["confs"]) / len(ln["confs"]) / 100) * textnorm.line_plausibility(text)
        out.append(ln)
    return out


def _overlap(a: dict, b: dict) -> bool:
    dy = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    dx = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    return dy > 0.5 * min(a["y1"] - a["y0"], b["y1"] - b["y0"]) and dx > 0.3 * min(a["x1"] - a["x0"], b["x1"] - b["x0"])


def merge_ocr_lines(primary: list[dict], other: list[dict]) -> str:
    """ادغام سطر به سطر دو خروجی OCR (مثلاً فارسی و انگلیسی): برای هر ناحیه، سطر باورپذیرتر.

    برای اسناد دوزبانه (ستون فارسی کنار ستون انگلیسی) که هیچ‌کدام از مدل‌ها به‌تنهایی کل صفحه را درست نمی‌خواند.
    """
    out, used = [], set()
    for ln in primary:
        rivals = [i for i, o in enumerate(other) if _overlap(ln, o)]
        best = max((other[i]["score"] for i in rivals), default=-1)
        if rivals and best > ln["score"] + 0.05:
            out += [other[i]["text"] for i in rivals if i not in used]
            used.update(rivals)
        else:
            out.append(ln["text"])
    # سطرهایی که فقط مدل دوم دیده (و باورپذیرند)، در جای عمودی خودشان
    extra = [(o["y0"], o["text"]) for i, o in enumerate(other)
             if i not in used and o["score"] >= 0.5 and not any(_overlap(o, p) for p in primary)]
    return "\n".join(out + [t for _, t in sorted(extra)])


def ocr_image(path: str) -> str:
    """OCR تطبیقی: اول فارسی؛ اگر بخشی از صفحه درست خوانده نشد، انگلیسی هم خوانده و سطر به سطر ادغام می‌شود.

    مدل ترکیبی fas+eng کلمات فارسی را گاهی لاتین می‌خواند، برای همین استفاده نمی‌شود.
    """
    first = os.getenv("CIVILDESK_OCR_LANGS", "fas")
    primary = _tesseract_lines(path, first)
    text = "\n".join(ln["text"] for ln in primary)
    qd = textnorm.quality(text)
    weak = sum(1 for ln in primary if ln["score"] < 0.45)
    if qd["score"] >= 0.85 and qd["words"] >= 15 and weak <= max(1, len(primary) // 10):
        return text
    if first == "eng":
        return text
    merged = merge_ocr_lines(primary, _tesseract_lines(path, "eng"))
    mq = textnorm.quality(merged)
    rate = lambda q: q["score"] * min(1.0, 0.5 + q["words"] / 40)  # noqa: E731 — متن معنادار بیشتر = بهتر
    return merged if rate(mq) >= rate(qd) else text


def _ocr_pdf_page(page) -> str:
    import pymupdf

    longest = max(page.rect.width, page.rect.height) / 72  # اینچ
    dpi = min(OCR_DPI, int(OCR_MAX_PIXELS / max(longest, 1)))
    pix = page.get_pixmap(dpi=max(dpi, 120), colorspace=pymupdf.csGRAY)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        pix.save(tmp.name)
    try:
        return ocr_image(tmp.name)
    finally:
        os.unlink(tmp.name)


def header_text(path: Path, kind: str) -> str:
    """OCR جداگانه‌ی بالای صفحه‌ی اول (کادر «شماره / تاریخ / پیوست»).

    در PDFهای اتوماسیون اداری سربرگ و شماره و تاریخ اغلب تصویر است و در متن PDF نیست؛ در اسکن‌ها هم OCR کل
    صفحه این کادر کوچک را گاهی جا می‌اندازد. اینجا فقط ۳۰٪ بالای صفحه با دو حالت چیدمان خوانده می‌شود.
    """
    from PIL import Image

    if kind == "image":
        img = Image.open(path).convert("L")
    else:
        import pymupdf

        with pymupdf.open(path) as pdf:
            page = pdf[0]
            longest = max(page.rect.width, page.rect.height) / 72
            pix = page.get_pixmap(dpi=min(300, int(OCR_MAX_PIXELS / max(longest, 1))), colorspace=pymupdf.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            del pix
    w, h = img.size
    crop = img.crop((0, 0, w, int(h * 0.3)))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        crop.save(tmp.name)
    try:
        return "\n".join(textnorm.clean_display(_tesseract(tmp.name, "fas", psm)) for psm in ("11", "6"))
    finally:
        os.unlink(tmp.name)
        del img, crop
        release_memory()


def backfill_headers() -> int:
    """یک بار برای اسناد قبلی: نامه‌هایی که شماره یا تاریخشان پیدا نشده، سربرگشان جداگانه خوانده و دوباره تحلیل می‌شود."""
    if not ocr_available():
        return 0
    with db.connect() as conn:
        if db.kv_get(conn, "header_backfill") == "1":
            return 0
        rows = conn.execute(
            "SELECT id, path, kind FROM documents WHERE status='ready' AND kind IN ('pdf','image') "
            "AND (doc_date IS NULL OR (doc_no IS NULL AND category IN ('letter','order'))) "
            "AND COALESCE(edited, '') NOT LIKE '%doc_date%' ORDER BY id"
        ).fetchall()
    n = 0
    for r in rows:
        if worker.stopping:
            return n
        try:
            archive.analyze(r["id"], archive.with_header(archive.head_pages_for(r["id"]), header_text(Path(r["path"]), r["kind"])))
            n += 1
        except Exception:
            log.exception("خواندن سربرگ سند %s ناموفق بود", r["id"])
    with db.connect() as conn:
        db.kv_set(conn, "header_backfill", "1")
    if n:
        log.info("سربرگ %d سند دوباره خوانده شد", n)
    return n


def read_sheet_page(path: Path, kind: str, page_no: int) -> dict[str, Any] | None:
    """جدول مالی یک صفحه (صورت‌وضعیت) سلول به سلول؛ None اگر جدولی پیدا نشد."""
    from PIL import Image

    if kind == "image":
        img = Image.open(path)
    else:
        import pymupdf

        with pymupdf.open(path) as pdf:
            page = pdf[page_no - 1]
            longest = max(page.rect.width, page.rect.height) / 72
            pix = page.get_pixmap(dpi=min(250, int(3300 / max(longest, 1))), colorspace=pymupdf.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            del pix
    try:
        return tables.read_sheet(img)
    finally:
        del img
        release_memory()


# ───────────────────────── استخراج متن ─────────────────────────


def _best_text(text_layer: str, ocr_fn) -> tuple[str, str]:
    """انتخاب بهترین متن بین لایه‌ی متنی PDF و OCR. خروجی: (متن، روش)."""
    fixed, reversed_ = textnorm.fix_direction(text_layer)
    q = textnorm.quality(fixed)
    method = "fixed" if reversed_ else "text"
    if q["words"] >= 15 and q["score"] >= GOOD_SCORE and q["bad"] <= 0.02:
        return fixed, method
    if ocr_fn is None:
        return fixed, (method if q["words"] >= 15 and q["score"] >= 0.6 else "weak")
    try:
        ocr = ocr_fn()
    except Exception as e:
        log.warning("OCR ناموفق: %s", e)
        return fixed, "weak"
    oq = textnorm.quality(ocr)
    if oq["words"] > 0 and (oq["score"] > q["score"] + 0.02 or (q["words"] < 15 and oq["words"] > q["words"])):
        return ocr, "ocr"
    return fixed, method if q["score"] >= 0.6 else "weak"


def iter_pages(path: Path, kind: str) -> Iterator[tuple[int, int, Any]]:
    """(شماره‌ی صفحه، تعداد کل، تابع استخراج) برای هر صفحه."""
    use_ocr = ocr_available()
    if kind == "pdf":
        import pymupdf

        doc = pymupdf.open(path)
        try:
            n = doc.page_count
            for i in range(n):
                page = doc[i]
                yield i + 1, n, lambda page=page: _best_text(
                    page.get_text("text"), (lambda: _ocr_pdf_page(page)) if use_ocr else None
                )
        finally:
            doc.close()
    elif kind == "image":
        if not use_ocr:
            raise RuntimeError("برای خواندن تصویر، OCR (Tesseract) باید نصب باشد")
        yield 1, 1, lambda: (ocr_image(str(path)), "ocr")
    elif kind == "docx":
        import docx

        paras = [p.text for p in docx.Document(str(path)).paragraphs]
        for t in docx.Document(str(path)).tables:
            for row in t.rows:
                paras.append(" | ".join(c.text for c in row.cells))
        yield from _virtual_pages("\n".join(paras))
    else:
        raw = path.read_bytes()
        for enc in ("utf-8", "utf-16", "cp1256"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
        yield from _virtual_pages(text)


def _virtual_pages(text: str, size: int = 3000) -> Iterator[tuple[int, int, Any]]:
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > size and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    parts = parts or [""]
    for i, p in enumerate(parts):
        yield i + 1, len(parts), lambda p=p: (p, "text")


# ───────────────────────── تقسیم به بند ─────────────────────────

_SENT_END = re.compile(r"(?<=[.!?؟؛:])\s+")


def _join_lines(text: str) -> list[str]:
    """خطوط شکسته‌ی PDF را به پاراگراف تبدیل می‌کند."""
    paras, cur = [], []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if cur:
                paras.append(" ".join(cur))
                cur = []
            continue
        cur.append(line)
        # تیتر یا بند شماره‌دار کوتاه ← پاراگراف جدا
        if line.endswith((".", "؟", "?", ":", "!")) and len(" ".join(cur)) > 200:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return [p for p in paras if p]


def chunk_text(text: str) -> list[str]:
    text = textnorm.clean_display(text)
    chunks, cur = [], ""
    for para in _join_lines(text):
        pieces = [para] if len(para) <= CHUNK_CHARS else [s for s in _SENT_END.split(para) if s]
        for piece in pieces:
            if len(piece) > CHUNK_CHARS and cur:
                chunks.append(cur.strip())
                cur = ""
            while len(piece) > CHUNK_CHARS:  # جمله‌ی خیلی بلند (مثلاً جدول)
                chunks.append(piece[:CHUNK_CHARS].strip())
                piece = piece[CHUNK_CHARS - CHUNK_OVERLAP:]
            if len(cur) + len(piece) + 1 > CHUNK_CHARS and cur:
                chunks.append(cur.strip())
                tail = cur[-CHUNK_OVERLAP:]
                cur = tail[tail.find(" ") + 1:] if " " in tail else ""
            cur += ("\n" if cur and piece is para else " ") + piece
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(textnorm.normalize(c)) >= 3]


# ───────────────────────── دریافت فایل ─────────────────────────


def kind_of(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in DOCX_EXT:
        return "docx"
    if ext in TEXT_EXT:
        return "text"
    raise db.ValidationError(f"نوع فایل پشتیبانی نمی‌شود: {ext or filename}. مجاز: PDF، تصویر، Word، متن")


def _safe_name(name: str) -> str:
    name = Path(name).name
    return re.sub(r"[^\w.\-؀-ۿ]+", "_", name)[:120] or "file"


def add_document(fileobj: BinaryIO, filename: str, title: str | None = None,
                 project_id: int | None = None, max_bytes: int | None = None,
                 category: str | None = None) -> dict[str, Any]:
    """category: دسته‌ی تعیین‌شده توسط کاربر (مثلاً بارگذاری از «بایگانی نامه‌ها»)؛ تشخیص خودکار آن را عوض نمی‌کند."""
    kind = kind_of(filename)
    if category is not None and category not in archive.CATEGORIES:
        raise db.ValidationError("دسته‌ی نامعتبر")
    max_bytes = max_bytes or int(os.getenv("CIVILDESK_MAX_UPLOAD_MB", "300")) * 1024 * 1024
    h = hashlib.sha256()
    tmp = tempfile.NamedTemporaryFile(dir=docs_dir(), delete=False, suffix=".part")
    size = 0
    try:
        with tmp:
            while chunk := fileobj.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise db.ValidationError(f"حجم فایل بیش از {max_bytes // (1024 * 1024)} مگابایت است")
                h.update(chunk)
                tmp.write(chunk)
        if size == 0:
            raise db.ValidationError("فایل خالی است")
        digest = h.hexdigest()
        with db.connect() as conn:
            dup = conn.execute("SELECT * FROM documents WHERE sha256 = ?", (digest,)).fetchone()
            if dup:
                os.unlink(tmp.name)
                if project_id and dup["project_id"] is None:  # همان فایل، این بار داخل بایگانی یک پروژه
                    conn.execute("UPDATE documents SET project_id=?, project_auto=0 WHERE id=?", (project_id, dup["id"]))
                if category and dup["category"] != category:  # همان فایل، این بار به‌عنوان نامه
                    edited = sorted(set(json.loads(dup["edited"] or "[]")) | {"category"})
                    conn.execute("UPDATE documents SET category=?, edited=? WHERE id=?", (category, json.dumps(edited), dup["id"]))
                dup = conn.execute("SELECT * FROM documents WHERE id = ?", (dup["id"],)).fetchone()
                return {**dict(dup), "duplicate": True}
            now = db.now_str()
            cur = conn.execute(
                "INSERT INTO documents (title, filename, path, sha256, size, kind, status, project_id, category, edited, "
                "created_at, updated_at) VALUES (?, ?, '', ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                (title or Path(filename).stem, filename, digest, size, kind, project_id, category,
                 json.dumps(["category"]) if category else None, now, now),
            )
            doc_id = cur.lastrowid
            final = docs_dir() / f"{doc_id}_{_safe_name(filename)}"
            os.replace(tmp.name, final)
            conn.execute("UPDATE documents SET path = ? WHERE id = ?", (str(final), doc_id))
            row = dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())
    except BaseException:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise
    worker.wake()
    return row


def delete_document(doc_id: int) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT path FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise db.NotFound("سند پیدا نشد")
        conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    if row["path"] and os.path.exists(row["path"]):
        os.unlink(row["path"])
    _vector_cache.invalidate()


def list_documents() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT d.*, p.name AS project_name FROM documents d LEFT JOIN projects p ON p.id = d.project_id "
            "ORDER BY d.id DESC"
        )]


def get_document(doc_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not row:
        raise db.NotFound("سند پیدا نشد")
    return dict(row)


def reprocess(doc_id: int) -> None:
    with db.connect() as conn:  # از اول (نه ادامه‌ی کار نیمه‌تمام)
        conn.execute("UPDATE documents SET status='queued', error=NULL, pages_done=0, attempts=0 WHERE id = ?", (doc_id,))
    worker.wake()


def render_page_png(doc_id: int, page: int, zoom: float = 1.6) -> bytes:
    import pymupdf

    d = get_document(doc_id)
    if d["kind"] == "image":
        return Path(d["path"]).read_bytes()
    if d["kind"] != "pdf":
        raise db.ValidationError("پیش‌نمایش صفحه فقط برای PDF است")
    with pymupdf.open(d["path"]) as pdf:
        if not 1 <= page <= pdf.page_count:
            raise db.NotFound("صفحه وجود ندارد")
        return pdf[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


# ───────────────────────── پردازش (نخ پس‌زمینه) ─────────────────────────


def process_document(doc_id: int) -> None:
    """خواندن و نمایه‌سازی سند، صفحه به صفحه.

    پیشرفت (متن، صفحه‌های اول و جدول‌ها) هر چند صفحه ذخیره می‌شود تا اگر برنامه وسط کار ری‌استارت شد
    (مثلاً کمبود حافظه)، از همان صفحه ادامه دهد نه از اول. صفحه‌ای که چند بار باعث توقف شده رد می‌شود.
    """
    d = get_document(doc_id)
    start = d["pages_done"] or 0  # > ۰ یعنی ادامه‌ی کار نیمه‌تمام
    attempts = (d["attempts"] or 0) + 1
    skipped: list[int] = json.loads(d["skipped_pages"] or "[]") if start else []
    if start and attempts > MAX_ATTEMPTS and start + 1 not in skipped:
        skipped.append(start + 1)  # این صفحه بارها کار را متوقف کرده
        log.warning("صفحه‌ی %s سند %s رد شد (پردازشش %s بار نیمه‌کاره ماند)", start + 1, doc_id, MAX_ATTEMPTS)
        attempts = 1
    with db.connect() as conn:
        conn.execute("DELETE FROM doc_fts WHERE doc_id = ? AND chunk_id IN (SELECT id FROM doc_chunks WHERE doc_id = ? AND page > ?)",
                     (doc_id, doc_id, start))
        conn.execute("DELETE FROM doc_chunks WHERE doc_id = ? AND page > ?", (doc_id, start))
        if start:
            conn.execute("UPDATE documents SET status='processing', attempts=?, skipped_pages=?, error=NULL WHERE id=?",
                         (attempts, json.dumps(skipped), doc_id))
        else:
            conn.execute(
                "UPDATE documents SET status='processing', pages_done=0, ocr_pages=0, fixed_pages=0, weak_pages=0, "
                "chunks=0, error=NULL, attempts=1, skipped_pages=NULL, head_text=NULL, sheets=NULL WHERE id=?", (doc_id,),
            )
    stats = {"ocr": d["ocr_pages"] if start else 0, "fixed": d["fixed_pages"] if start else 0,
             "weak": d["weak_pages"] if start else 0, "chunks": 0}
    if start:
        with db.connect() as conn:
            stats["chunks"] = conn.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    pending: list[tuple[int, int, str, str]] = []
    # صفحه‌های اول و آخر و جدول‌های مالی (برای بایگانی)؛ در ادامه‌ی کار از ذخیره‌ی قبلی خوانده می‌شوند
    saved = json.loads(d["head_text"] or "[]") if start else []
    head_pages: list[tuple[int, str]] = [tuple(x) for x in saved if x[0] <= min(start, archive.ANALYZE_PAGES)]
    tail_pages: list[tuple[int, str]] = [tuple(x) for x in saved if archive.ANALYZE_PAGES < x[0] <= start][-2:]
    sheets: list[dict[str, Any]] = [x for x in json.loads(d["sheets"] or "[]") if x["page"] <= start] if start else []

    def flush(done: int, total: int) -> None:
        with db.connect() as conn:
            for page, seq, text, method in pending:
                cur = conn.execute(
                    "INSERT INTO doc_chunks (doc_id, page, seq, text, method) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, page, seq, text, method),
                )
                norm = textnorm.normalize(f"{d['title']} {text}" if seq == 0 and page == 1 else text)
                conn.execute("INSERT INTO doc_fts (norm, chunk_id, doc_id) VALUES (?, ?, ?)", (norm, cur.lastrowid, doc_id))
            conn.execute(
                "UPDATE documents SET pages=?, pages_done=?, ocr_pages=?, fixed_pages=?, weak_pages=?, chunks=?, "
                "attempts=0, head_text=?, sheets=?, updated_at=? WHERE id=?",
                (total, done, stats["ocr"], stats["fixed"], stats["weak"], stats["chunks"],
                 json.dumps(head_pages + tail_pages, ensure_ascii=False), json.dumps(sheets, ensure_ascii=False),
                 db.now_str(), doc_id),
            )
        pending.clear()

    total = 0
    invoice_like = archive.classify(d["title"], "", d["kind"], 1) == "invoice"
    read_tables = ocr_available() and d["kind"] in ("pdf", "image")
    for page_no, total, extract in iter_pages(Path(d["path"]), d["kind"]):
        if worker.stopping:
            return
        if page_no <= start:
            continue  # قبلاً خوانده شده
        if page_no in skipped:
            text, method = "", "weak"
        else:
            text, method = extract()
        if (read_tables and page_no not in skipped and page_no <= archive.ANALYZE_PAGES and len(sheets) < MAX_SHEET_PAGES
                and (tables.looks_financial(text) or (invoice_like and page_no <= 3))):
            try:
                sheet = read_sheet_page(Path(d["path"]), d["kind"], page_no)
                if sheet:
                    sheets.append({"page": page_no, "title": tables.sheet_title(text), **sheet})
            except Exception:
                log.exception("خواندن جدول صفحه‌ی %s سند %s ناموفق بود", page_no, doc_id)
        if page_no == 1 and ocr_available() and d["kind"] in ("pdf", "image") and not archive.labeled_date(text[:3000]):
            try:  # شماره و تاریخ در متن صفحه پیدا نشد: سربرگ جداگانه از روی تصویر
                head_pages.extend(archive.with_header([], header_text(Path(d["path"]), d["kind"])))
            except Exception:
                log.exception("خواندن سربرگ سند %s ناموفق بود", doc_id)
        if page_no <= archive.ANALYZE_PAGES:
            if head_pages and head_pages[-1][0] == page_no:  # سربرگ همین صفحه
                head_pages[-1] = (page_no, head_pages[-1][1] + "\n\n" + textnorm.clean_display(text))
            else:
                head_pages.append((page_no, textnorm.clean_display(text)))
        else:  # دو صفحه‌ی آخر هم (برگ خلاصه‌ی صورت‌وضعیت، امضاها) نگه داشته می‌شود
            tail_pages = tail_pages[-1:] + [(page_no, textnorm.clean_display(text))]
        if method in ("ocr", "fixed", "weak"):
            stats[method] += 1
        for seq, chunk in enumerate(chunk_text(text)):
            pending.append((page_no, seq, chunk, method))
            stats["chunks"] += 1
        release_memory()
        if page_no % 3 == 0 or page_no == total:
            flush(page_no, total)
    flush(total, total)
    if skipped:
        with db.connect() as conn:
            conn.execute("UPDATE documents SET error=? WHERE id=?",
                         (f"صفحه‌ی {', '.join(map(str, skipped))} خوانده نشد (پردازشش چند بار نیمه‌کاره ماند)", doc_id))

    # بردارهای معنایی
    if embedder.available():
        with db.connect() as conn:
            rows = conn.execute("SELECT id, text FROM doc_chunks WHERE doc_id = ? ORDER BY id", (doc_id,)).fetchall()
        for i in range(0, len(rows), 64):
            batch = rows[i : i + 64]
            vecs = embedder.embed_passages([f"{d['title']}: {r['text']}" for r in batch])
            if vecs is None:
                break
            with db.connect() as conn:
                conn.executemany(
                    "UPDATE doc_chunks SET vec = ? WHERE id = ?",
                    [(v.astype(np.float16).tobytes(), r["id"]) for v, r in zip(vecs, batch)],
                )
    with db.connect() as conn:
        conn.execute("UPDATE documents SET status='ready', updated_at=? WHERE id=?", (db.now_str(), doc_id))
    _vector_cache.invalidate()
    try:  # دسته‌بندی و استخراج اطلاعات برای بایگانی پروژه
        archive.analyze(doc_id, head_pages + (tail_pages if total > archive.ANALYZE_PAGES else []), sheets)
    except Exception:
        log.exception("تحلیل سند %s برای بایگانی ناموفق بود", doc_id)


def backfill_vectors(batch: int = 64) -> int:
    """ساخت بردار معنایی برای بندهایی که بدون آن نمایه شده‌اند.

    مثلاً اگر برنامه مدتی با CIVILDESK_NO_EMBEDDINGS (پلن کم‌حافظه) اجرا شده و بعد مدل فعال شده باشد؛
    دیگر لازم نیست اسناد دوباره پردازش شوند.
    """
    if not embedder.available():
        return 0
    done = 0
    while not worker.stopping:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT c.id, c.text, d.title FROM doc_chunks c JOIN documents d ON d.id = c.doc_id "
                "WHERE c.vec IS NULL AND d.status = 'ready' LIMIT ?", (batch,),
            ).fetchall()
        if not rows:
            break
        vecs = embedder.embed_passages([f"{r['title']}: {r['text']}" for r in rows])
        if vecs is None:
            break
        with db.connect() as conn:
            conn.executemany(
                "UPDATE doc_chunks SET vec = ? WHERE id = ?",
                [(v.astype(np.float16).tobytes(), r["id"]) for v, r in zip(vecs, rows)],
            )
        done += len(rows)
    if done:
        log.info("بردار معنایی برای %d بند ساخته شد", done)
        _vector_cache.invalidate()
    return done


class Worker:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._thread: threading.Thread | None = None
        self.stopping = False

    def wake(self) -> None:
        self._event.set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with db.connect() as conn:  # کارهای نیمه‌تمام قبل از ری‌استارت
            conn.execute("UPDATE documents SET status='queued' WHERE status='processing'")
        self.stopping = False
        self._thread = threading.Thread(target=self._run, name="doc-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self.stopping = True
            self._event.set()

    def run_pending(self) -> int:
        """پردازش همه‌ی اسناد در صف (برای اجرای هم‌زمان در آزمون‌ها)."""
        n = 0
        while True:
            with db.connect() as conn:
                # اول کارهای نیمه‌تمام، بعد فایل‌های کوچک‌تر؛ تا یک سند خیلی بزرگ بقیه را معطل نکند
                row = conn.execute("SELECT id FROM documents WHERE status='queued' "
                                   "ORDER BY (pages_done > 0) DESC, size, id LIMIT 1").fetchone()
            if not row:
                return n
            try:
                process_document(row["id"])
            except Exception as e:
                log.exception("پردازش سند %s ناموفق بود", row["id"])
                with db.connect() as conn:
                    conn.execute("UPDATE documents SET status='error', error=? WHERE id=?", (str(e)[:500], row["id"]))
            n += 1

    def _run(self) -> None:
        try:
            archive.upgrade()  # بعد از به‌روزرسانی برنامه: تحلیل دوباره (و خواندن دوباره‌ی اسناد خراب)
            archive.analyze_pending()  # اسنادی که هنوز تحلیل نشده‌اند
            backfill_headers()  # نامه‌های قبلی بی‌شماره/بی‌تاریخ
        except Exception:
            log.exception("تحلیل اسناد قدیمی ناموفق بود")
        embedder.get_model()  # بارگذاری مدل در پس‌زمینه
        try:
            backfill_vectors()
        except Exception:
            log.exception("ساخت بردارهای جامانده ناموفق بود")
        while not self.stopping:
            try:
                self.run_pending()
            except Exception:
                log.exception("خطای پردازشگر اسناد")
            self._event.wait(30)
            self._event.clear()


worker = Worker()


# ───────────────────────── جستجو ─────────────────────────


class _VectorCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._data = None

    def get(self) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        with self._lock:
            if self._data is None:
                with db.connect() as conn:
                    rows = conn.execute(
                        "SELECT c.id, c.doc_id, c.vec FROM doc_chunks c JOIN documents d ON d.id=c.doc_id "
                        "WHERE c.vec IS NOT NULL AND d.status='ready'"
                    ).fetchall()
                if not rows:
                    return None
                ids = np.array([r["id"] for r in rows], dtype=np.int64)
                docs = np.array([r["doc_id"] for r in rows], dtype=np.int64)
                mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float16) for r in rows]).astype(np.float32)
                self._data = (ids, docs, mat)
            return self._data


_vector_cache = _VectorCache()


def _keyword_hits(conn: sqlite3.Connection, query: str, doc_ids: list[int] | None, k: int) -> list[int]:
    fts = textnorm.fts_query(query)
    if not fts:
        return []
    sql = "SELECT chunk_id FROM doc_fts WHERE doc_fts MATCH ?"
    params: list[Any] = [fts]
    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        params += doc_ids
    sql += " ORDER BY bm25(doc_fts) LIMIT ?"
    params.append(k)
    try:
        return [r["chunk_id"] for r in conn.execute(sql, params)]
    except sqlite3.OperationalError as e:
        log.warning("پرسش FTS نامعتبر %r: %s", fts, e)
        return []


def _semantic_hits(query: str, doc_ids: list[int] | None, k: int) -> list[int]:
    """بندهای نزدیک از نظر معنا؛ فقط آن‌هایی که شباهتشان نزدیک به بهترین نتیجه است."""
    data = _vector_cache.get()
    if data is None:
        return []
    q = embedder.embed_query(query)
    if q is None:
        return []
    ids, docs, mat = data
    scores = mat @ q
    if doc_ids:
        scores = np.where(np.isin(docs, doc_ids), scores, -1.0)
    top = np.argsort(-scores)[:k]
    if len(top) == 0:
        return []
    cutoff = max(0.78, float(scores[top[0]]) - 0.05)  # شباهت‌های کمتر معمولاً بی‌ربط‌اند
    return [int(ids[i]) for i in top if scores[i] >= cutoff]


def best_sentences(text: str, terms: list[str], n: int = 2) -> list[str]:
    sents = [s.strip() for s in re.split(r"(?<=[.!?؟؛])\s+|\n", text) if len(s.strip()) > 10]
    if not terms:
        return sents[:n]
    scored = []
    for i, s in enumerate(sents):
        norm = textnorm.normalize(s)
        hits = sum(1 for t in terms if t in norm)
        scored.append((hits, -i, s))
    scored.sort(reverse=True)
    return [s for h, _, s in scored[:n] if h > 0] or sents[:1]


def search(query: str, doc_ids: list[int] | None = None, limit: int = 8) -> dict[str, Any]:
    """جستجوی ترکیبی (کلیدواژه + معنایی) و برگرداندن بهترین بندها با منبع."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "semantic": False}
    k = max(limit * 5, 40)
    with db.connect() as conn:
        kw = _keyword_hits(conn, query, doc_ids, k)
    sem = _semantic_hits(query, doc_ids, k)
    fused: dict[int, float] = {}
    for hits, weight in ((kw, 1.0), (sem, 1.0)):
        for rank, cid in enumerate(hits):
            fused[cid] = fused.get(cid, 0.0) + weight / (60 + rank)
    terms = textnorm.highlight_terms(query)
    if not fused:
        return {"query": query, "results": [], "semantic": bool(sem) or embedder.available(), "terms": terms}
    ranked = sorted(fused, key=fused.get, reverse=True)[: limit * 3]
    with db.connect() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT c.id, c.doc_id, c.page, c.text, c.method, d.title, d.kind FROM doc_chunks c "
            f"JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({','.join('?' * len(ranked))})",
            ranked,
        )}
    results, seen_pages = [], set()
    for cid in ranked:
        r = rows.get(cid)
        if not r:
            continue
        key = (r["doc_id"], r["page"])
        if key in seen_pages:  # از هر صفحه یک نتیجه
            continue
        seen_pages.add(key)
        norm = textnorm.normalize(r["text"])
        results.append({
            "chunk_id": cid, "doc_id": r["doc_id"], "title": r["title"], "page": r["page"], "kind": r["kind"],
            "text": r["text"], "method": r["method"], "score": round(fused[cid], 5),
            "matched_terms": [t for t in terms if t in norm],
            "keyword": cid in kw, "semantic": cid in sem,
            "highlights": best_sentences(r["text"], terms),
        })
        if len(results) >= limit:
            break
    return {"query": query, "results": results, "semantic": bool(sem), "terms": terms}


def stats() -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(pages),0) pages, COALESCE(SUM(chunks),0) chunks, "
            "SUM(status IN ('queued','processing')) pending FROM documents"
        ).fetchone()
    return {**dict(row), "ocr": ocr_available(), "semantic": embedder.status()}
