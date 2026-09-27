"""گزارش‌های ترکیبی: داشبورد امروز، خلاصه‌ی پروژه، وضعیت مالی و متن گزارش صبحگاهی."""
from __future__ import annotations

import sqlite3
from typing import Any

from . import db, jalali
from .config import settings

OPEN_TASK = ("todo", "doing", "waiting")


def money(v: int | None) -> str:
    return f"{int(v or 0):,} {settings.currency}"


def _open_tasks(conn: sqlite3.Connection, where: str, params: list[Any]) -> list[dict[str, Any]]:
    sql = (
        "SELECT t.*, p.name AS project_name FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
        f"WHERE t.status IN ('todo','doing','waiting') AND {where} "
        "ORDER BY t.due_date, CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, t.due_time"
    )
    return [dict(r) for r in conn.execute(sql, params)]


def dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    today = db.today_str()
    week_end = jalali.add_days(today, 7)
    overdue = _open_tasks(conn, "t.due_date < ?", [today])
    due_today = _open_tasks(conn, "t.due_date = ?", [today])
    upcoming = _open_tasks(conn, "t.due_date > ? AND t.due_date <= ?", [today, week_end])
    no_date = _open_tasks(conn, "t.due_date IS NULL AND t.priority IN ('urgent','high')", [])

    projects = [dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE status IN ('active','tender','on_hold') ORDER BY status, id DESC"
    )]
    for p in projects:
        p["open_tasks"] = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_id=? AND status IN ('todo','doing','waiting')", (p["id"],)
        ).fetchone()[0]
        last = conn.execute(
            "SELECT MAX(report_date) FROM daily_reports WHERE project_id=?", (p["id"],)
        ).fetchone()[0]
        p["last_report"] = last
        p["days_left"] = jalali.days_between(today, p["end_date"]) if p["end_date"] else None

    pending_invoices = [dict(r) for r in conn.execute(
        "SELECT i.*, p.name AS project_name FROM invoices i LEFT JOIN projects p ON p.id=i.project_id "
        "WHERE i.status IN ('submitted','reviewing','approved','partial') ORDER BY i.submit_date"
    )]
    for inv in pending_invoices:
        inv["days_waiting"] = jalali.days_between(inv["submit_date"], today) if inv["submit_date"] else None
        inv["outstanding"] = (inv["approved_amount"] or inv["net_amount"] or 0) - (inv["paid_amount"] or 0)

    month_start = today[:8] + "01"
    month = conn.execute(
        "SELECT kind, COALESCE(SUM(amount),0) s FROM transactions WHERE tx_date >= ? AND tx_date <= ? GROUP BY kind",
        (month_start, today),
    ).fetchall()
    month_totals = {r["kind"]: r["s"] for r in month}

    return {
        "today": today,
        "today_long": jalali.long_format(settings.now().date()),
        "now": db.now_str(),
        "overdue": overdue,
        "due_today": due_today,
        "upcoming": upcoming,
        "important_undated": no_date,
        "projects": projects,
        "pending_invoices": pending_invoices,
        "month": {
            "from": month_start,
            "income": month_totals.get("income", 0),
            "expense": month_totals.get("expense", 0),
        },
    }


def project_overview(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    p = db.get(conn, "projects", project_id)
    today = db.today_str()
    tasks = conn.execute(
        "SELECT status, COUNT(*) c FROM tasks WHERE project_id=? GROUP BY status", (project_id,)
    ).fetchall()
    inv = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(net_amount),0) net, COALESCE(SUM(approved_amount),0) appr, "
        "COALESCE(SUM(paid_amount),0) paid FROM invoices WHERE project_id=?",
        (project_id,),
    ).fetchone()
    tx = conn.execute(
        "SELECT kind, COALESCE(SUM(amount),0) s FROM transactions WHERE project_id=? GROUP BY kind", (project_id,)
    ).fetchall()
    tx_totals = {r["kind"]: r["s"] for r in tx}
    reports = db.list_records(conn, "daily_reports", filters={"project_id": project_id}, limit=5)

    time_progress = None
    if p["start_date"] and p["end_date"]:
        total = jalali.days_between(p["start_date"], p["end_date"])
        if total > 0:
            elapsed = jalali.days_between(p["start_date"], today)
            time_progress = max(0, min(100, round(100 * elapsed / total)))

    return {
        "project": p,
        "time_progress_percent": time_progress,
        "physical_progress_percent": p["progress"],
        "days_left": jalali.days_between(today, p["end_date"]) if p["end_date"] else None,
        "tasks_by_status": {r["status"]: r["c"] for r in tasks},
        "open_tasks": db.list_records(
            conn, "tasks", filters={"project_id": project_id}, exclude_status=["done", "cancelled"], limit=20
        ),
        "invoices": {
            "count": inv["n"], "net_total": inv["net"], "approved_total": inv["appr"], "paid_total": inv["paid"],
            "billed_percent_of_contract": (
                round(100 * inv["net"] / p["contract_amount"], 1) if p["contract_amount"] else None
            ),
        },
        "transactions": {"income": tx_totals.get("income", 0), "expense": tx_totals.get("expense", 0)},
        "recent_reports": reports,
    }


