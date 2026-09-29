import os
import tempfile

# پیش از import برنامه: پایگاه داده‌ی موقت و خاموش بودن ربات
_tmp = tempfile.mkdtemp()
os.environ["CIVILDESK_DB"] = os.path.join(_tmp, "test.db")
os.environ["CIVILDESK_NO_BOT"] = "1"
os.environ["CIVILDESK_NO_WORKER"] = "1"  # پردازش اسناد در آزمون‌ها هم‌زمان انجام می‌شود
os.environ["CIVILDESK_NO_EMBEDDINGS"] = "1"  # بدون دانلود مدل
os.environ["CIVILDESK_DOCS_DIR"] = os.path.join(_tmp, "docs")
for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CIVILDESK_LLM_BASE_URL"):
    os.environ.pop(_k, None)
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
        conn.execute("DELETE FROM doc_fts")
        conn.execute("DELETE FROM doc_chunks")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM kv")
    yield
