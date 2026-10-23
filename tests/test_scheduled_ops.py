"""F16 追加：**立即执行预约（T32）** 与 **改预约时间（T33）**。

| 验收点 | 用例 |
|---|---|
| T33 改时间：pending 可改、新时间晚于现在 | `test_reschedule_updates_pending_schedule` |
| T33 撞规则：改到过去 → INVALID_ARGUMENT；非 pending → INVALID_STATE | `test_reschedule_rejects_past_and_settled` |
| T32 立即执行：pending → 扣款 + 预约置 success | `test_run_now_settles_pending_schedule` |
| T32 撞规则：非 pending → INVALID_STATE | `test_run_now_rejects_settled_schedule` |
| 编排层：一句话 → 确认卡 → 执行（改时间不扣款 / 立即执行扣款） | `test_orchestration_modify_and_run` |
"""

from __future__ import annotations

import datetime
import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import _transfer_schedule_ops as ops

SESSION = "sess-schedops"
WANGWU = "王五"
NEW_TIME = "2026-11-05 09:00"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, intent: str, slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def _make_schedule(wired, monkeypatch, when: str = "2026-10-30T09:00") -> str:
    """先经编排层落一条 pending 预约，返回 schedule_id。"""
    _fake(monkeypatch, "transfer_scheduled",
          {"payee": WANGWU, "amount": "100", "schedule": when.replace("T", " ")})
    orchestrator.handle(f"{when.replace('T', ' ')} 给{WANGWU}转 100 元", session_id=SESSION)
    assert orchestrator.handle("确认", session_id=SESSION).executed is True
    row = raw(wired, "SELECT id FROM scheduled_transfer WHERE status = 'pending'")[0]
    return row["id"]


def test_reschedule_updates_pending_schedule(wired, monkeypatch) -> None:
    """T33：改时间只动 execute_at，金额/收款人不动。"""
    sid = _make_schedule(wired, monkeypatch)
    before = raw(wired, "SELECT amount, payee_id FROM scheduled_transfer WHERE id = ?", (sid,))[0]
    result = ops.reschedule_scheduled(sid, NEW_TIME)
    assert result.ok is True and result.data["execute_at"].startswith("2026-11-05")
    row = raw(wired, "SELECT execute_at, amount, payee_id, status FROM scheduled_transfer WHERE id = ?",
              (sid,))[0]
    assert row["execute_at"].startswith("2026-11-05") and row["status"] == "pending"
    assert row["amount"] == before["amount"] and row["payee_id"] == before["payee_id"]


def test_reschedule_rejects_past_and_settled(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch)
    past = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat(timespec="minutes")
    bad = ops.reschedule_scheduled(sid, past)
    assert bad.ok is False and "晚于现在" in bad.message
    assert ops.run_scheduled_now(sid).ok is True                       # 结算掉
    again = ops.reschedule_scheduled(sid, NEW_TIME)
    assert again.ok is False and "不能改时间" in again.message


def test_run_now_settles_pending_schedule(wired, monkeypatch) -> None:
    """T32：立即执行 → 真的扣款 + 预约置 success（复用 T22 的结算）。"""
    sid = _make_schedule(wired, monkeypatch)
    balance_before = raw(wired, "SELECT balance FROM account WHERE type = 'savings'")[0]["balance"]
    result = ops.run_scheduled_now(sid)
    assert result.ok is True and result.data["status"] == "success"
    status = raw(wired, "SELECT status FROM scheduled_transfer WHERE id = ?", (sid,))[0]["status"]
    assert status == "success"
    balance_after = raw(wired, "SELECT balance FROM account WHERE type = 'savings'")[0]["balance"]
    assert balance_after == balance_before - 10_000                    # 100 元
    assert raw(wired, "SELECT 1 FROM audit_log WHERE tool = 'run_scheduled_now'")


def test_run_now_rejects_settled_schedule(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch)
    assert ops.run_scheduled_now(sid).ok is True
    again = ops.run_scheduled_now(sid)
    assert again.ok is False and "不能再立即执行" in again.message


def test_orchestration_modify_and_run(wired, monkeypatch) -> None:
    """改时间：卡面「原→新」→ 确认 → 库里的时间变了、钱包没动。"""
    sid = _make_schedule(wired, monkeypatch)
    balance_before = raw(wired, "SELECT balance FROM account WHERE type = 'savings'")[0]["balance"]
    _fake(monkeypatch, "scheduled_modify", {"schedule": NEW_TIME})
    card = orchestrator.handle(f"把那笔预约改到 {NEW_TIME}", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "原预约时间" in card.reply and "新预约时间" in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and done.tool_calls == ["reschedule_scheduled"]
    assert raw(wired, "SELECT execute_at FROM scheduled_transfer WHERE id = ?", (sid,))[0]["execute_at"].startswith("2026-11-05")
    assert raw(wired, "SELECT balance FROM account WHERE type = 'savings'")[0]["balance"] == balance_before

    _fake(monkeypatch, "scheduled_run")
    run_card = orchestrator.handle("立即执行那笔预约", session_id=SESSION)
    assert "CONFIRM_CARD" in run_card.states and "立即执行预约确认卡" in run_card.reply
    ran = orchestrator.handle("确认", session_id=SESSION)
    assert ran.executed is True and ran.tool_calls == ["run_scheduled_now"]
    assert raw(wired, "SELECT status FROM scheduled_transfer WHERE id = ?", (sid,))[0]["status"] == "success"
