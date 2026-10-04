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
    p = _project()
    for text, name in ((CONTRACT, "c.txt"), (LETTER, "l.txt"), (MINUTES, "m.txt")):
        _add(text, name, project_id=p["id"])
    story = archive.project_summary(p["id"])["story"]
    titles = [x["title"] for x in story]
    assert titles[0] == "آغاز" and "مرداد 1403" in titles and "شهریور 1403" in titles
    intro = story[0]["items"][0]["text"]
    assert "85,000,000,000 ریال" in intro and "شرکت عمران شهر (کارفرما)" in intro and "18 ماه" in intro
    letter = next(x for x in story if x["title"] == "مرداد 1403")["items"][0]
    assert letter["text"].startswith("12 مرداد: نامه‌ی شماره‌ی 1403/ص/245") and letter["doc_id"]
    assert story[-1]["title"].startswith("وضعیت امروز")


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
