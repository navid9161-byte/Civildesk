import io

from fastapi.testclient import TestClient

from civildesk import archive, db, documents
from civildesk.main import app

LETTER = """شماره: ۱۴۰۳/ص/۲۴۵
تاریخ: ۱۴۰۳/۰۵/۱۲
پیوست: ندارد

جناب آقای مهندس رضایی
مدیرعامل محترم شرکت ساختمانی البرز

موضوع: تأخیر در اجرای سقف طبقه‌ی سوم

با سلام و احترام
احتراماً، با توجه به بازدید مورخ ۱۴۰۳/۰۵/۱۰ از کارگاه پروژه برج ولیعصر، عملیات آرماتوربندی سقف طبقه‌ی سوم
نسبت به برنامه‌ی زمانبندی عقب است. خواهشمند است ظرف مدت ۱۰ روز نسبت به تجهیز نیروی کافی اقدام فرمایید.

رونوشت: کارفرمای محترم
"""

CONTRACT = """قرارداد اجرای عملیات ساختمانی
شماره قرارداد: ۹۸/۱۲۳۴
تاریخ: ۱۴۰۲/۱۱/۲۰

این قرارداد بین شرکت عمران شهر به نمایندگی آقای احمدی که در این قرارداد کارفرما نامیده می‌شود از یک طرف
و شرکت ساختمانی البرز به شماره ثبت ۱۲۳۴۵ که از این پس پیمانکار نامیده می‌شود از طرف دیگر منعقد می‌گردد.

ماده ۱ - موضوع قرارداد: اجرای اسکلت بتنی و سفت‌کاری پروژه برج ولیعصر
ماده ۲ - مبلغ قرارداد: مبلغ کل قرارداد ۸۵,۰۰۰,۰۰۰,۰۰۰ ریال می‌باشد.
ماده ۳ - مدت قرارداد: مدت قرارداد ۱۸ ماه شمسی از تاریخ شروع ۱۴۰۲/۱۲/۰۱ می‌باشد.
ماده ۴ - پیش پرداخت معادل ۲۵ درصد مبلغ قرارداد در قبال ضمانت‌نامه پرداخت می‌شود.
ماده ۵ - تعهدات پیمانکار
ماده ۶ - تعهدات کارفرما
"""

MINUTES = """صورتجلسه بازدید کارگاه
تاریخ جلسه: ۱۴۰۳/۰۶/۰۲
حاضرین: نماینده کارفرما، دستگاه نظارت، پیمانکار

مصوبات:
۱- بتن‌ریزی سقف چهارم تا تاریخ ۱۴۰۳/۰۶/۲۰ انجام شود.
۲- نمونه‌گیری بتن توسط آزمایشگاه انجام شود.

امضا
"""

INVOICE = """صورت وضعیت موقت شماره ۵
پروژه: برج ولیعصر
دوره کارکرد: از تاریخ ۱۴۰۳/۰۵/۰۱ لغایت ۱۴۰۳/۰۵/۳۱
خلاصه صورت وضعیت
ردیف شرح مبلغ (ریال)
۱ مبلغ کارکرد تا پایان این دوره ۴۵,۲۵۰,۰۰۰,۰۰۰
۲ مبلغ کارکرد صورت وضعیت قبلی ۳۱,۱۰۰,۰۰۰,۰۰۰
۳ مبلغ کارکرد این دوره ۱۴,۱۵۰,۰۰۰,۰۰۰
۴ تعدیل ۱,۲۰۰,۰۰۰,۰۰۰
۵ جمع کسورات (حسن انجام کار، بیمه، مالیات، پیش پرداخت) ۳,۴۵۰,۰۰۰,۰۰۰
۶ خالص قابل پرداخت ۱۱,۹۰۰,۰۰۰,۰۰۰
درصد پیشرفت فیزیکی: ۴۲٪
"""

GUARANTEE = """ضمانت‌نامه حسن انجام کار
بانک ملت شعبه ونک
به مبلغ ۴,۲۵۰,۰۰۰,۰۰۰ ریال
این ضمانت‌نامه تا تاریخ ۱۴۰۳/۰۱/۱۵ معتبر است.
"""


