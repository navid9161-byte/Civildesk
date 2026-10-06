import os
import shutil

import pytest

from civildesk import archive, digits, documents, tables


def test_parse_amount_and_percent():
    assert tables.parse_amount("۱,۵۰۱,۸۱۷,۵۲۶,۴۹۷") == 1_501_817_526_497
    assert tables.parse_amount("۹۰۰٬۲۵۹٬۰۰۴٬۷۳۲") == 900_259_004_732
    assert tables.parse_amount("9..,259,..4,732") == 900_259_004_732  # صفرهایی که نقطه خوانده شده‌اند
    assert tables.parse_amount("12,34,5") is None and tables.parse_amount("۱۴۰۴/۰۵/۰۱") is None
    assert tables.parse_percent("۱۷.۶۰%") == 17.6


def test_fix_small_glyphs():
    # «۰» و جداکننده هر دو کوچک‌اند؛ قالب سه‌رقمی تکلیف را روشن می‌کند
    labs = list("8.880,0.0,000,0,0")
    small = [c in ".,0" for c in labs]
    assert digits.fix_small(labs, small) == "8,880,000,000,000"
    assert digits.fix_small(list("17,60%"), [False, False, True, False, True, False]) == "17.60%"


def _grid(rows: list[list[str]], spans: dict[tuple[int, int], tuple[int, int]] | None = None) -> list[dict]:
    """سلول‌های ساختگی از یک جدول متنی؛ spans: (r, c) ← (r1, c1) برای سلول‌های ادغام‌شده."""
    spans = spans or {}
    covered, cells = set(), []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if (r, c) in covered:
                continue
            r1, c1 = spans.get((r, c), (r, c))
            for rr in range(r, r1 + 1):
                for cc in range(c, c1 + 1):
                    covered.add((rr, cc))
            cells.append({"r0": r, "r1": r1, "c0": c, "c1": c1, "text": text})
    return cells


SHEET = [
    ["ردیف", "بخش", "مبلغ عملیات پیمان (قرارداد)", "", "ارکان پروژه", "پیشرفت تجمعی کار تا دوره قبل", "", "پیشرفت کار طی دوره", "", "پیشرفت تجمعی کار تاکنون", "", ""],
    ["", "", "درصد وزنی", "مبلغ قرارداد (ریال)", "", "درصد از ردیف", "مبلغ (ریال)", "درصد از ردیف", "مبلغ (ریال)", "درصد از کل", "درصد از هر بخش", "مبلغ (ریال)"],
    ["1", "اجرا", "96.07%", "8,530,777,162,044", "پیمانکار", "10.55%", "900,259,004,733", "7.05%", "601,558,521,764", "16.91%", "17.60%", "1,501,817,526,497"],
    ["", "", "", "", "مشاور", "10.55%", "900,259,004,732", "6.86%", "584,965,145,707", "16.73%", "17.41%", "1,385,224,150,439"],
    ["", "", "", "", "کارفرما", "10.55%", "900,259,004,732", "", "", "", "", ""],
    ["2", "مهندسی", "2.38%", "211,650,204,822", "کارفرما", "58.63%", "124,082,022,612", "", "", "1.40%", "58.63%", "124,082,022,612"],
    ["3", "بیمه", "0.36%", "137,572,833,134", "کارفرما", "0.00%", "0", "", "", "0.00%", "0.00%", "0"],
    ["", "جمع", "100%", "8,880,000,000,000", "", "11.54%", "1,024,341,027,344", "", "", "18.13%", "", "1,609,306,173,051"],
]
SPANS = {(0, 0): (1, 0), (0, 1): (1, 1), (0, 2): (0, 3), (0, 4): (1, 4), (0, 5): (0, 6), (0, 7): (0, 8), (0, 9): (0, 11),
         (2, 0): (4, 0), (2, 1): (4, 1), (2, 2): (4, 2), (2, 3): (4, 3)}


def test_parse_sheet_structure_and_correction():
    sh = tables.parse_sheet(_grid(SHEET, SPANS))
    assert sh["sum"] == {"contract": 8_880_000_000_000, "prev": 1_024_341_027_344, "total": 1_609_306_173_051}
    assert [s["name"] for s in sh["sections"]] == ["اجرا", "مهندسی", "بیمه"] and sh["sections"][0]["contract"] == 8_530_777_162_044
    # «۱,۳۸۵» اشتباه خوانده شده؛ ردیف «جمع» نشان می‌دهد «تاکنون» غلط است، نه «قبل» یا «طی دوره»
    assert sh["corrected"] == [{"row": "اجرا · مشاور", "field": "total", "read": 1_385_224_150_439, "fixed": 1_485_224_150_439}]
    assert sh["by_role"]["مشاور"] == {"prev": 900_259_004_732, "period": 584_965_145_707, "total": 1_485_224_150_439}
    assert sh["by_role"]["پیمانکار"]["period"] == 601_558_521_764


def test_sum_row_found_without_label():
    rows = [r[:] for r in SHEET]
    rows[-1][1] = "مه"  # «جمع» بد خوانده شده
    sh = tables.parse_sheet(_grid(rows, SPANS))
    assert sh["sum"]["contract"] == 8_880_000_000_000 and sh["sum"]["total"] == 1_609_306_173_051


def test_sheet_feeds_invoice_fields():
    info: dict = {}
    archive.apply_sheets(info, [{"page": 2, "title": "برگ خلاصه مالی کل", **tables.parse_sheet(_grid(SHEET, SPANS))}])
    v = {k: x["value"] for k, x in info.items() if isinstance(x, dict) and k not in ("sheets", "sheet_fixes")}
    assert v["work_total"] == 1_609_306_173_051 and v["work_prev"] == 1_024_341_027_344
    assert v["work_period"] == 584_965_145_707  # تاکنون − قبل
    assert v["claimed_period"] == 601_558_521_764 and v["approved_period"] == 584_965_145_707
    assert v["contract_total"] == 8_880_000_000_000 and v["progress_fin"] == 18.12
    assert info["work_total"]["page"] == 2


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@pytest.mark.skipif(shutil.which("tesseract") is None or not os.path.exists(FONT), reason="Tesseract یا قلم DejaVu نصب نیست")
def test_scanned_invoice_sheet_end_to_end(tmp_path):
    from tests.fixtures.invoice_sheet import build

    build(str(tmp_path / "text.pdf"), str(tmp_path / "scan.pdf"))
    with open(tmp_path / "scan.pdf", "rb") as f:
        d = documents.add_document(f, "وضعیت شماره ۲.pdf")
    documents.worker.run_pending()
    doc = archive.list_docs_by_id(d["id"])[0]
    i = {k: x["value"] for k, x in doc["info"].items() if isinstance(x, dict)}
    assert doc["category"] == "invoice"
    assert i["work_total"] == 1_609_306_173_051 and i["contract_total"] == 8_880_000_000_000
    assert i["approved_period"] == 584_965_145_707 and i["claimed_period"] == 601_558_521_764
    assert i["period"] == "1404/09/01 تا 1404/10/30"
