"""وب‌سرور CivilDesk: API برای برنامه‌ی وب + اجرای ربات تلگرام در پس‌زمینه.

اجرا:  uvicorn civildesk.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, db, services, telegram_bot
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("civildesk")

STATIC = Path(__file__).parent / "static"
_basic = HTTPBasic(auto_error=False)


def require_auth(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not settings.web_password:
        return
    if creds is None or not secrets.compare_digest(creds.password.encode(), settings.web_password.encode()):
        raise HTTPException(401, "نیاز به ورود", headers={"WWW-Authenticate": 'Basic realm="CivilDesk"'})


@asynccontextmanager
async def lifespan(_: FastAPI):
    with db.connect():
        pass  # ساخت جدول‌ها
    bot = None if os.getenv("CIVILDESK_NO_BOT") else telegram_bot.start_in_background()
    yield
    if bot:
        bot.stop()


app = FastAPI(title="CivilDesk", lifespan=lifespan, dependencies=[Depends(require_auth)])


@app.exception_handler(db.ValidationError)
async def _validation(_: Request, e: db.ValidationError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(db.NotFound)
async def _not_found(_: Request, e: db.NotFound):
    return JSONResponse({"detail": str(e)}, status_code=404)


# ───────────────────────── صفحات ─────────────────────────


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ───────────────────────── API عمومی ─────────────────────────


@app.get("/api/meta")
def meta():
    return {
        "today": db.today_str(),
        "now": db.now_str(),
        "currency": settings.currency,
        "owner": settings.owner_name,
        "schema": db.schema(),
        "ai_enabled": bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")),
        "telegram_enabled": bool(settings.telegram_token),
    }


@app.get("/api/dashboard")
def dashboard():
    with db.connect() as conn:
        return services.dashboard(conn)


@app.get("/api/brief")
def brief():
    with db.connect() as conn:
        return {"text": services.daily_brief_text(conn)}


@app.get("/api/finance/summary")
def finance(date_from: str | None = None, date_to: str | None = None, project_id: int | None = None):
    with db.connect() as conn:
        return services.finance_summary(conn, date_from, date_to, project_id)


@app.get("/api/projects/{project_id}/overview")
def project_overview(project_id: int):
    with db.connect() as conn:
        return services.project_overview(conn, project_id)


@app.get("/api/export")
def export_all():
    """پشتیبان کامل همه‌ی داده‌ها به صورت JSON."""
    with db.connect() as conn:
        data = {name: db.list_records(conn, name, limit=1000000) for name in db.ENTITIES}
    return JSONResponse(
        data, headers={"Content-Disposition": f'attachment; filename="civildesk-{db.today_str().replace("/", "-")}.json"'}
    )


# ───────────────────────── گفتگو با دستیار ─────────────────────────


class ChatIn(BaseModel):
    message: str


@app.post("/api/chat")
def chat(body: ChatIn):
    if not body.message.strip():
        raise HTTPException(400, "پیام خالی است")
    try:
        result = agent.chat("web", body.message.strip())
    except anthropic.AuthenticationError:
        raise HTTPException(503, "کلید API کلود (ANTHROPIC_API_KEY) تنظیم نشده یا نامعتبر است") from None
    except anthropic.RateLimitError:
        raise HTTPException(429, "محدودیت تعداد درخواست؛ کمی بعد دوباره امتحان کنید") from None
    except anthropic.APIConnectionError:
        raise HTTPException(502, "اتصال به سرور Claude برقرار نشد") from None
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"خطای Claude: {e.message}") from None
    return {"reply": result.reply, "actions": result.actions}


@app.get("/api/chat/history")
def chat_history():
    with db.connect() as conn:
        return db.chat_history(conn, "web", 200)


@app.delete("/api/chat/history")
def chat_clear():
    with db.connect() as conn:
        db.clear_chat(conn, "web")
    return {"ok": True}


# ───────────────────────── CRUD عمومی برای همه‌ی موجودیت‌ها ─────────────────────────


def _entity(name: str) -> db.Entity:
    if name not in db.ENTITIES:
        raise HTTPException(404, "یافت نشد")
    return db.ENTITIES[name]


@app.get("/api/{entity}")
def list_entity(
    entity: str,
    request: Request,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_closed: bool = True,
    limit: int = 300,
):
    ent = _entity(entity)
    field_names = {f.name for f in ent.fields} | {"id"}
    filters: dict[str, Any] = {}
    for key, value in request.query_params.multi_items():
        if key in field_names and value != "":
            filters.setdefault(key, []).append(value)
    closed = {"tasks": ["done", "cancelled"], "projects": ["closed"]}.get(entity, [])
    with db.connect() as conn:
        return db.list_records(
            conn, entity, filters=filters, search=search, date_from=date_from, date_to=date_to,
            exclude_status=None if include_closed else closed, limit=limit,
        )


@app.post("/api/{entity}", status_code=201)
def create_entity(entity: str, data: dict[str, Any]):
    _entity(entity)
    with db.connect() as conn:
        return db.create(conn, entity, data)


@app.get("/api/{entity}/{rec_id}")
def get_entity(entity: str, rec_id: int):
    _entity(entity)
    with db.connect() as conn:
        return db.get(conn, entity, rec_id)


@app.patch("/api/{entity}/{rec_id}")
def update_entity(entity: str, rec_id: int, data: dict[str, Any]):
    _entity(entity)
    with db.connect() as conn:
        return db.update(conn, entity, rec_id, data)


@app.delete("/api/{entity}/{rec_id}")
def delete_entity(entity: str, rec_id: int):
    _entity(entity)
    with db.connect() as conn:
        db.delete(conn, entity, rec_id)
    return {"ok": True}
