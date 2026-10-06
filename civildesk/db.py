"""لایه‌ی داده: تعریف موجودیت‌ها و عملیات عمومی ایجاد/ویرایش/جستجو روی SQLite.

هر موجودیت (پروژه، وظیفه، گزارش روزانه، ...) یک بار در ENTITIES تعریف می‌شود؛
جدول پایگاه داده، فرم‌های وب و ابزارهای دستیار هوشمند همه از همین تعریف ساخته می‌شوند.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import jalali
from .config import settings


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: str = "text"  # text | longtext | int | money | percent | date | datetime | choice | project
    required: bool = False
    choices: dict[str, str] = field(default_factory=dict)  # کلید → برچسب فارسی
    default: Any = None
    help: str = ""
    system: bool = False  # فقط‌خواندنی؛ در فرم و ورودی ابزارها نمی‌آید

    @property
    def sql_type(self) -> str:
        return "INTEGER" if self.type in ("int", "money", "percent", "project") else "TEXT"


@dataclass(frozen=True)
class Entity:
    name: str  # نام جدول
    label: str  # برچسب مفرد
    label_plural: str
    fields: tuple[Field, ...]
    title_field: str
    date_field: str | None = None
    order_by: str = "id DESC"
    search_fields: tuple[str, ...] = ()

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def editable(self) -> list[Field]:
        return [f for f in self.fields if not f.system]

    @property
    def has_project(self) -> bool:
        return any(f.type == "project" for f in self.fields)


PRIORITY = {"urgent": "فوری", "high": "زیاد", "medium": "متوسط", "low": "کم"}

ENTITIES: dict[str, Entity] = {
    e.name: e
    for e in [
        Entity(
            name="projects",
            label="پروژه",
            label_plural="پروژه‌ها",
            title_field="name",
            date_field="start_date",
            order_by="CASE status WHEN 'active' THEN 0 WHEN 'tender' THEN 1 WHEN 'on_hold' THEN 2 ELSE 3 END, id DESC",
            search_fields=("name", "code", "employer", "contractor", "location", "notes"),
            fields=(
                Field("name", "نام پروژه", required=True),
                Field("code", "کد / شماره قرارداد"),
                Field("role", "نقش من", "choice", choices={
                    "supervisor": "ناظر / مشاور", "contractor": "پیمانکار", "employer": "کارفرما",
                    "designer": "طراح", "site_manager": "رئیس کارگاه", "other": "سایر",
                }),
                Field("employer", "کارفرما"),
                Field("contractor", "پیمانکار"),
                Field("consultant", "مشاور"),
                Field("location", "محل پروژه"),
                Field("contract_amount", "مبلغ قرارداد", "money"),
                Field("start_date", "تاریخ شروع", "date"),
                Field("end_date", "تاریخ پایان قراردادی", "date"),
                Field("progress", "درصد پیشرفت", "percent", default=0),
                Field("status", "وضعیت", "choice", default="active", choices={
                    "tender": "مناقصه / پیشنهاد", "active": "در حال اجرا", "on_hold": "متوقف",
                    "done": "تحویل موقت / پایان", "closed": "بسته‌شده",
                }),
                Field("notes", "توضیحات", "longtext"),
            ),
        ),
        Entity(
            name="tasks",
            label="وظیفه",
            label_plural="وظایف",
            title_field="title",
            date_field="due_date",
            order_by=(
                "CASE status WHEN 'doing' THEN 0 WHEN 'todo' THEN 1 WHEN 'waiting' THEN 2 ELSE 3 END, "
                "CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, "
                "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id"
            ),
            search_fields=("title", "description"),
            fields=(
                Field("title", "عنوان", required=True),
                Field("description", "شرح", "longtext"),
                Field("project_id", "پروژه", "project"),
                Field("category", "دسته", "choice", default="work", choices={
                    "work": "کاری", "site": "بازدید کارگاه", "meeting": "جلسه",
                    "document": "مکاتبه / مدارک", "personal": "شخصی",
                }),
                Field("priority", "اولویت", "choice", default="medium", choices=PRIORITY),
                Field("status", "وضعیت", "choice", default="todo", choices={
                    "todo": "انجام‌نشده", "doing": "در حال انجام", "waiting": "منتظر دیگران",
                    "done": "انجام‌شده", "cancelled": "لغو‌شده",
                }),
                Field("due_date", "مهلت", "date"),
                Field("due_time", "ساعت", help="مثلاً 10:30"),
                Field("remind_at", "زمان یادآوری", "datetime", help="مثلاً 1405/07/06 09:00"),
                Field("reminded", "یادآوری ارسال شد", "int", system=True, default=0),
                Field("completed_at", "زمان انجام", "datetime", system=True),
            ),
        ),
        Entity(
            name="daily_reports",
            label="گزارش روزانه",
            label_plural="گزارش‌های روزانه",
            title_field="report_date",
            date_field="report_date",
            order_by="report_date DESC, id DESC",
            search_fields=("work_done", "issues", "materials", "notes"),
            fields=(
                Field("project_id", "پروژه", "project", required=True),
                Field("report_date", "تاریخ", "date", required=True),
                Field("weather", "وضعیت جوی"),
                Field("temperature", "دما"),
                Field("manpower", "نیروی انسانی", "longtext", help="مثلاً: ۲ بنا، ۴ کارگر، ۱ آرماتوربند"),
                Field("equipment", "ماشین‌آلات", "longtext"),
                Field("materials", "مصالح ورودی", "longtext"),
                Field("work_done", "کارهای انجام‌شده", "longtext", required=True),
                Field("issues", "مشکلات / موانع", "longtext"),
                Field("instructions", "دستورات و صورتجلسات", "longtext"),
                Field("notes", "یادداشت", "longtext"),
            ),
        ),
        Entity(
            name="invoices",
            label="صورت‌وضعیت",
            label_plural="صورت‌وضعیت‌ها",
            title_field="number",
            date_field="submit_date",
            order_by="id DESC",
            search_fields=("number", "notes"),
            fields=(
                Field("project_id", "پروژه", "project", required=True),
                Field("number", "شماره صورت‌وضعیت", required=True, help="مثلاً ۳ موقت"),
                Field("kind", "نوع", "choice", default="interim", choices={
                    "interim": "موقت", "final": "قطعی", "adjustment": "تعدیل", "extra": "کار اضافه",
                }),
                Field("period_start", "از تاریخ", "date"),
                Field("period_end", "تا تاریخ", "date"),
                Field("gross_amount", "مبلغ ناخالص", "money", required=True),
                Field("deductions", "کسورات", "money", default=0, help="حسن انجام کار، بیمه، مالیات، پیش‌پرداخت و ..."),
                Field("net_amount", "خالص قابل پرداخت", "money", help="خالی بگذارید تا خودکار حساب شود"),
                Field("status", "وضعیت", "choice", default="draft", choices={
                    "draft": "پیش‌نویس", "submitted": "ارسال‌شده", "reviewing": "در دست بررسی",
                    "approved": "تأیید‌شده", "partial": "پرداخت جزئی", "paid": "پرداخت‌شده",
                }),
                Field("submit_date", "تاریخ ارسال", "date"),
                Field("approved_amount", "مبلغ تأییدشده", "money"),
                Field("paid_amount", "مبلغ پرداخت‌شده", "money", default=0),
                Field("paid_date", "تاریخ پرداخت", "date"),
                Field("notes", "توضیحات", "longtext"),
            ),
        ),
        Entity(
            name="transactions",
            label="تراکنش",
            label_plural="دریافت و پرداخت",
            title_field="description",
            date_field="tx_date",
            order_by="tx_date DESC, id DESC",
            search_fields=("description", "counterparty", "category"),
            fields=(
                Field("tx_date", "تاریخ", "date", required=True),
                Field("kind", "نوع", "choice", required=True, default="expense", choices={
                    "income": "دریافتی", "expense": "هزینه / پرداختی",
                }),
                Field("amount", "مبلغ", "money", required=True),
                Field("project_id", "پروژه", "project"),
                Field("category", "دسته", help="مثلاً مصالح، دستمزد، ایاب‌وذهاب، حق‌الزحمه"),
                Field("counterparty", "طرف حساب"),
                Field("description", "شرح", required=True),
            ),
        ),
        Entity(
            name="contacts",
            label="مخاطب",
            label_plural="مخاطبین",
            title_field="name",
            order_by="name",
            search_fields=("name", "company", "role", "phone", "notes"),
            fields=(
                Field("name", "نام", required=True),
                Field("company", "شرکت / سازمان"),
                Field("role", "سمت"),
                Field("phone", "تلفن"),
                Field("email", "ایمیل"),
                Field("project_id", "پروژه", "project"),
                Field("notes", "یادداشت", "longtext"),
            ),
        ),
        Entity(
            name="notes",
            label="یادداشت",
            label_plural="یادداشت‌ها",
            title_field="title",
            order_by="id DESC",
            search_fields=("title", "content", "tags"),
            fields=(
                Field("title", "عنوان", required=True),
                Field("content", "متن", "longtext"),
                Field("tags", "برچسب‌ها", help="با ویرگول جدا کنید"),
                Field("project_id", "پروژه", "project"),
            ),
        ),
    ]
}


class ValidationError(ValueError):
    pass


class NotFound(LookupError):
    pass


# ───────────────────────── اتصال ─────────────────────────

_init_lock = threading.Lock()
_initialized: set[str] = set()


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    path = str(path or settings.db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with _init_lock:
            if path not in _initialized:
                _migrate(conn)
                if path != ":memory:":
                    _initialized.add(path)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """ساخت جدول‌ها و افزودن ستون‌های جدید (اگر تعریف موجودیت بعداً تغییر کرد)."""
    conn.execute("PRAGMA journal_mode = WAL")
    for ent in ENTITIES.values():
        cols = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for f in ent.fields:
            col = f"{f.name} {f.sql_type}"
            if f.type == "project":
                col += " REFERENCES projects(id) ON DELETE SET NULL"
            cols.append(col)
        cols += ["created_at TEXT", "updated_at TEXT"]
        conn.execute(f"CREATE TABLE IF NOT EXISTS {ent.name} ({', '.join(cols)})")
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({ent.name})")}
        for f in ent.fields:
            if f.name not in existing:
                conn.execute(f"ALTER TABLE {ent.name} ADD COLUMN {f.name} {f.sql_type}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT
        )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, due_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_channel ON chat_messages(channel, id)")
    # کتابخانه‌ی اسناد (PDF و ...) برای پرسش و پاسخ
    conn.execute(
        """CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT,
            size INTEGER,
            kind TEXT,
            pages INTEGER DEFAULT 0,
            pages_done INTEGER DEFAULT 0,
            ocr_pages INTEGER DEFAULT 0,
            fixed_pages INTEGER DEFAULT 0,
            weak_pages INTEGER DEFAULT 0,
            chunks INTEGER DEFAULT 0,
            status TEXT DEFAULT 'queued',
            error TEXT,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    # بایگانی پروژه: دسته، اطلاعات استخراج‌شده و فیلدهایی که کاربر دستی اصلاح کرده
    doc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    for col, typ in (("category", "TEXT"), ("doc_date", "TEXT"), ("doc_no", "TEXT"), ("subject", "TEXT"),
                     ("info", "TEXT"), ("edited", "TEXT"), ("project_auto", "INTEGER DEFAULT 0"),
                     ("analyzed_at", "TEXT"), ("head_text", "TEXT"), ("sheets", "TEXT")):
        if col not in doc_cols:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id, category)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS doc_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page INTEGER,
            seq INTEGER,
            text TEXT NOT NULL,
            method TEXT,
            vec BLOB
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(doc_id, page, seq)")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5("
        "norm, chunk_id UNINDEXED, doc_id UNINDEXED, tokenize='unicode61 remove_diacritics 0')"
    )


