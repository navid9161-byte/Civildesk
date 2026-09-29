"""تنظیمات برنامه — همه از متغیرهای محیطی (یا فایل .env) خوانده می‌شوند."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv اختیاری است
    pass


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("CIVILDESK_DB", "data/civildesk.db")))
    timezone: str = field(default_factory=lambda: os.getenv("CIVILDESK_TZ", "Asia/Tehran"))
    currency: str = field(default_factory=lambda: os.getenv("CIVILDESK_CURRENCY", "ریال"))
    owner_name: str = field(default_factory=lambda: os.getenv("CIVILDESK_OWNER", "مهندس"))

    # Claude
    claude_model: str = field(default_factory=lambda: os.getenv("CIVILDESK_MODEL", "claude-opus-5"))
    claude_effort: str = field(default_factory=lambda: os.getenv("CIVILDESK_EFFORT", ""))
    claude_fallbacks: bool = field(default_factory=lambda: os.getenv("CIVILDESK_FALLBACKS", "1") != "0")
    chat_history_turns: int = field(default_factory=lambda: int(os.getenv("CHAT_HISTORY_TURNS", "20")))

    # وب
    web_password: str = field(default_factory=lambda: os.getenv("CIVILDESK_PASSWORD", ""))

    # تلگرام
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_allowed: list[str] = field(default_factory=lambda: _list("TELEGRAM_ALLOWED_CHAT_IDS"))
    daily_brief_time: str = field(default_factory=lambda: os.getenv("DAILY_BRIEF_TIME", "07:30"))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz)


settings = Settings()
