"""F16 追加：**查预约列表**（T24 只读 + `render_lists.render_schedules` 表格）。

| 验收点 | 用例 |
|---|---|
| 空列表 → 自己的句子，不套空表格 | `test_empty_schedules_say_so` |
| 有预约 → 表格四列（时间/收款人/金额/状态） | `test_schedule_table_renders_rows` |
| 编排层：一句话 → scheduled_list → T24 | `test_orchestration_lists_schedules` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, render_lists, session_memory
from tests.conftest import raw

SESSION = "sess-schedlist"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _facts(items: list[dict]) -> dict:
    return {"total_count": len(items), "items": items}


def test_empty_schedules_say_so() -> None:
    """零预约 → 一句引导，不印空表（与 `T_CARD_NO_CARD` 同口径）。"""
    text = render_lists.render_schedules(_facts([]))
    assert "还没有待执行的预约" in text
    assert "|" not in text


def test_schedule_table_renders_rows() -> None:
    """有预约 → 表格四列；状态说中文；金额/时间原样取自 facts。"""
    text = render_lists.render_schedules(_facts([
        {"schedule_id": "sch_1", "payee_name": "王五", "execute_at": "2026-10-30T09:00",
         "status": "pending", "amount": 10000, "amount_yuan": "100.00"},
    ]))
    assert "| 预约时间 | 收款人 | 金额（元） | 状态 |" in text
    assert "2026-10-30 09:00" in text and "王五" in text and "100.00" in text
    assert "待执行" in text


def _fake(monkeypatch, intent: str = "scheduled_list", slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_orchestration_lists_schedules(wired, monkeypatch) -> None:
    """先建一笔预约，再问「我的预约有哪些」→ 走 T24 并出表格。"""
    _fake(monkeypatch, intent="transfer_scheduled",
          slots={"payee": "王五", "amount": "100", "schedule": "2026-10-30 09:00"})
    card = orchestrator.handle("2026-10-30 09:00 给王五转 100 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True
    assert raw(wired, "SELECT 1 FROM scheduled_transfer WHERE status = 'pending'")

    _fake(monkeypatch)
    listed = orchestrator.handle("我的预约有哪些", session_id=SESSION)
    assert listed.intent == "scheduled_list" and "list_scheduled" in listed.tool_calls
    assert "| 预约时间 | 收款人 | 金额（元） | 状态 |" in listed.reply
    assert "王五" in listed.reply and "待执行" in listed.reply
