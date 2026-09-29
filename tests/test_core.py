import datetime as dt

import pytest

from civildesk import db, jalali, services


def test_jalali_known_dates():
    assert jalali.to_jalali(dt.date(2026, 9, 27)) == "1405/07/05"
    assert jalali.to_jalali(dt.date(2025, 3, 21)) == "1404/01/01"
    assert jalali.parse("1403/12/30") == dt.date(2025, 3, 20)  # سال کبیسه


def test_jalali_roundtrip():
    d = dt.date(1990, 1, 1)
    for i in range(0, 20000, 7):
        x = d + dt.timedelta(days=i)
        assert jalali.parse(jalali.to_jalali(x)) == x


def test_jalali_normalize():
    assert jalali.normalize("۱۴۰۵-۷-۵") == "1405/07/05"
    assert jalali.normalize("1405/7/5 8:30", with_time=True) == "1405/07/05 08:30"
    assert jalali.normalize("1405/07/05", with_time=True) == "1405/07/05 09:00"
    with pytest.raises(ValueError):
        jalali.normalize("1405/13/01")
    with pytest.raises(ValueError):
        jalali.normalize("1405/08/31")


def test_crud_and_validation():
    with db.connect() as conn:
        p = db.create(conn, "projects", {"name": "مجتمع مهر", "contract_amount": "۱۲٬۵۰۰٬۰۰۰", "role": "ناظر / مشاور"})
        assert p["contract_amount"] == 12_500_000
        assert p["role"] == "supervisor" and p["status"] == "active"

        t = db.create(conn, "tasks", {"title": "بازدید", "project_id": p["id"], "due_date": "1405/7/6"})
        assert t["project_name"] == "مجتمع مهر" and t["due_date"] == "1405/07/06"

        with pytest.raises(db.ValidationError):
            db.create(conn, "tasks", {"description": "بدون عنوان"})
        with pytest.raises(db.ValidationError):
            db.create(conn, "tasks", {"title": "x", "project_id": 999})
        with pytest.raises(db.ValidationError):
            db.create(conn, "tasks", {"title": "x", "priority": "super"})
        with pytest.raises(db.ValidationError):
            db.create(conn, "tasks", {"title": "x", "unknown": 1})
        with pytest.raises(db.ValidationError):
            db.update(conn, "tasks", t["id"], {"title": ""})

        done = db.update(conn, "tasks", t["id"], {"status": "done"})
        assert done["completed_at"]
        assert db.update(conn, "tasks", t["id"], {"status": "todo"})["completed_at"] is None

        open_tasks = db.list_records(conn, "tasks", exclude_status=["done", "cancelled"])
        assert len(open_tasks) == 1
        assert db.list_records(conn, "tasks", search="بازد")[0]["id"] == t["id"]
        assert db.list_records(conn, "tasks", filters={"status": ["done"]}) == []

        db.delete(conn, "projects", p["id"])
        assert db.get(conn, "tasks", t["id"])["project_id"] is None
        with pytest.raises(db.NotFound):
            db.get(conn, "projects", p["id"])


def test_reminder_flag_only_resets_on_change():
    with db.connect() as conn:
        t = db.create(conn, "tasks", {"title": "یادآوری", "remind_at": "1400/01/01 08:00"})
        assert [r["id"] for r in services.due_reminders(conn)] == [t["id"]]
        services.mark_reminded(conn, t["id"])
        assert services.due_reminders(conn) == []
        db.update(conn, "tasks", t["id"], {"remind_at": "1400/01/01 08:00", "title": "یادآوری ۲"})
        assert services.due_reminders(conn) == []
        db.update(conn, "tasks", t["id"], {"remind_at": "1400/01/02 08:00"})
        assert len(services.due_reminders(conn)) == 1


def test_invoice_net_amount():
    with db.connect() as conn:
        p = db.create(conn, "projects", {"name": "پل"})
        inv = db.create(conn, "invoices", {"project_id": p["id"], "number": "1", "gross_amount": 1000, "deductions": 150})
        assert inv["net_amount"] == 850
        assert db.update(conn, "invoices", inv["id"], {"deductions": 200})["net_amount"] == 800


def test_dashboard_and_finance():
    today = db.today_str()
    with db.connect() as conn:
        p = db.create(conn, "projects", {"name": "ساختمان اداری", "start_date": jalali.add_days(today, -30),
                                         "end_date": jalali.add_days(today, 60), "contract_amount": 1000})
        db.create(conn, "tasks", {"title": "دیروز", "due_date": jalali.add_days(today, -1)})
        db.create(conn, "tasks", {"title": "امروز", "due_date": today, "project_id": p["id"]})
        db.create(conn, "tasks", {"title": "سه روز بعد", "due_date": jalali.add_days(today, 3)})
        db.create(conn, "transactions", {"tx_date": today, "kind": "income", "amount": 500, "description": "پیش‌پرداخت", "project_id": p["id"]})
        db.create(conn, "transactions", {"tx_date": today, "kind": "expense", "amount": 200, "description": "سیمان", "category": "مصالح"})
        db.create(conn, "invoices", {"project_id": p["id"], "number": "1", "gross_amount": 300, "status": "submitted", "submit_date": today})

        d = services.dashboard(conn)
        assert [t["title"] for t in d["overdue"]] == ["دیروز"]
        assert [t["title"] for t in d["due_today"]] == ["امروز"]
        assert [t["title"] for t in d["upcoming"]] == ["سه روز بعد"]
        assert d["projects"][0]["days_left"] == 60
        assert d["pending_invoices"][0]["outstanding"] == 300

        f = services.finance_summary(conn, project_id=p["id"])
        assert (f["income"], f["expense"], f["invoices_receivable"]) == (500, 0, 300)
        f = services.finance_summary(conn, date_from=today, date_to=today)
        assert f["balance"] == 300

        o = services.project_overview(conn, p["id"])
        assert o["time_progress_percent"] == 33
        assert o["invoices"]["billed_percent_of_contract"] == 30.0

        text = services.daily_brief_text(conn)
        assert "امروز" in text and "دیروز" in text