def _add(text: str, name: str, project_id=None):
    d = documents.add_document(io.BytesIO(text.encode()), name, project_id=project_id)
    documents.worker.run_pending()
    return documents.get_document(d["id"])


def _project(**kw):
    with db.connect() as conn:
        return db.create(conn, "projects", {"name": "برج ولیعصر", **kw})


def test_classify():
    assert archive.classify("scan", archive._prep(LETTER), "pdf", 1) == "letter"
    assert archive.classify("scan", archive._prep(CONTRACT), "pdf", 5) == "contract"
    assert archive.classify("scan", archive._prep(MINUTES), "pdf", 1) == "minutes"
    assert archive.classify("scan", archive._prep(GUARANTEE), "pdf", 1) == "guarantee"
    assert archive.classify("قرارداد اصلی", "", "pdf", 1) == "contract"  # نام فایل
    assert archive.classify("IMG_2034", "", "image", 1) == "photo"


def test_letter_extraction():
    info = archive.extract(archive._prep(LETTER), "letter")
    assert info["doc_no"]["value"] == "1403/ص/245"
    assert info["doc_date"]["value"] == "1403/05/12"
    assert info["subject"]["value"] == "تأخیر در اجرای سقف طبقه‌ی سوم"
    assert info["to"]["value"].startswith("جناب آقای مهندس رضایی")
    assert info["deadline"]["days"] == 10
    assert "آرماتوربندی" in info["gist"]["value"]


def test_contract_extraction():
    info = archive.extract(archive._prep(CONTRACT), "contract")
    assert info["doc_no"]["value"] == "98/1234"
    assert info["amount"]["value"] == 85_000_000_000
    assert info["duration"]["n"] == 18 and info["duration"]["unit"] == "ماه"
    assert info["start_date"]["value"] == "1402/12/01"
    assert info["employer"]["value"] == "شرکت عمران شهر"
    assert info["contractor"]["value"] == "شرکت ساختمانی البرز"
    assert "اسکلت بتنی" in info["subject"]["value"]


def test_reversed_date_and_toman():
    info = archive.extract(archive._prep("تاریخ: ۱۲/۰۵/۱۴۰۳\nمبلغ ۲۵۰,۰۰۰ تومان واریز شد"), "financial")
    assert info["doc_date"]["value"] == "1403/05/12"
    assert info["amount"]["value"] == 2_500_000


def test_live_project_summary():
    p = _project(code="98/1234")
    c = _add(CONTRACT, "contract.txt", project_id=p["id"])
    assert c["category"] == "contract" and c["doc_date"] == "1402/11/20"
    # نامه بدون انتخاب پروژه آپلود می‌شود و از روی نام پروژه در متن خودکار به آن وصل می‌شود
    letter = _add(LETTER, "letter.txt")
    assert letter["project_id"] == p["id"] and letter["project_auto"] == 1
    assert letter["category"] == "letter"
    _add(MINUTES, "jalase.txt", project_id=p["id"])
    _add(GUARANTEE, "zemanat.txt", project_id=p["id"])

    s = archive.project_summary(p["id"])
    assert s["counts"] == {"contract": 1, "letter": 1, "minutes": 1, "guarantee": 1}
    facts = {f["key"]: f["value"] for f in s["facts"]}
    assert facts["contract_amount"] == 85_000_000_000
    assert facts["employer"] == "شرکت عمران شهر"
    assert facts["end_date"] == "1404/06/01"  # شروع + ۱۸ ماه
    assert {x["due"] for x in s["deadlines"]} == {"1403/05/22", "1403/06/20"}
    assert s["guarantees"][0]["valid_until"] == "1403/01/15"
    assert s["minutes"][0]["items"][0].startswith("بتن‌ریزی سقف چهارم")
    sugg = {x["field"]: x["value"] for x in s["suggestions"]}
    assert sugg["contract_amount"] == 85_000_000_000 and sugg["start_date"] == "1402/12/01"
    assert "code" not in sugg  # کد پروژه پر بوده
    dated = [t for t in s["timeline"] if t["date"] != s["today"]]  # ضمانت‌نامه تاریخ صدور ندارد
    assert dated[0]["date"] == "1403/06/02"  # جدیدترین اول