def now_str() -> str:
    return jalali.to_jalali(settings.now(), with_time=True)


def today_str() -> str:
    return jalali.to_jalali(settings.now())


# ───────────────────────── اعتبارسنجی ─────────────────────────

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬،", "01234567890123456789,,")


def _to_int(f: Field, value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"«{f.label}» باید عدد باشد")
    if isinstance(value, (int, float)):
        return int(round(value))
    s = str(value).translate(_FA_DIGITS).replace(",", "").replace(" ", "").replace("%", "")
    try:
        return int(round(float(s)))
    except ValueError:
        raise ValidationError(f"«{f.label}» باید عدد باشد (مقدار: {value!r})") from None


def _coerce(conn: sqlite3.Connection, f: Field, value: Any) -> Any:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    try:
        if f.type in ("int", "money"):
            return _to_int(f, value)
        if f.type == "percent":
            v = _to_int(f, value)
            if not 0 <= v <= 100:
                raise ValidationError(f"«{f.label}» باید بین ۰ تا ۱۰۰ باشد")
            return v
        if f.type == "date":
            return jalali.normalize(str(value))
        if f.type == "datetime":
            return jalali.normalize(str(value), with_time=True)
        if f.type == "choice":
            v = str(value).strip()
            if v in f.choices:
                return v
            for key, label in f.choices.items():  # برچسب فارسی هم پذیرفته می‌شود
                if label == v:
                    return key
            opts = "، ".join(f"{k} ({lbl})" for k, lbl in f.choices.items())
            raise ValidationError(f"مقدار «{f.label}» نامعتبر است: {v!r}. گزینه‌ها: {opts}")
        if f.type == "project":
            pid = _to_int(f, value)
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                raise ValidationError(f"پروژه با شناسه {pid} وجود ندارد")
            return pid
    except ValueError as e:  # خطای تاریخ
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(str(e)) from None
    return str(value).strip()


