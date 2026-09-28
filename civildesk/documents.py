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

from . import db, embedder, textnorm
from .config import settings

log = logging.getLogger(__name__)

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
TEXT_EXT = {".txt", ".md"}
DOCX_EXT = {".docx"}
ALLOWED_EXT = PDF_EXT | IMAGE_EXT | TEXT_EXT | DOCX_EXT

CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
OCR_DPI = 300
OCR_MAX_PIXELS = 4200  # بیشترین طول تصویر برای OCR (برای نقشه‌های بزرگ)
GOOD_SCORE = 0.97


def docs_dir() -> Path:
    d = Path(os.getenv("CIVILDESK_DOCS_DIR") or (settings.db_path.parent / "docs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── OCR ─────────────────────────


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None and os.getenv("CIVILDESK_NO_OCR") is None


def _tesseract(path: str, langs: str) -> str:
    res = subprocess.run(
        ["tesseract", path, "stdout", "-l", langs, "--psm", "3"],
        capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:300] or "خطای OCR")
    return res.stdout


def ocr_image(path: str) -> str:
    """OCR تطبیقی: اول فارسی (دقیق‌ترین برای متن فارسی)، اگر نتیجه ضعیف بود انگلیسی و ترکیبی.

    مدل ترکیبی fas+eng بعضی کلمات فارسی را لاتین می‌خواند، برای همین پیش‌فرض نیست.
    """
    first = os.getenv("CIVILDESK_OCR_LANGS", "fas")
    best, best_q = "", -1.0
    for langs in dict.fromkeys([first, "eng", "fas+eng"]):
        text = _tesseract(path, langs)
        qd = textnorm.quality(text)
        q = qd["score"] * min(1.0, 0.5 + qd["words"] / 40)  # متن معنادار بیشتر = بهتر
        if q > best_q:
            best, best_q = text, q
        if qd["score"] >= 0.85 and qd["words"] >= 15:
            break
    return best


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
                 project_id: int | None = None, max_bytes: int | None = None) -> dict[str, Any]:
    kind = kind_of(filename)
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
                return {**dict(dup), "duplicate": True}
            now = db.now_str()
            cur = conn.execute(
                "INSERT INTO documents (title, filename, path, sha256, size, kind, status, project_id, created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?, ?, 'queued', ?, ?, ?)",
                (title or Path(filename).stem, filename, digest, size, kind, project_id, now, now),
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
    with db.connect() as conn:
        conn.execute("UPDATE documents SET status='queued', error=NULL WHERE id = ?", (doc_id,))
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
    d = get_document(doc_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
        conn.execute(
            "UPDATE documents SET status='processing', pages_done=0, ocr_pages=0, fixed_pages=0, weak_pages=0, "
            "chunks=0, error=NULL WHERE id=?", (doc_id,),
        )
    stats = {"ocr": 0, "fixed": 0, "weak": 0, "chunks": 0}
    pending: list[tuple[int, int, str, str]] = []

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
                "updated_at=? WHERE id=?",
                (total, done, stats["ocr"], stats["fixed"], stats["weak"], stats["chunks"], db.now_str(), doc_id),
            )
        pending.clear()

    total = 0
    for page_no, total, extract in iter_pages(Path(d["path"]), d["kind"]):
        if worker.stopping:
            return
        text, method = extract()
        if method in ("ocr", "fixed", "weak"):
            stats[method] += 1
        for seq, chunk in enumerate(chunk_text(text)):
            pending.append((page_no, seq, chunk, method))
            stats["chunks"] += 1
        if page_no % 5 == 0 or page_no == total:
            flush(page_no, total)
    flush(total, total)

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
                row = conn.execute("SELECT id FROM documents WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
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
