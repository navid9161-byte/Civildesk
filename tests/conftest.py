import os
import tempfile

# پیش از import برنامه: پایگاه داده‌ی موقت و خاموش بودن ربات
_tmp = tempfile.mkdtemp()
os.environ["CIVILDESK_DB"] = os.path.join(_tmp, "test.db")
os.environ["CIVILDESK_NO_BOT"] = "1"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("CIVILDESK_PASSWORD", None)

import pytest  # noqa: E402

from civildesk import db  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    with db.connect() as conn:
        for name in list(db.ENTITIES)[::-1]:
            conn.execute(f"DELETE FROM {name}")
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM kv")
    yield