def _clean(conn: sqlite3.Connection, ent: Entity, data: dict[str, Any], partial: bool) -> dict[str, Any]:
    allowed = {f.name: f for f in ent.editable}
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValidationError(
            f"فیلد(های) ناشناخته برای {ent.label}: {', '.join(sorted(unknown))}. "
            f"فیلدهای مجاز: {', '.join(allowed)}"
        )
    out = {name: _coerce(conn, allowed[name], v) for name, v in data.items()}
    if not partial:
        for f in ent.editable:
            if out.get(f.name) is None and f.default is not None:
                out[f.name] = f.default
        missing = [f.label for f in ent.editable if f.required and out.get(f.name) is None]
        if missing:
            raise ValidationError(f"فیلدهای اجباری خالی است: {'، '.join(missing)}")
    else:
        empty = [allowed[n].label for n, v in out.items() if v is None and allowed[n].required]
        if empty:
            raise ValidationError(f"فیلد اجباری را نمی‌توان خالی کرد: {'، '.join(empty)}")
    return out


def _apply_rules(ent: Entity, values: dict[str, Any], before: dict[str, Any] | None) -> None:
    """قواعد خاص هر موجودیت (محاسبات خودکار)."""
    merged = {**(before or {}), **values}
    if ent.name == "invoices":
        if "net_amount" not in values or values["net_amount"] is None:
            if "gross_amount" in values or "deductions" in values or before is None:
                gross = merged.get("gross_amount") or 0
                values["net_amount"] = gross - (merged.get("deductions") or 0)
    if ent.name == "tasks":
        if "status" in values:
            if values["status"] == "done" and (before or {}).get("status") != "done":
                values["completed_at"] = now_str()
            elif values["status"] != "done":
                values["completed_at"] = None
        if "remind_at" in values and values["remind_at"] != (before or {}).get("remind_at"):
            values["reminded"] = 0