def finance_summary(
    conn: sqlite3.Connection, date_from: str | None = None, date_to: str | None = None, project_id: int | None = None
) -> dict[str, Any]:
    def where_for(prefix: str) -> tuple[str, list[Any]]:
        where, params = ["1=1"], []
        if date_from:
            where.append(f"{prefix}tx_date >= ?")
            params.append(jalali.normalize(date_from))
        if date_to:
            where.append(f"{prefix}tx_date <= ?")
            params.append(jalali.normalize(date_to))
        if project_id:
            where.append(f"{prefix}project_id = ?")
            params.append(int(project_id))
        return " AND ".join(where), params

    w, params = where_for("")
    totals = {r["kind"]: r["s"] for r in conn.execute(
        f"SELECT kind, COALESCE(SUM(amount),0) s FROM transactions WHERE {w} GROUP BY kind", params
    )}
    by_cat = [dict(r) for r in conn.execute(
        f"SELECT kind, COALESCE(category,'بدون دسته') category, SUM(amount) total, COUNT(*) n "
        f"FROM transactions WHERE {w} GROUP BY kind, category ORDER BY total DESC", params
    )]
    wt, params_t = where_for("t.")
    by_project = [dict(r) for r in conn.execute(
        f"SELECT COALESCE(p.name,'بدون پروژه') project, t.kind, SUM(t.amount) total FROM transactions t "
        f"LEFT JOIN projects p ON p.id=t.project_id WHERE {wt} "
        f"GROUP BY t.project_id, t.kind ORDER BY total DESC", params_t
    )]
    inv_where = "status NOT IN ('draft','paid')" + (" AND project_id = ?" if project_id else "")
    receivable = conn.execute(
        f"SELECT COALESCE(SUM(COALESCE(approved_amount, net_amount, 0) - COALESCE(paid_amount, 0)),0) FROM invoices WHERE {inv_where}",
        [int(project_id)] if project_id else [],
    ).fetchone()[0]
    income, expense = totals.get("income", 0), totals.get("expense", 0)
    return {
        "from": date_from, "to": date_to, "project_id": project_id,
        "income": income, "expense": expense, "balance": income - expense,
        "by_category": by_cat, "by_project": by_project,
        "invoices_receivable": receivable,
        "currency": settings.currency,
    }


def _task_line(t: dict[str, Any]) -> str:
    parts = [f"• {t['title']}"]
    if t.get("due_time"):
        parts.append(f"⏰{t['due_time']}")
    if t.get("project_name"):
        parts.append(f"[{t['project_name']}]")
    if t.get("priority") in ("urgent", "high"):
        parts.append("❗" if t["priority"] == "high" else "‼️")
    return " ".join(parts)


def daily_brief_text(conn: sqlite3.Connection) -> str:
    d = dashboard(conn)
    lines = [f"☀️ صبح بخیر {settings.owner_name}! امروز {d['today_long']}", ""]
    if d["overdue"]:
        lines.append(f"🔴 عقب‌افتاده ({len(d['overdue'])}):")
        lines += [_task_line(t) + f" (مهلت {t['due_date']})" for t in d["overdue"][:10]]
        lines.append("")
    if d["due_today"]:
        lines.append(f"📌 کارهای امروز ({len(d['due_today'])}):")
        lines += [_task_line(t) for t in d["due_today"]]
        lines.append("")
    else:
        lines += ["📌 برای امروز کار مهلت‌داری ثبت نشده.", ""]
    if d["upcoming"]:
        lines.append("🗓 هفت روز آینده:")
        lines += [_task_line(t) + f" ({t['due_date']})" for t in d["upcoming"][:8]]
        lines.append("")
    stale = [p for p in d["projects"] if p["status"] == "active" and p["last_report"] != d["today"]]
    if stale:
        lines.append("📝 گزارش روزانه‌ی امروز هنوز ثبت نشده برای: " + "، ".join(p["name"] for p in stale))
    late_inv = [i for i in d["pending_invoices"] if (i["days_waiting"] or 0) > 30]
    if late_inv:
        lines.append("💰 صورت‌وضعیت‌های بیش از ۳۰ روز در انتظار: " + "، ".join(
            f"{i['project_name']} ش{i['number']} ({i['days_waiting']} روز)" for i in late_inv
        ))
    return "\n".join(lines).strip()


def due_reminders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """وظایفی که زمان یادآوری‌شان رسیده و هنوز یادآوری نشده‌اند."""
    return _open_tasks(conn, "t.remind_at IS NOT NULL AND t.remind_at <= ? AND COALESCE(t.reminded,0)=0", [db.now_str()])


def mark_reminded(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (task_id,))
