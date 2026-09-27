from civildesk import db, telegram_bot


class FakeBot(telegram_bot.TelegramBot):
    def __init__(self, allowed):
        super().__init__("TOKEN", allowed)
        self.sent = []

    def call(self, method, **params):
        if method == "sendMessage":
            self.sent.append((str(params["chat_id"]), params["text"]))
        return {}


def test_unauthorized_chat_is_rejected():
    bot = FakeBot(["111"])
    bot.handle({"chat": {"id": 999}, "text": "سلام"})
    assert "دسترسی ندارید" in bot.sent[0][1] and "999" in bot.sent[0][1]


def test_commands():
    bot = FakeBot(["111"])
    with db.connect() as conn:
        db.create(conn, "tasks", {"title": "خرید سیمان"})
    bot.handle({"chat": {"id": 111}, "text": "/tasks"})
    assert "خرید سیمان" in bot.sent[-1][1]
    bot.handle({"chat": {"id": 111}, "text": "/today"})
    assert "صبح بخیر" in bot.sent[-1][1]


def test_scheduler_sends_reminders_once():
    bot = FakeBot(["111", "222"])
    with db.connect() as conn:
        db.create(conn, "tasks", {"title": "تمدید پروانه", "remind_at": "1400/01/01 08:00"})
        db.kv_set(conn, "last_brief", db.today_str())  # گزارش صبحگاهی امروز قبلاً رفته
    telegram_bot.run_scheduler_once(bot)
    telegram_bot.run_scheduler_once(bot)
    reminders = [s for s in bot.sent if "تمدید پروانه" in s[1]]
    assert sorted(c for c, _ in reminders) == ["111", "222"]
