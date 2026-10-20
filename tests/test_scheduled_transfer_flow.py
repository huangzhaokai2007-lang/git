"""F16 编排层单测（Stage 2）：**能预约、到点才扣款、惰性结算、只扣一次**。

| F16 验收点 | 用例 |
|---|---|
| **预约时间到达前不能按即时转账扣款** | `test_confirming_a_scheduled_transfer_only_books_it`（确认后**余额一分没动**） |
| 到期才扣、重复触发只扣一次 | `test_lazy_settlement_debits_when_due_and_only_once` |
| 重启恢复（状态在库里） | 结算器每轮从库读 `pending` —— 同一条用例里连跑两轮覆盖 |
| 卡面不得写「实时到账」 | `test_card_never_claims_realtime_arrival` |
| 相对日不支持、不猜 | 见 `test_confirm_flow.py`（Stage 1 已覆盖） |

**时间控制**：`clock` 夹具钉住 `transfer._now`；`tick(hours=…)` 把时间推过预约时刻即可触发结算。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import confirm_card, llm, orchestrator

from tests.conftest import raw

SESSION = "sess-f16-flow"
WANGWU = "王五"
DUE = "2026-09-13T09:00"          # 晚于被钉住的 2026-09-12 12:00
AMOUNT_CENTS = 10_000


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def sched(schedule: str = DUE, payee: str = WANGWU, amount: str = "100.00") -> dict:
    return {"intent": "transfer_scheduled", "confidence": 0.95,
            "slots": {"payee": payee, "amount": amount, "schedule": schedule},
            "missing_slots": []}


def chitchat() -> dict:
    return {"intent": "smalltalk", "confidence": 0.9, "slots": {}, "missing_slots": []}


@pytest.fixture()
def wired(seeded: Path, monkeypatch: pytest.MonkeyPatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    yield seeded
    confirm_card.reset_state()


def account_of(path: Path) -> dict:
    return raw(path, "SELECT balance, available FROM account WHERE type = 'savings'"
                     " ORDER BY id LIMIT 1")[0]


def schedules_of(path: Path) -> list[dict]:
    return raw(path, "SELECT * FROM scheduled_transfer ORDER BY created_at, id")


def turn(monkeypatch: pytest.MonkeyPatch, text: str, script: list[dict]):
    fake_llm(monkeypatch, script)
    return orchestrator.handle(text, session_id=SESSION)


def book(monkeypatch: pytest.MonkeyPatch) -> None:
    """走完整写路径把预约落库（出卡 → 确认 → 执行）。"""
    card = turn(monkeypatch, "预约转账", [sched()])
    assert "CONFIRM_CARD" in card.states, card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True, done.reply


# ---------------- ① 确认只落预约，**一分钱不扣** ----------------

def test_confirming_a_scheduled_transfer_only_books_it(wired, clock, monkeypatch) -> None:
    before, before_txn = account_of(wired), len(raw(wired, "SELECT 1 FROM txn"))
    card = turn(monkeypatch, "预约转账", [sched()])
    assert "CONFIRM_CARD" in card.states
    assert "预约时间" in card.reply and card.executed is False
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and done.tool_calls == ["schedule_transfer"]
    assert account_of(wired) == before                                   # ← 余额与可用余额都没动
    assert len(raw(wired, "SELECT 1 FROM txn")) == before_txn            # ← 也没有流水
    rows = schedules_of(wired)
    assert len(rows) == 1 and rows[0]["status"] == "pending"


def test_card_never_claims_realtime_arrival(wired, clock, monkeypatch) -> None:
    """卡面**不许**出现即时转账的措辞（F16 修的就是「被理解成马上到账」）。"""
    card = turn(monkeypatch, "预约转账", [sched()])
    assert "实时到账" not in card.reply
    assert "预计到账" not in card.reply
    assert "到预约时间后才扣款" in card.reply


# ---------------- ② 到点才扣，且只扣一次 ----------------

def test_lazy_settlement_debits_when_due_and_only_once(wired, clock, monkeypatch) -> None:
    book(monkeypatch)
    before = account_of(wired)
    # 到点**之前**再聊一句：不结算、不扣款
    turn(monkeypatch, "你好", [chitchat()])
    assert account_of(wired) == before
    assert schedules_of(wired)[0]["status"] == "pending"
    # 把时间推过预约时刻 → 下一轮任意交互触发惰性结算
    clock.moment = clock.moment.replace(day=13, hour=9, minute=30)
    after = turn(monkeypatch, "你好", [chitchat()])
    assert "【预约转账已执行】" in after.reply                            # 告知句附在回执前面
    assert account_of(wired)["balance"] == before["balance"] - AMOUNT_CENTS
    assert schedules_of(wired)[0]["status"] == "success"
    # 再聊一句：幂等，不会再扣一次
    settled = account_of(wired)
    turn(monkeypatch, "你好", [chitchat()])
    assert account_of(wired) == settled


def test_no_settlement_writes_nothing_when_nothing_is_due(wired, clock, monkeypatch) -> None:
    """钩子对正常请求**零副作用**：没有到期预约时既不写表也不改余额。"""
    before, before_txn = account_of(wired), len(raw(wired, "SELECT 1 FROM txn"))
    before_audit = len(raw(wired, "SELECT 1 FROM audit_log"))
    turn(monkeypatch, "你好", [chitchat()])
    assert account_of(wired) == before
    assert len(raw(wired, "SELECT 1 FROM txn")) == before_txn
    assert schedules_of(wired) == []
    # 审计只多本次请求那一条（`scheduled_runner` 不额外写）
    assert len(raw(wired, "SELECT 1 FROM audit_log")) - before_audit <= 1


# ---------------- ③ 取消预约（Q4：L1、不要 OTP、不动钱） ----------------

def cancel_intent() -> dict:
    return {"intent": "scheduled_cancel", "confidence": 0.95, "slots": {}, "missing_slots": []}


def test_cancel_books_nothing_and_moves_no_money(wired, clock, monkeypatch) -> None:
    """走完整路径：**先预约 → 再取消** → 状态 cancelled、**余额一分没动**、L1 不要 OTP。"""
    book(monkeypatch)
    assert schedules_of(wired)[0]["status"] == "pending"
    before = account_of(wired)
    card = turn(monkeypatch, "取消那个定时转账", [cancel_intent()])
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "取消预约确认卡" in card.reply or "【取消预约" in card.reply
    assert "验证码" not in card.reply                      # Q4：不要 OTP
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and done.tool_calls == ["cancel_scheduled"]
    assert account_of(wired) == before                     # ← 取消不动钱
    assert schedules_of(wired)[0]["status"] == "cancelled"


def test_cancel_without_any_pending_schedule_is_reported(wired, clock, monkeypatch) -> None:
    """没有任何待执行预约 → 直接报「没有待执行的预约」，**不出确认卡**。"""
    step = turn(monkeypatch, "取消那个定时转账", [cancel_intent()])
    assert step.executed is False and "CONFIRM_CARD" not in step.states
    assert "没有待执行的预约" in step.reply
    assert schedules_of(wired) == []


def test_cancelled_schedule_is_never_settled(wired, clock, monkeypatch) -> None:
    """取消后即使过了预约时间，惰性结算也**不会扣款**（状态已不是 pending）。"""
    book(monkeypatch)
    turn(monkeypatch, "取消那个定时转账", [cancel_intent()])
    orchestrator.handle("确认", session_id=SESSION)
    before = account_of(wired)
    clock.moment = clock.moment.replace(day=13, hour=9, minute=30)     # 过了预约时刻
    turn(monkeypatch, "你好", [chitchat()])
    assert account_of(wired) == before
    assert schedules_of(wired)[0]["status"] == "cancelled"
