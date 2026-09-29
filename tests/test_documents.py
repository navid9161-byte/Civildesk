import io
import shutil

import pytest

from civildesk import documents, llm, textnorm

MABHAS = """مبحث نهم مقررات ملی ساختمان
۹-۶-۳ پوشش بتن روی میلگردها
حداقل ضخامت پوشش بتن روی میلگردها در شرایط محیطی شدید برای تیرها و ستون‌ها ۵۰ میلی‌متر است.

۹-۷ عمل آوردن بتن
بتن باید حداقل ۷ روز پس از بتن‌ریزی مرطوب نگه داشته شود.
"""
CONTRACT = """شرایط عمومی پیمان
ماده ۳۷ — پیمانکار موظف است صورت‌وضعیت موقت را در پایان هر ماه به مهندس مشاور تسلیم کند.
کارفرما ظرف پانزده روز مبلغ آن را پرداخت می‌کند.
"""


def _add(text: str, name: str):
    return documents.add_document(io.BytesIO(text.encode()), name)


def test_normalize_and_query():
    assert textnorm.normalize("ميلگردها ۵۰ مي‌شود") == "میلگردها 50 می شود"
    assert textnorm.normalize("آرماتور") == "ارماتور"
    q = textnorm.fts_query("حداقل پوشش بتن تیرها چقدر است؟")
    assert '"تیر"*' in q and "چقدر" not in q and '"است"' not in q


def test_reversed_text_is_fixed():
    good = "حداقل ضخامت پوشش بتن در شرایط محیطی شدید برای تیرها و ستون‌ها ۵۰ میلی‌متر است و باید رعایت شود."
    rev = "\n".join(textnorm._reverse_line(line) for line in good.split("\n"))
    fixed, did = textnorm.fix_direction(rev)
    assert did and fixed == good


def test_quality_detects_garbage():
    assert textnorm.quality(MABHAS)["score"] > 0.95
    garbage = "0 206 0281-10-01 10۲ 00۵۷6۵۲ 60۳06۲۵6 9۵60[]190 20۱0 02۲5 19 ۱۱۵۰ ۲0۲ ۲۳۱۴۱ 50 ]۱929 الاو"
    assert textnorm.quality(garbage)["score"] < 0.5


def test_chunking_keeps_text():
    long = ("این یک جمله‌ی آزمایشی درباره‌ی بتن است. " * 80).strip()
    chunks = documents.chunk_text(long)
    assert len(chunks) > 2 and all(len(c) <= documents.CHUNK_CHARS + 50 for c in chunks)


def test_ingest_search_and_extractive_answer():
    d1 = _add(MABHAS, "mabhas9.txt")
    _add(CONTRACT, "peyman.txt")
    dup = _add(MABHAS, "copy.txt")
    assert dup["duplicate"] and dup["id"] == d1["id"]
    assert documents.worker.run_pending() == 2
    assert all(d["status"] == "ready" for d in documents.list_documents())

    r = documents.search("حداقل پوشش بتن برای تیرها در محیط شدید")
    assert r["results"][0]["title"] == "mabhas9"
    assert "۵۰" in r["results"][0]["text"]
    assert "پوشش" in r["results"][0]["matched_terms"]

    r = documents.search("مهلت پرداخت صورت وضعیت")
    assert r["results"][0]["title"] == "peyman"

    # محدود کردن جستجو به یک سند
    r = documents.search("بتن", doc_ids=[d1["id"]])
    assert {x["doc_id"] for x in r["results"]} == {d1["id"]}

    a = llm.answer("چند روز بتن را مرطوب نگه داریم")
    assert a["mode"] == "extractive" and "۷ روز" in a["answer"]
    assert llm.answer("واژه‌ای که وجود ندارد زرشک")["results"] == []


def test_delete_document():
    d = _add(CONTRACT, "peyman.txt")
    documents.worker.run_pending()
    documents.delete_document(d["id"])
    assert documents.search("پیمانکار")["results"] == []
    assert documents.list_documents() == []


def test_docx_and_pdf_text_layer():
    docx = pytest.importorskip("docx")
    pymupdf = pytest.importorskip("pymupdf")
    w = docx.Document()
    w.add_paragraph("Specified concrete cover for members exposed to weather shall be at least 50 mm.")
    buf = io.BytesIO()
    w.save(buf)
    documents.add_document(io.BytesIO(buf.getvalue()), "aci.docx")

    pdf = pymupdf.open()
    for i in range(3):
        page = pdf.new_page()
        page.insert_text((72, 72), f"Page {i + 1}: slab thickness and deflection limits for one-way slabs.", fontsize=11)
    documents.add_document(io.BytesIO(pdf.tobytes()), "slabs.pdf")
    documents.worker.run_pending()

    r = documents.search("concrete cover weather")
    assert r["results"][0]["title"] == "aci"
    r = documents.search("deflection slabs")
    assert {x["page"] for x in r["results"]} == {1, 2, 3}
    png = documents.render_page_png(r["results"][0]["doc_id"], 2)
    assert png[:4] == b"\x89PNG"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract نصب نیست")
def test_scanned_pdf_uses_ocr():
    pymupdf = pytest.importorskip("pymupdf")
    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 100), "Minimum concrete cover for beams in severe exposure is 50 mm.", fontsize=14)
    pix = page.get_pixmap(dpi=200)
    scan = pymupdf.open()
    p = scan.new_page()
    p.insert_image(p.rect, pixmap=pix)  # فقط تصویر، بدون لایه‌ی متنی
    d = documents.add_document(io.BytesIO(scan.tobytes()), "scan.pdf")
    documents.worker.run_pending()
    doc = [x for x in documents.list_documents() if x["id"] == d["id"]][0]
    assert doc["ocr_pages"] == 1
    assert documents.search("concrete cover beams")["results"][0]["method"] == "ocr"


def test_openai_compatible_answer(monkeypatch):
    _add(MABHAS, "mabhas9.txt")
    documents.worker.run_pending()
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "حداقل پوشش ۵۰ میلی‌متر است [1]."}}]}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, body=json)
        return Resp()

    monkeypatch.setenv("CIVILDESK_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    a = llm.answer("حداقل پوشش بتن تیر")
    assert a["mode"] == "openai" and "۵۰" in a["answer"]
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert "صفحه 1" in seen["body"]["messages"][1]["content"]

    # اگر سرور مدل در دسترس نبود، پاسخ استخراجی برمی‌گردد
    def broken_post(*a, **k):
        raise llm.httpx.ConnectError("down")

    monkeypatch.setattr(llm.httpx, "post", broken_post)
    a = llm.answer("حداقل پوشش بتن تیر")
    assert a["mode"] == "extractive" and a["error"] and "۵۰" in a["answer"]


def test_backfill_vectors_after_enabling_model(monkeypatch):
    import numpy as np

    from civildesk import db, embedder

    _add(MABHAS, "mabhas9.txt")
    documents.worker.run_pending()  # بدون مدل (CIVILDESK_NO_EMBEDDINGS)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM doc_chunks WHERE vec IS NULL").fetchone()[0] > 0

    # فعال شدن مدل (مثلاً بعد از ارتقای پلن)
    monkeypatch.setattr(embedder, "available", lambda: True)
    monkeypatch.setattr(embedder, "embed_passages",
                        lambda texts: np.ones((len(texts), embedder.DIM), dtype=np.float32))
    assert documents.backfill_vectors() > 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM doc_chunks WHERE vec IS NULL").fetchone()[0] == 0
    assert documents.backfill_vectors() == 0
