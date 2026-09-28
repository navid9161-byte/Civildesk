"""دستیار هوشمند: Claude با ابزارهایی که مستقیماً روی داده‌های CivilDesk کار می‌کنند.

حلقه‌ی ابزار به صورت دستی نوشته شده تا تاریخچه‌ی گفتگو در پایگاه داده ذخیره شود
و یک کانال (وب یا هر چت تلگرام) گفتگوی جداگانه‌ی خودش را داشته باشد.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

from . import db, jalali, services
from .config import settings

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """تو «سیویل‌دسک»، دستیار شخصی یک مهندس عمران هستی. کارت این است که کارهای روزمره، \
وظایف، پروژه‌ها، گزارش‌های روزانه‌ی کارگاه، صورت‌وضعیت‌ها، دریافت و پرداخت‌ها، مخاطبین و یادداشت‌های او را \
ثبت، پیگیری و خلاصه کنی و در کارهای فنی و اداری (نامه‌نگاری، صورتجلسه، برآورد، نکات آیین‌نامه‌ای) کمکش کنی.

قواعد کار:
- همیشه به فارسی روان و مختصر جواب بده. از قالب‌بندی ساده استفاده کن (فهرست کوتاه)، نه جدول‌های بزرگ.
- همه‌ی تاریخ‌ها شمسی و با قالب 1405/07/05 هستند؛ زمان‌ها «1405/07/05 14:30». تاریخ امروز و روزهای \
پیش رو در ابتدای هر پیام کاربر آمده؛ عبارت‌هایی مثل «فردا»، «پنج‌شنبه»، «هفته‌ی بعد» را با همان جدول \
به تاریخ دقیق تبدیل کن. برای محاسبه‌ی فاصله‌ی تاریخ‌ها از ابزار date_calc استفاده کن و خودت حدس نزن.
- مبالغ به {currency} ذخیره می‌شوند. اگر کاربر «تومان» گفت، در ۱۰ ضرب کن تا ریال شود (مگر واحد پیش‌فرض تومان باشد). \
«میلیون» و «میلیارد» را به عدد کامل تبدیل کن.
- وقتی کاربر چیزی را برای ثبت یا پیگیری می‌گوید، مستقیم با ابزار مناسب ثبتش کن و بعد کوتاه تأیید کن \
(چه چیزی، با چه تاریخی، در کدام پروژه). برای موارد مبهم کوچک، منطقی‌ترین حالت را انتخاب کن و در تأیید بگو.
- اگر نام پروژه آمد، اول با projects_search شناسه‌اش را پیدا کن. اگر پروژه‌ای با آن نام نبود، بپرس که ساخته شود یا نه.
- برای یادآوری، فیلد remind_at وظیفه را تنظیم کن؛ سیستم در آن زمان در تلگرام پیام می‌فرستد. اگر ساعت یادآوری \
گفته نشده ولی مهلت دارد، یادآوری را ساعت ۸ صبح همان روز بگذار.
- قبل از هر حذف، از کاربر تأیید صریح بگیر. ویرایش‌ها را بدون پرسیدن انجام بده.
- برای «امروز چه کار دارم؟» یا «وضعیت کلی» از ابزار dashboard استفاده کن؛ برای یک پروژه از project_overview.
- اطلاعات را از خودت نساز؛ اگر داده‌ای ثبت نشده، بگو.
- برای پرسش‌های مربوط به مقررات، آیین‌نامه، قرارداد یا هر سندی که کاربر بارگذاری کرده، اول با documents_search \
بگرد و پاسخ را با ذکر نام سند و شماره‌ی صفحه بده.
- در پاسخ‌های فنی (محاسبات، ضوابط مبحث‌های مقررات ملی ساختمان، نشریه‌ی ۵۵ و فهرست‌بها) دقیق باش و اگر \
مطمئن نیستی بگو که باید با متن مرجع چک شود.
"""


# ───────────────────────── ساخت تعریف ابزارها از روی موجودیت‌ها ─────────────────────────


def _prop(f: db.Field, nullable: bool) -> dict[str, Any]:
    desc = f.label + (f" — {f.help}" if f.help else "")
    if f.type in ("int", "money", "percent", "project"):
        p: dict[str, Any] = {"type": "integer"}
        if f.type == "money":
            desc += f" (به {settings.currency})"
        if f.type == "project":
            desc = "شناسه‌ی پروژه (از projects_search)"
    elif f.type == "choice":
        p = {"type": "string", "enum": list(f.choices)}
        desc += " — " + "، ".join(f"{k}={v}" for k, v in f.choices.items())
    else:
        p = {"type": "string"}
        if f.type == "date":
            desc += " (شمسی، YYYY/MM/DD)"
        elif f.type == "datetime":
            desc += " (شمسی، YYYY/MM/DD HH:MM)"
    if nullable:
        p["type"] = [p["type"], "null"]
        if "enum" in p:
            p["enum"] = [*p["enum"], None]
    p["description"] = desc
    return p


def _entity_tools(ent: db.Entity) -> list[dict[str, Any]]:
    fields = ent.editable
    create_props = {f.name: _prop(f, nullable=False) for f in fields}
    update_props = {"id": {"type": "integer", "description": f"شناسه‌ی {ent.label}"}}
    update_props.update({f.name: _prop(f, nullable=True) for f in fields})
    search_props: dict[str, Any] = {
        "search": {"type": "string", "description": "جستجوی متنی آزاد"},
        "id": {"type": "integer", "description": "شناسه‌ی یک رکورد مشخص"},
        "limit": {"type": "integer", "description": "حداکثر تعداد نتیجه (پیش‌فرض ۳۰)"},
    }
    if ent.has_project:
        search_props["project_id"] = {"type": "integer", "description": "فقط رکوردهای این پروژه"}
    for f in fields:
        if f.type == "choice":
            search_props[f.name] = {
                "type": "array", "items": {"type": "string", "enum": list(f.choices)},
                "description": f"فیلتر {f.label}",
            }
    if ent.date_field:
        label = ent.field(ent.date_field).label
        search_props["date_from"] = {"type": "string", "description": f"از {label} (شمسی)"}
        search_props["date_to"] = {"type": "string", "description": f"تا {label} (شمسی)"}
    if any(f.name == "status" for f in fields):
        search_props["include_closed"] = {
            "type": "boolean",
            "description": "موارد بسته/انجام‌شده/لغوشده هم بیاید (پیش‌فرض: فقط موارد باز برای وظایف)",
        }
    return [
        {
            "name": f"{ent.name}_create",
            "description": f"ثبت {ent.label} جدید.",
            "input_schema": {
                "type": "object", "properties": create_props,
                "required": [f.name for f in fields if f.required], "additionalProperties": False,
            },
        },
        {
            "name": f"{ent.name}_update",
            "description": f"ویرایش {ent.label}؛ فقط فیلدهایی که باید تغییر کنند را بفرست (null = پاک کردن).",
            "input_schema": {
                "type": "object", "properties": update_props, "required": ["id"], "additionalProperties": False,
            },
        },
        {
            "name": f"{ent.name}_search",
            "description": f"جستجو و فهرست {ent.label_plural}.",
            "input_schema": {"type": "object", "properties": search_props, "additionalProperties": False},
        },
        {
            "name": f"{ent.name}_delete",
            "description": f"حذف {ent.label} (فقط بعد از تأیید صریح کاربر).",
            "input_schema": {
                "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"],
                "additionalProperties": False,
            },
        },
    ]


EXTRA_TOOLS: list[dict[str, Any]] = [
    {
        "name": "dashboard",
        "description": "نمای کلی امروز: کارهای عقب‌افتاده، امروز و هفت روز آینده، پروژه‌های فعال، "
                       "صورت‌وضعیت‌های در انتظار پرداخت و جمع دریافت/پرداخت ماه جاری.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "project_overview",
        "description": "خلاصه‌ی کامل یک پروژه: پیشرفت فیزیکی و زمانی، وظایف باز، صورت‌وضعیت‌ها، هزینه‌ها، آخرین گزارش‌ها.",
        "input_schema": {
            "type": "object", "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"], "additionalProperties": False,
        },
    },
    {
        "name": "finance_summary",
        "description": "جمع دریافتی‌ها و هزینه‌ها (به تفکیک دسته و پروژه) در یک بازه، به‌همراه مطالبات صورت‌وضعیت‌ها.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "شمسی"},
                "date_to": {"type": "string", "description": "شمسی"},
                "project_id": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "date_calc",
        "description": "محاسبات تاریخ شمسی: روز هفته و معادل میلادی یک تاریخ، افزودن/کم کردن روز، فاصله‌ی دو تاریخ.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "تاریخ شمسی مبنا"},
                "add_days": {"type": "integer", "description": "تعداد روز برای افزودن (منفی برای کم کردن)"},
                "until": {"type": "string", "description": "تاریخ شمسی دوم برای محاسبه‌ی فاصله"},
            },
            "required": ["date"],
            "additionalProperties": False,
        },
    },
]


EXTRA_TOOLS.append({
    "name": "documents_search",
    "description": "جستجو در کتابخانه‌ی اسناد کاربر (PDFهای مقررات، نشریات، قراردادها و ...). "
                   "مرتبط‌ترین بندها را با نام سند و شماره‌ی صفحه برمی‌گرداند.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "پرسش یا واژه‌های کلیدی"},
            "limit": {"type": "integer", "description": "تعداد نتیجه (پیش‌فرض ۶)"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
})


def build_tools() -> list[dict[str, Any]]:
    tools = [t for ent in db.ENTITIES.values() for t in _entity_tools(ent)] + EXTRA_TOOLS
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}  # کش تعریف ابزارها
    return tools


TOOLS = build_tools()


# ───────────────────────── اجرای ابزارها ─────────────────────────


def _compact(obj: Any) -> Any:
    """حذف مقادیر خالی برای کم کردن حجم ورودی مدل."""
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [_compact(v) for v in obj]
    return obj


def execute_tool(name: str, args: dict[str, Any]) -> Any:
    """اجرای یک ابزار و برگرداندن نتیجه (قابل تبدیل به JSON). خطاها به صورت استثنا بالا می‌روند."""
    with db.connect() as conn:
        if name == "dashboard":
            return services.dashboard(conn)
        if name == "project_overview":
            return services.project_overview(conn, args["project_id"])
        if name == "finance_summary":
            return services.finance_summary(conn, **args)
        if name == "documents_search":
            from . import documents

            found = documents.search(args["query"], limit=args.get("limit") or 6)
            return [
                {"source": f"{r['title']} — صفحه {r['page']}", "text": r["text"], "ocr": r["method"] == "ocr"}
                for r in found["results"]
            ]
        if name == "date_calc":
            base = jalali.normalize(args["date"])
            g = jalali.parse(base)
            out: dict[str, Any] = {"date": base, "weekday": jalali.weekday_fa(g), "gregorian": g.isoformat()}
            if args.get("add_days") is not None:
                res = jalali.add_days(base, int(args["add_days"]))
                out["result"] = res
                out["result_weekday"] = jalali.weekday_fa(jalali.parse(res))
            if args.get("until"):
                out["days_until"] = jalali.days_between(base, args["until"])
            return out

        entity, _, action = name.rpartition("_")
        if entity not in db.ENTITIES:
            raise ValueError(f"ابزار ناشناخته: {name}")
        if action == "create":
            return db.create(conn, entity, args)
        if action == "update":
            args = dict(args)
            rec_id = args.pop("id")
            return db.update(conn, entity, rec_id, args)
        if action == "delete":
            return {"deleted": db.delete(conn, entity, args["id"])}
        if action == "search":
            args = dict(args)
            ent = db.ENTITIES[entity]
            include_closed = args.pop("include_closed", entity != "tasks")
            closed = {"tasks": ["done", "cancelled"], "projects": ["closed"]}.get(entity, [])
            filters = {k: args.pop(k) for k in list(args) if k in ("id", "project_id") or (
                k in {f.name for f in ent.fields})}
            rows = db.list_records(
                conn, entity,
                filters=filters,
                search=args.get("search"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                exclude_status=None if include_closed else closed,
                limit=args.get("limit") or 30,
            )
            return {"count": len(rows), "items": rows}
        raise ValueError(f"ابزار ناشناخته: {name}")


# ───────────────────────── حلقه‌ی گفتگو ─────────────────────────


def time_context(now: dt.datetime | None = None) -> str:
    now = now or settings.now()
    today = now.date()
    days = []
    for i in range(-1, 14):
        d = today + dt.timedelta(days=i)
        tag = {-1: " (دیروز)", 0: " (امروز)", 1: " (فردا)"}.get(i, "")
        days.append(f"{jalali.weekday_fa(d)} {jalali.to_jalali(d)}{tag}")
    return (
        f"[زمان فعلی: {jalali.to_jalali(now, with_time=True)} — {jalali.long_format(today)}]\n"
        f"[تقویم: {' | '.join(days)}]"
    )


@dataclass
class ChatResult:
    reply: str
    actions: list[str] = field(default_factory=list)


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _call_model(client: anthropic.Anthropic, messages: list[dict[str, Any]]):
    kwargs: dict[str, Any] = dict(
        model=settings.claude_model,
        max_tokens=16000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT.format(currency=settings.currency),
            "cache_control": {"type": "ephemeral"},
        }],
        tools=TOOLS,
        messages=messages,
        thinking={"type": "adaptive"},
    )
    if settings.claude_effort:
        kwargs["output_config"] = {"effort": settings.claude_effort}
    if settings.claude_fallbacks:
        return client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
    return client.messages.create(**kwargs)


def chat(
    channel: str,
    text: str,
    client: anthropic.Anthropic | None = None,
    on_tool: Callable[[str], None] | None = None,
) -> ChatResult:
    """یک پیام کاربر را پردازش می‌کند و پاسخ نهایی را برمی‌گرداند.

    تاریخچه فقط شامل متن نهایی پیام‌ها ذخیره می‌شود؛ جزئیات فراخوانی ابزارها داخل همین نوبت می‌ماند.
    """
    client = client or get_client()
    with db.connect() as conn:
        history = db.chat_history(conn, channel, settings.chat_history_turns * 2)
    messages: list[dict[str, Any]] = []
    for h in history:
        if messages and messages[-1]["role"] == h["role"]:  # نقش‌ها باید یکی‌درمیان باشند
            messages[-1]["content"] += "\n\n" + h["content"]
        else:
            messages.append({"role": h["role"], "content": h["content"]})
    if messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    user_content = f"{time_context()}\n\n{text}"
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n\n" + user_content
    else:
        messages.append({"role": "user", "content": user_content})

    actions: list[str] = []
    reply = ""
    for _ in range(MAX_TOOL_ROUNDS):
        response = _call_model(client, messages)
        if response.stop_reason == "refusal":
            reply = "متأسفم، نمی‌توانم به این درخواست پاسخ بدهم."
            break
        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "tool_use" and tool_uses:
            results = []
            for tu in tool_uses:
                if on_tool:
                    on_tool(tu.name)
                try:
                    out = execute_tool(tu.name, dict(tu.input or {}))
                    if not tu.name.endswith(("_search", "dashboard", "overview", "summary", "calc")):
                        actions.append(tu.name)
                    results.append({
                        "type": "tool_result", "tool_use_id": tu.id,
                        "content": json.dumps(_compact(out), ensure_ascii=False, default=str),
                    })
                except (db.ValidationError, db.NotFound, ValueError, KeyError, TypeError) as e:
                    results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": f"خطا: {e}",
                    })
            messages.append({"role": "user", "content": results})
            continue
        reply = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "max_tokens":
            reply += "\n\n(پاسخ به دلیل طولانی بودن ناتمام ماند.)"
        break
    else:
        reply = reply or "کار بیش از حد طول کشید؛ لطفاً درخواست را ساده‌تر یا مرحله‌به‌مرحله بگو."

    reply = reply or "انجام شد."
    with db.connect() as conn:
        db.add_chat(conn, channel, "user", text)
        db.add_chat(conn, channel, "assistant", reply)
    return ChatResult(reply=reply, actions=actions)
