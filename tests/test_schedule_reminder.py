"""T-24h 执行前提醒的用例。"""
import datetime
import json

import pytest

from agent import confirm_card, llm, orchestrator, scheduled_runner, session_memory
from tests.conftest import raw

SESSION = "sess-remind"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    scheduled_runner.reset_reminders()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    scheduled_runner.reset_reminders()


def _fake(monkeypatch, intent: str, slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def _schedule(wired, monkeypatch, clock, hours_ahead: int) -> str:
    """直接落一条 pending 预约（跳过卡片流程，专注提醒逻辑）。"""
    when = (clock() + datetime.timedelta(hours=hours_ahead)).isoformat(timespec="minutes")
    raw(wired, "SELECT 1")                                   # 确认库可用
    from data import dao
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (WANGWU,))[0]["id"]
    dao.connection().execute(
        "INSERT INTO scheduled_transfer (id, user_id, payee_id, amount, execute_at, status, tier,"
        " created_at) VALUES ('sch_test', 'u_zhangsan_0001', ?, 10000, ?, 'pending', 'L1', ?)",
        (pid, when, clock().isoformat(timespec="seconds")))
    dao.connection().commit()
    return when


def test_reminder_fires_within_24h_once(wired, monkeypatch, clock) -> None:
    """24 小时内要执行的预约 → 提醒一次；再问一次不再重复。"""
    when = _schedule(wired, monkeypatch, clock, hours_ahead=6)
    _fake(monkeypatch, "balance_query")
    first = orchestrator.handle("查一下余额", session_id=SESSION)
    assert "【执行前提醒】" in first.reply and WANGWU in first.reply and "100.00" in first.reply
    assert when[:10] in first.reply
    second = orchestrator.handle("查一下余额", session_id=SESSION)
    assert "【执行前提醒】" not in second.reply                    # 同一笔只提醒一次


def test_no_reminder_outside_window(wired, monkeypatch, clock) -> None:
    """超过 24 小时 → 不提醒。"""
    _schedule(wired, monkeypatch, clock, hours_ahead=48)
    _fake(monkeypatch, "balance_query")
    turn = orchestrator.handle("查一下余额", session_id=SESSION)
    assert "【执行前提醒】" not in turn.reply


def test_reminder_mentions_cancel_hint(wired, monkeypatch, clock) -> None:
    """提醒句里要有「改/取消」的出口（人类规格：可一键取消）。"""
    _schedule(wired, monkeypatch, clock, hours_ahead=3)
    _fake(monkeypatch, "balance_query")
    turn = orchestrator.handle("查一下余额", session_id=SESSION)
    assert "取消预约" in turn.reply