# ───────────────────────── عملیات ─────────────────────────


def _select_sql(ent: Entity) -> str:
    if ent.has_project and ent.name != "projects":
        # زیرپرس‌وجو تا نام ستون‌ها (مثل status) در ORDER BY مبهم نشوند
        return (
            f"SELECT * FROM (SELECT x.*, p.name AS project_name FROM {ent.name} x "
            f"LEFT JOIN projects p ON p.id = x.project_id) t"
        )
    return f"SELECT t.* FROM {ent.name} t"


def get_entity(name: str) -> Entity:
    try:
        return ENTITIES[name]
    except KeyError:
        raise NotFound(f"موجودیت ناشناخته: {name}") from None


def get(conn: sqlite3.Connection, entity: str, rec_id: int) -> dict[str, Any]:
    ent = get_entity(entity)
    row = conn.execute(f"{_select_sql(ent)} WHERE t.id = ?", (int(rec_id),)).fetchone()
    if not row:
        raise NotFound(f"{ent.label} با شناسه {rec_id} پیدا نشد")
    return dict(row)


def create(conn: sqlite3.Connection, entity: str, data: dict[str, Any]) -> dict[str, Any]:
    ent = get_entity(entity)
    values = _clean(conn, ent, data, partial=False)
    _apply_rules(ent, values, None)
    for f in ent.fields:  # مقدار پیش‌فرض فیلدهای سیستمی
        if f.system and f.default is not None:
            values.setdefault(f.name, f.default)
    values["created_at"] = values["updated_at"] = now_str()
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {ent.name} ({cols}) VALUES ({marks})", list(values.values()))
    return get(conn, entity, cur.lastrowid)


