"""F16 追加：**循环预约**（T34 设周期 / T35 续下一期）。

| 验收点 | 用例 |
|---|---|
| 「每个月月末」→ monthly（确定性映射） | `test_repeat_words_normalize` |
| T34：pending 可设周期；非法周期拒绝 | `test_set_repeat_updates_pending` |
| T35：结算后生成下一期；**月末对齐**（1/31 → 2/28） | `test_next_occurrence_month_end_alignment` |
| T35：非循环 / 上期未成功 → 幂等无操作 | `test_next_occurrence_noop_for_non_repeat` |
| 编排层：一句话带「每月」→ 卡面有「循环」→ 确认 → 库里记 monthly | `test_orchestration_monthly_schedule` |
"""

from __future__ import annotations

import json

import pytest

from agent import _write_transfer, confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import _transfer_schedule_ops as ops

SESSION = "sess-repeat"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, slots: dict) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "transfer_scheduled", "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_repeat_words_normalize() -> None:
    """「每个月月末 / 每月 / 每周」→ 周期码；认不出 → None（按一次性，不打扰用户）。"""
    from agent._write_common import repeat_cycle
    assert repeat_cycle("每个月月末") == "monthly"
    assert repeat_cycle("每月") == "monthly"
    assert repeat_cycle("每周") == "weekly"
    assert repeat_cycle("就这一次") is None
    assert repeat_cycle(None) is None


def _make_schedule(wired, monkeypatch, when: str = "2026-10-31 09:00", repeat: str = "") -> str:
    slots = {"payee": WANGWU, "amount": "100", "schedule": when}
    if repeat:
        slots["repeat"] = repeat
    _fake(monkeypatch, slots)
    orchestrator.handle(f"{when} 给{WANGWU}转 100 元", session_id=SESSION)
    assert orchestrator.handle("确认", session_id=SESSION).executed is True
    return raw(wired, "SELECT id FROM scheduled_transfer WHERE status = 'pending' ORDER BY rowid DESC LIMIT 1")[0]["id"]


def test_set_repeat_updates_pending(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch)
    assert ops.set_schedule_repeat(sid, "monthly").ok is True
    assert raw(wired, "SELECT repeat_cycle FROM scheduled_transfer WHERE id = ?", (sid,))[0]["repeat_cycle"] == "monthly"
    assert ops.set_schedule_repeat(sid, "yearly").ok is False          # 只支持每月/每周


def test_next_occurrence_month_end_alignment(wired, monkeypatch) -> None:
    """1 月 31 日的 monthly → 下一期 2 月 28 日（月末对齐，不漏期）。"""
    assert ops._next_occurrence("2027-01-31T09:00", "monthly") == "2027-02-28T09:00"
    assert ops._next_occurrence("2026-10-31T09:00", "monthly") == "2026-11-30T09:00"
    assert ops._next_occurrence("2026-12-15T09:00", "monthly") == "2027-01-15T09:00"
    assert ops._next_occurrence("2026-10-31T09:00", "weekly") == "2026-11-07T09:00"


def test_next_occurrence_creates_next_pending(wired, monkeypatch) -> None:
    """结算成功后：T35 生成下一期 pending（同收款人/金额/档位，时间 = 下一月同日）。"""
    sid = _make_schedule(wired, monkeypatch, repeat="每个月")
    assert raw(wired, "SELECT repeat_cycle FROM scheduled_transfer WHERE id = ?", (sid,))[0]["repeat_cycle"] == "monthly"
    assert ops.run_scheduled_now(sid).ok is True                       # 首期立即结算
    result = ops.schedule_next_occurrence(sid)
    assert result.ok is True and result.data["next_id"]
    nxt = raw(wired, "SELECT * FROM scheduled_transfer WHERE id = ?", (result.data["next_id"],))[0]
    assert nxt["status"] == "pending" and nxt["repeat_cycle"] == "monthly"
    assert nxt["execute_at"].startswith("2026-11-30")                  # 10-31 → 11-30（月末对齐）
    again = ops.schedule_next_occurrence(sid)                          # 幂等：不重复插
    assert again.data["next_id"] == nxt["id"]


def test_next_occurrence_noop_for_non_repeat(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch)                           # 不带循环
    assert ops.run_scheduled_now(sid).ok is True
    result = ops.schedule_next_occurrence(sid)
    assert result.ok is True and result.data["next_id"] is None
    assert "非循环预约" in result.message


def test_orchestration_monthly_schedule(wired, monkeypatch) -> None:
    """「每个月月末给房东转 100」→ 卡面有「循环」行 → 确认后库里记 monthly。"""
    _fake(monkeypatch, {"payee": "房东", "amount": "100", "schedule": "2026-10-31 09:00",
                        "repeat": "每个月月末"})
    card = orchestrator.handle("每个月月末给房东转 100 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and "循环" in card.reply and "每月循环" in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and "每月循环" in done.reply
    row = raw(wired, "SELECT repeat_cycle FROM scheduled_transfer WHERE status = 'pending'")[0]
    assert row["repeat_cycle"] == "monthly"
