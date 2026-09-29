"""ربات تلگرام: گفتگو با دستیار، دستورات سریع، یادآوری وظایف و گزارش صبحگاهی.

با long polling کار می‌کند (نیازی به دامنه و وبهوک نیست) و در یک نخ پس‌زمینه کنار وب‌سرور اجرا می‌شود.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from . import agent, db, services
from .config import settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000

HELP = """سلام! من دستیار سیویل‌دسک هستم. هر چیزی را به زبان ساده بنویس، مثلاً:
• «فردا ساعت ۱۰ بازدید آرماتوربندی سقف سوم پروژه ولیعصر، یک ساعت قبلش یادم بنداز»
• «گزارش امروز کارگاه ولیعصر: ۳ بنا و ۶ کارگر، بتن‌ریزی فونداسیون بلوک B، ۴۰ متر مکعب بتن C25 رسید»
• «صورت‌وضعیت شماره ۴ ولیعصر رو ۲.۵ میلیارد ریال ثبت کن، امروز ارسال شد»
• «این ماه چقدر هزینه کردم؟»

دستورات:
/today — خلاصه‌ی امروز
/tasks — وظایف باز
/reset — شروع گفتگوی تازه
/id — نمایش شناسه‌ی چت"""


class TelegramBot:
    def __init__(self, token: str, allowed: list[str]):
        self.token = token
        self.allowed = set(allowed)
        self.http = httpx.Client(timeout=httpx.Timeout(70.0, connect=15.0))
        self._stop = threading.Event()
        self._offset = 0

    # ── API ──
    def call(self, method: str, **params: Any) -> Any:
        r = self.http.post(API.format(token=self.token, method=method), json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def send(self, chat_id: int | str, text: str) -> None:
        text = text.replace("**", "").strip() or "—"
        for i in range(0, len(text), MAX_LEN):
            self.call("sendMessage", chat_id=chat_id, text=text[i : i + MAX_LEN])

    def broadcast(self, text: str) -> None:
        for chat_id in self.allowed:
            try:
                self.send(chat_id, text)
            except Exception:
                log.exception("ارسال به %s ناموفق بود", chat_id)

    # ── پردازش پیام ──
    def handle(self, msg: dict[str, Any]) -> None:
        chat_id = str(msg["chat"]["id"])
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/id"):
            self.send(chat_id, f"شناسه‌ی این چت: {chat_id}")
            return
        if chat_id not in self.allowed:
            self.send(
                chat_id,
                f"دسترسی ندارید. برای فعال‌سازی، این شناسه را در TELEGRAM_ALLOWED_CHAT_IDS قرار دهید: {chat_id}",
            )
            return
        if not text:
            self.send(chat_id, "فعلاً فقط پیام متنی را می‌فهمم.")
            return
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self.send(chat_id, HELP)
        elif cmd == "/today":
            with db.connect() as conn:
                self.send(chat_id, services.daily_brief_text(conn))
        elif cmd == "/tasks":
            with db.connect() as conn:
                rows = db.list_records(conn, "tasks", exclude_status=["done", "cancelled"], limit=40)
            lines = [
                f"#{t['id']} {t['title']}"
                + (f" — {t['due_date']}" if t["due_date"] else "")
                + (f" [{t['project_name']}]" if t.get("project_name") else "")
                for t in rows
            ]
            self.send(chat_id, "وظایف باز:\n" + "\n".join(lines) if lines else "هیچ وظیفه‌ی بازی نداری 🎉")
        elif cmd == "/reset":
            with db.connect() as conn:
                db.clear_chat(conn, f"tg:{chat_id}")
            self.send(chat_id, "گفتگو از نو شروع شد.")
        else:
            try:
                self.call("sendChatAction", chat_id=chat_id, action="typing")
            except Exception:
                pass
            try:
                result = agent.chat(f"tg:{chat_id}", text)
                self.send(chat_id, result.reply)
            except Exception as e:
                log.exception("خطای دستیار")
                self.send(chat_id, f"⚠️ خطا در ارتباط با دستیار: {e}")

    def poll_forever(self) -> None:
        log.info("ربات تلگرام شروع به کار کرد")
        while not self._stop.is_set():
            try:
                updates = self.call("getUpdates", offset=self._offset, timeout=50, allowed_updates=["message"])
                for u in updates:
                    self._offset = u["update_id"] + 1
                    if "message" in u:
                        try:
                            self.handle(u["message"])
                        except Exception:
                            log.exception("خطا در پردازش پیام")
            except Exception:
                log.exception("خطای getUpdates؛ تلاش دوباره تا ۱۰ ثانیه‌ی دیگر")
                self._stop.wait(10)

    def stop(self) -> None:
        self._stop.set()


# ───────────────────────── زمان‌بندی: یادآوری‌ها و گزارش صبحگاهی ─────────────────────────


def run_scheduler_once(bot: TelegramBot) -> None:
    with db.connect() as conn:
        for t in services.due_reminders(conn):
            text = f"⏰ یادآوری: {t['title']}"
            if t.get("project_name"):
                text += f"\nپروژه: {t['project_name']}"
            if t.get("due_date"):
                text += f"\nمهلت: {t['due_date']} {t.get('due_time') or ''}".rstrip()
            if t.get("description"):
                text += f"\n{t['description']}"
            bot.broadcast(text)
            services.mark_reminded(conn, t["id"])

        now = settings.now()
        today = db.today_str()
        hh, mm = (int(x) for x in settings.daily_brief_time.split(":"))
        minutes_after = (now.hour * 60 + now.minute) - (hh * 60 + mm)
        # فقط در بازه‌ی ۳ ساعت بعد از زمان تعیین‌شده (تا اگر سرور شب روشن شد، گزارش صبح نفرستد)
        if 0 <= minutes_after < 180 and db.kv_get(conn, "last_brief") != today:
            db.kv_set(conn, "last_brief", today)
            bot.broadcast(services.daily_brief_text(conn))


def scheduler_loop(bot: TelegramBot, interval: float = 30) -> None:
    while not bot._stop.is_set():
        try:
            run_scheduler_once(bot)
        except Exception:
            log.exception("خطای زمان‌بند")
        bot._stop.wait(interval)


def start_in_background() -> TelegramBot | None:
    if not settings.telegram_token:
        log.info("TELEGRAM_BOT_TOKEN تنظیم نشده؛ ربات تلگرام غیرفعال است")
        return None
    bot = TelegramBot(settings.telegram_token, settings.telegram_allowed)
    threading.Thread(target=bot.poll_forever, name="telegram-poll", daemon=True).start()
    threading.Thread(target=scheduler_loop, args=(bot,), name="scheduler", daemon=True).start()
    return bot


if __name__ == "__main__":  # اجرای مستقل: python -m civildesk.telegram_bot
    logging.basicConfig(level=logging.INFO)
    b = start_in_background()
    if b is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN را تنظیم کنید")
    while True:
        time.sleep(3600)