def update(conn: sqlite3.Connection, entity: str, rec_id: int, data: dict[str, Any]) -> dict[str, Any]:
    ent = get_entity(entity)
    before = get(conn, entity, rec_id)
    values = _clean(conn, ent, data, partial=True)
    if not values:
        return before
    _apply_rules(ent, values, before)
    values["updated_at"] = now_str()
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {ent.name} SET {sets} WHERE id = ?", [*values.values(), int(rec_id)])
    return get(conn, entity, rec_id)


def delete(conn: sqlite3.Connection, entity: str, rec_id: int) -> dict[str, Any]:
    ent = get_entity(entity)
    row = get(conn, entity, rec_id)
    conn.execute(f"DELETE FROM {ent.name} WHERE id = ?", (int(rec_id),))
    return row


def list_records(
    conn: sqlite3.Connection,
    entity: str,
    *,
    filters: dict[str, Any] | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    exclude_status: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """جستجو با فیلترهای برابری، متن آزاد و بازه‌ی تاریخ (روی فیلد تاریخ اصلی موجودیت)."""
    ent = get_entity(entity)
    where, params = [], []
    for key, value in (filters or {}).items():
        if value is None or value == "":
            continue
        f = ent.field(key) if key != "id" else Field("id", "شناسه", "int")
        if isinstance(value, (list, tuple)):
            coerced = [_coerce(conn, f, v) for v in value]
            where.append(f"t.{key} IN ({', '.join('?' for _ in coerced)})")
            params += coerced
        else:
            where.append(f"t.{key} = ?")
            params.append(_coerce(conn, f, value))
    if search:
        cols = ent.search_fields or (ent.title_field,)
        where.append("(" + " OR ".join(f"t.{c} LIKE ?" for c in cols) + ")")
        params += [f"%{search.strip()}%"] * len(cols)
    if ent.date_field and (date_from or date_to):
        try:
            if date_from:
                where.append(f"t.{ent.date_field} >= ?")
                params.append(jalali.normalize(date_from))
            if date_to:
                where.append(f"t.{ent.date_field} <= ?")
                params.append(jalali.normalize(date_to))
        except ValueError as e:
            raise ValidationError(str(e)) from None
    if exclude_status and any(f.name == "status" for f in ent.fields):
        where.append(f"t.status NOT IN ({', '.join('?' for _ in exclude_status)})")
        params += exclude_status
    sql = _select_sql(ent)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {ent.order_by} LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    return [dict(r) for r in conn.execute(sql, params)]



def label_of(entity: str, field_name: str, value: Any) -> str:
    f = ENTITIES[entity].field(field_name)
    return f.choices.get(value, value or "")


def schema() -> dict[str, Any]:
    """تعریف موجودیت‌ها برای فرانت‌اند."""
    return {
        name: {
            "label": e.label,
            "label_plural": e.label_plural,
            "title_field": e.title_field,
            "date_field": e.date_field,
            "fields": [
                {
                    "name": f.name, "label": f.label, "type": f.type, "required": f.required,
                    "choices": f.choices, "default": f.default, "help": f.help, "system": f.system,
                }
                for f in e.fields
            ],
        }
        for name, e in ENTITIES.items()
    }


# ───────────────────────── تاریخچه‌ی گفتگو و تنظیمات ─────────────────────────


def add_chat(conn: sqlite3.Connection, channel: str, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO chat_messages (channel, role, content, created_at) VALUES (?, ?, ?, ?)",
        (channel, role, content, now_str()),
    )


def chat_history(conn: sqlite3.Connection, channel: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT role, content, created_at FROM chat_messages WHERE channel = ? ORDER BY id DESC LIMIT ?",
        (channel, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_chat(conn: sqlite3.Connection, channel: str) -> None:
    conn.execute("DELETE FROM chat_messages WHERE channel = ?", (channel,))


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