def test_manual_edit_survives_reprocess():
    p = _project()
    d = _add(LETTER, "letter.txt", project_id=p["id"])
    archive.update_meta(d["id"], {"category": "order", "subject": "دستور تجهیز نیرو"})
    documents.reprocess(d["id"])
    documents.worker.run_pending()
    d = documents.get_document(d["id"])
    assert d["category"] == "order" and d["subject"] == "دستور تجهیز نیرو"
    assert d["doc_no"] == "1403/ص/245"  # فیلدهای دست‌نخورده دوباره استخراج می‌شوند


def test_archive_api():
    client = TestClient(app)
    p = _project()
    r = client.post("/api/documents", files={"files": ("c.txt", CONTRACT.encode())}, data={"project_id": str(p["id"])})
    assert r.status_code == 201
    documents.worker.run_pending()
    ov = client.get("/api/archive").json()
    assert ov["projects"][0]["docs"] == 1 and ov["unassigned"] == 0
    s = client.get(f"/api/archive/{p['id']}").json()
    doc_id = s["documents"][0]["id"]
    assert s["documents"][0]["category"] == "contract"
    r = client.patch(f"/api/documents/{doc_id}", json={"project_id": None, "doc_date": "۱۴۰۲/۱۱/۲۱"})
    assert r.status_code == 200 and r.json()["doc_date"] == "1402/11/21"
    assert client.get("/api/archive/0").json()["documents"][0]["id"] == doc_id
    assert client.patch(f"/api/documents/{doc_id}", json={"category": "xxx"}).status_code == 400


def test_latin_garbage_is_low_quality():
    from civildesk import textnorm
    broken = "e ee © •eeee © •eee ee ©e e @ @ee WFee eJ ee © +ee • e • ee nb +) oIB Un oI I' I=++) q' :$ ! § b :::L q a' 'a ^g"
    mixed = ("AMENDMENT (low OF CONTRACT , No. 073-1404-9 & gy yo pl FOF —oV 9 lad BETWEEN os PETROLEUM Call dag 9 "
             "urhign OS pi ENGINEERING AND oe DEVELOPMENT (onside Jlait) COMPANY AND MEHRAN OIL AND GAS Cah 990 g Cad dewgd")
    english = ("This amendment is made between the Company and the Contractor and shall be effective from the date "
               "of signature of both parties. The Contractor shall complete the works within the time for completion.")
    assert textnorm.quality(broken)["score"] < 0.2
    assert textnorm.quality(mixed)["score"] < 0.5
    assert textnorm.quality(english)["score"] > 0.95
    assert textnorm.quality(LETTER)["score"] > 0.9


def test_merge_bilingual_ocr_lines():
    def ln(text, x0, y0, conf=90):
        return {"text": text, "x0": x0, "y0": y0, "x1": x0 + 400, "y1": y0 + 30,
                "score": conf / 100 * __import__("civildesk.textnorm").textnorm.line_plausibility(text)}
    fas = [ln("پیمانکار موظف است کارها را در مدت مقرر به پایان برساند", 600, 100),
           ln("۰ 00۵۱۲۳۵۲ 10 1 ۱۱۵۰ ۸۵۷۴۶۱۱۲2۱۱۲۴", 100, 100, 70)]
    eng = [ln("cul abo yISiley Wlgo 5lS 9 rai", 600, 100, 60),
           ln("The Contractor shall complete the works within the time", 100, 100)]
    merged = documents.merge_ocr_lines(fas, eng)
    assert merged.split("\n") == ["پیمانکار موظف است کارها را در مدت مقرر به پایان برساند",
                                  "The Contractor shall complete the works within the time"]


