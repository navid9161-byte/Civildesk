import re
from types import SimpleNamespace as NS

from civildesk import agent, db


def test_tool_definitions_are_valid():
    names = [t["name"] for t in agent.TOOLS]
    assert len(names) == len(set(names))
    for t in agent.TOOLS:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", t["name"])
        assert t["input_schema"]["type"] == "object"
        for req in t["input_schema"].get("required", []):
            assert req in t["input_schema"]["properties"]


def test_execute_tools_roundtrip():
    p = agent.execute_tool("projects_create", {"name": "برج آفتاب", "employer": "شهرداری"})
    t = agent.execute_tool("tasks_create", {"title": "کنترل نقشه‌ها", "project_id": p["id"], "priority": "high"})
    res = agent.execute_tool("tasks_search", {"project_id": p["id"], "priority": ["high"]})
    assert res["count"] == 1 and res["items"][0]["id"] == t["id"]
    agent.execute_tool("tasks_update", {"id": t["id"], "status": "done"})
    assert agent.execute_tool("tasks_search", {})["count"] == 0
    assert agent.execute_tool("tasks_search", {"include_closed": True})["count"] == 1
    calc = agent.execute_tool("date_calc", {"date": "1405/07/05", "add_days": 10, "until": "1405/08/01"})
    assert calc["result"] == "1405/07/15" and calc["days_until"] == 26
    assert "overdue" in agent.execute_tool("dashboard", {})


class FakeMessages:
    """شبیه‌ساز API: اول یک ابزار صدا می‌زند، بعد جواب متنی می‌دهد."""

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return NS(stop_reason="tool_use", content=[
                NS(type="tool_use", id="tu_1", name="tasks_create",
                   input={"title": "خرید میلگرد", "due_date": "1405/07/06"}),
                NS(type="tool_use", id="tu_2", name="tasks_create", input={"title": ""}),
            ])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="ثبت شد.")])


def test_chat_loop_with_fake_client():
    fake = FakeMessages()
    client = NS(messages=fake, beta=NS(messages=fake))
    result = agent.chat("test", "فردا میلگرد بخر", client=client)
    assert result.reply == "ثبت شد."
    assert result.actions == ["tasks_create"]

    # فهرست پیام‌ها همان شیء است و پاسخ نهایی هم به انتهایش اضافه شده
    second = fake.calls[1]["messages"]
    assert second[-1]["role"] == "assistant"
    tool_results = second[-2]["content"]
    assert tool_results[0]["tool_use_id"] == "tu_1" and "is_error" not in tool_results[0]
    assert tool_results[1]["is_error"] is True
    assert "[زمان فعلی:" in second[0]["content"]

    with db.connect() as conn:
        assert db.list_records(conn, "tasks")[0]["title"] == "خرید میلگرد"
        hist = db.chat_history(conn, "test", 10)
    assert [h["role"] for h in hist] == ["user", "assistant"]
    assert hist[0]["content"] == "فردا میلگرد بخر"