def test_summary_and_invoice_title():
    gist = archive.extract(archive._prep(LETTER), "letter")["gist"]["value"]
    assert gist.startswith("با توجه به بازدید") and "خواهشمند است" in gist and "شماره" not in gist
    assert archive.classify("وضعیت شماره ۱", "", "pdf", 10) == "invoice"
    table = "ردیف شرح واحد مقدار\n1 خاکبرداری m3 1250 450000 562500000\n2 بتن m3 320 9800000 3136000000\n" * 5
    info = archive.extract(archive._prep(table), "invoice")
    assert "gist" not in info and info["table"]["value"]
    g = archive.extract(archive._prep(GUARANTEE), "guarantee")
    assert "doc_date" not in g and g["valid_until"]["value"] == "1403/01/15"


def test_project_story():
    p = _project(location="تهران")
    for text, name in ((CONTRACT, "c.txt"), (LETTER, "l.txt"), (MINUTES, "m.txt"), (INVOICE, "وضعیت شماره ۵.txt")):
        _add(text, name, project_id=p["id"])
    s = archive.project_summary(p["id"])
    story = {x["title"]: " ".join(i["text"] for i in x["items"]) for x in s["story"]}
    titles = list(story)
    assert titles[:2] == ["در یک نگاه", "معرفی پروژه"] and "مرداد 1403" in titles and "وضعیت مالی" in titles
    intro = story["معرفی پروژه"]
    assert "در تهران" in intro and "شرکت عمران شهر به‌عنوان کارفرما" in intro and "85 میلیارد ریال" in intro
    assert "کار از 1402/12/01 آغاز شده و باید تا 1404/06/01 به پایان برسد." in intro
    aug = story["مرداد 1403"]
    assert aug.startswith("در 12 مرداد، نامه‌ای به شماره‌ی 1403/ص/245") and "سپس در 31 مرداد، صورت‌وضعیت موقت شماره‌ی 5" in aug
    assert "کارکرد این دوره 14٫15 میلیارد ریال" in aug and "(حدود 53٫2٪ مبلغ قرارداد)" in aug
    assert "پیشرفت فیزیکی 42٪ است، یعنی حدود" in story["در یک نگاه"]
    assert s["invoices"][0]["work_total"] == 45_250_000_000 and s["invoices"][0]["total_pct"] == 53.2
    assert all(i.get("doc_id") for x in s["story"] if x["kind"] == "month" for i in x["items"])


def test_invoice_summary_sheet():
    info = archive.extract(archive._prep(INVOICE), "invoice")
    v = {k: x["value"] for k, x in info.items() if isinstance(x, dict)}
    assert v["invoice_kind"] == "موقت" and v["invoice_no"] == "5"
    assert v["period"] == "1403/05/01 تا 1403/05/31" and v["doc_date"] == "1403/05/31"
    assert (v["work_total"], v["work_prev"], v["work_period"]) == (45_250_000_000, 31_100_000_000, 14_150_000_000)
    assert (v["adjustment"], v["deductions"], v["net"], v["progress"]) == (1_200_000_000, 3_450_000_000, 11_900_000_000, 42)
    # ستون اعداد جدا از ستون شرح، و کارکرد این دوره از تفاضل
    col = "وضعیت شماره ۲ - موقت\nکارکرد ماه خرداد ۱۴۰۴\nمبلغ کارکرد تا این صورت وضعیت\nمبلغ کارکرد قبلی\nخالص قابل پرداخت\n" \
          "۲۰,۰۰۰,۰۰۰,۰۰۰\n۱۲,۵۰۰,۰۰۰,۰۰۰\n۶,۸۰۰,۰۰۰,۰۰۰\n"
    v = {k: x["value"] for k, x in archive.extract(archive._prep(col), "invoice").items() if isinstance(x, dict)}
    assert v["invoice_kind"] == "موقت" and v["period"] == "خرداد 1404"
    assert (v["work_total"], v["work_prev"], v["net"], v["work_period"]) == (20_000_000_000, 12_500_000_000, 6_800_000_000, 7_500_000_000)
    # برگه‌ی خلاصه‌ی ساخت‌یافته برای کارت سند
    d = _add(INVOICE, "وضعیت شماره ۵.txt", project_id=_project()["id"])
    brief = {r["label"]: r["value"] for r in archive.list_docs(d["project_id"])[0]["brief"]}
    assert brief["کارکرد این دوره"] == 14_150_000_000 and brief["دوره‌ی کارکرد"] == "1403/05/01 تا 1403/05/31"


def test_upgrade_requeues_garbage_and_reanalyzes():
    p = _project()
    d = _add(LETTER, "l.txt", project_id=p["id"])
    with db.connect() as conn:
        conn.execute("UPDATE documents SET kind='pdf', pages=2 WHERE id=?", (d["id"],))
        conn.execute("UPDATE doc_chunks SET text=? WHERE doc_id=?", ("e ee © •eeee © •eee ee ©e e @ @ee WFee eJ ee nb oIB Un", d["id"]))
        conn.execute("DELETE FROM kv WHERE key='archive_version'")
    archive.upgrade()
    assert documents.get_document(d["id"])["status"] == "queued"
    assert documents.get_document(d["id"])["analyzed_at"] is None
    archive.upgrade()  # فقط یک بار


def test_archived_invoice_to_record():
    p = _project()
    d = _add(INVOICE, "وضعیت شماره ۵.txt", project_id=p["id"])
    rec = archive.to_invoice_record(d["id"])
    assert rec["number"] == "5" and rec["kind"] == "interim" and rec["status"] == "submitted"
    assert rec["gross_amount"] == 15_350_000_000 and rec["deductions"] == 3_450_000_000 and rec["net_amount"] == 11_900_000_000
    assert (rec["period_start"], rec["period_end"]) == ("1403/05/01", "1403/05/31")
    import pytest
    with pytest.raises(db.ValidationError):
        archive.to_invoice_record(d["id"])  # تکراری


def test_letters_register_and_two_line_summary():
    p = _project()
    _add(LETTER, "letter.txt", project_id=p["id"])
    _add(CONTRACT, "c.txt", project_id=p["id"])  # قرارداد در دفتر نامه‌ها نمی‌آید
    # فایلی که از «بایگانی نامه‌ها» بارگذاری شده، نامه می‌ماند حتی اگر تشخیص خودکار چیز دیگری بگوید
    d = documents.add_document(io.BytesIO("گزارش بازدید از کارگاه و وضعیت ایمنی پرسنل در هفته‌ی گذشته".encode()),
                               "note.txt", project_id=p["id"], category="letter")
    documents.worker.run_pending()
    reg = archive.letters_register(p["id"])
    assert d["id"] in [x["id"] for x in reg["letters"]] and len(reg["letters"]) == 2
    x = next(x for x in reg["letters"] if x["id"] != d["id"])
    assert x["doc_no"] == "1403/ص/245" and x["subject"] == "تأخیر در اجرای سقف طبقه‌ی سوم"
    assert x["to"].startswith("جناب آقای") and x["deadline"] == "1403/05/22" and x["late"]
    assert x["summary"].startswith("با توجه به بازدید") and "خواهشمند است" in x["summary"] and len(x["summary"]) <= 231
    ov = archive.overview()
    assert ov["projects"][0]["letters"] == 2
    name, data = archive.letters_csv(p["id"])
    text = data.decode("utf-8")
    assert text.startswith("﻿ردیف,تاریخ,شماره") and "1403/ص/245" in text and name.endswith(".csv")


def test_letters_api():
    client = TestClient(app)
    p = _project()
    r = client.post("/api/documents", files={"files": ("l.txt", LETTER.encode())},
                    data={"project_id": str(p["id"]), "category": "letter"})
    assert r.status_code == 201
    documents.worker.run_pending()
    assert client.get(f"/api/letters/{p['id']}").json()["letters"][0]["summary"]
    r = client.get(f"/api/letters/{p['id']}/export.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert client.post("/api/documents", files={"files": ("x.txt", b"x")}, data={"category": "bad"}).status_code == 400
