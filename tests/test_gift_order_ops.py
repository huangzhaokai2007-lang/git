"""F20 追加：**送礼订单状态流转**（T39 取消解锁 / T40 支付扣款）。

| 验收点 | 用例 |
|---|---|
| T39：取消 → `cancelled` + 解锁（available 回补、balance 不动） | `test_cancel_releases_locked_funds` |
| T40：支付 → `paid` + 真扣款（balance 减少） | `test_pay_charges_balance` |
| 撞规则：重复取消不二次解锁；非 created → INVALID_STATE | `test_repeat_transitions_are_rejected` |
| 编排层：一句话取消 → 确认卡（L1）→ 生效 | `test_orchestration_cancel_order` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import _gift_order as orders
from tools import _gift_order_ops as ops

SESSION = "sess-giftops"
CONTACT = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _money(wired) -> tuple[int, int]:
    row = raw(wired, "SELECT available, balance FROM account WHERE type = 'savings'")[0]
    return row["available"], row["balance"]


def _created_order(wired, monkeypatch, budget: str = "500", date: str = "2026-10-30") -> str:
    """经编排层下一笔送礼订单（T16 锁款 + T38 落单），返回订单号。"""
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "gift_plan", "confidence": 0.95,
                                          "slots": {"contact": CONTACT, "date": date,
                                                    "budget": budget}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    orchestrator.handle(f"给{CONTACT}送个礼物，{date} 送达，预算 {budget} 元", session_id=SESSION)
    orchestrator.handle("确认", session_id=SESSION)
    done = orchestrator.handle("123456", session_id=SESSION)
    assert done.executed is True and "订单号 ord_" in done.reply
    return raw(wired, "SELECT id FROM gift_order ORDER BY rowid DESC LIMIT 1")[0]["id"]


def test_cancel_releases_locked_funds(wired, monkeypatch) -> None:
    """取消：status→cancelled，available 回补、balance 不变。"""
    order_id = _created_order(wired, monkeypatch)
    total = raw(wired, "SELECT total FROM gift_order WHERE id = ?", (order_id,))[0]["total"]
    before_available, before_balance = _money(wired)
    result = ops.cancel_gift_order(order_id)
    assert result.ok is True and result.data["status"] == "cancelled"
    after_available, after_balance = _money(wired)
    assert after_available == before_available + total and after_balance == before_balance


def test_pay_charges_balance(wired, monkeypatch) -> None:
    """支付：status→paid，balance 真扣、available 不变（T16 已扣过）。"""
    order_id = _created_order(wired, monkeypatch)
    total = raw(wired, "SELECT total FROM gift_order WHERE id = ?", (order_id,))[0]["total"]
    before_available, before_balance = _money(wired)
    result = ops.pay_gift_order(order_id)
    assert result.ok is True and result.data["status"] == "paid"
    after_available, after_balance = _money(wired)
    assert after_balance == before_balance - total and after_available == before_available
    assert raw(wired, "SELECT status FROM gift_order WHERE id = ?", (order_id,))[0]["status"] == "paid"


def test_repeat_transitions_are_rejected(wired, monkeypatch) -> None:
    """重复取消不会二次解锁；已支付的不能再取消。"""
    order_id = _created_order(wired, monkeypatch)
    assert ops.cancel_gift_order(order_id).ok is True
    available_once, _ = _money(wired)
    again = ops.cancel_gift_order(order_id)
    assert again.ok is False and "不能取消" in again.message
    assert _money(wired)[0] == available_once                     # 没二次解锁
    assert ops.pay_gift_order(order_id).ok is False                # 已取消 → 不能支付


def test_orchestration_cancel_order(wired, monkeypatch) -> None:
    """「取消那个礼物订单」→ L1 确认卡 → 确认即生效（免除验证码）。"""
    order_id = _created_order(wired, monkeypatch)

    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "gift_order_cancel", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    card = orchestrator.handle("取消那个礼物订单", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "取消订单确认卡" in card.reply and order_id in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and done.tool_calls == ["cancel_gift_order"]
    assert raw(wired, "SELECT status FROM gift_order WHERE id = ?", (order_id,))[0]["status"] == "cancelled"


# ---------------- 2026-10-24 人类规格：5 状态流转 ----------------

def _order(wired, monkeypatch, pay: bool = False) -> str:
    """下一单；pay=True 时再支付（created → paid）。"""
    order_id = _created_order(wired, monkeypatch)
    if pay:
        assert ops.pay_gift_order(order_id).ok is True
    return order_id


def test_advance_ship_then_complete(wired, monkeypatch) -> None:
    """paid → shipped → completed；只有对的状态能推进（L1，不动钱）。"""
    order_id = _order(wired, monkeypatch, pay=True)
    available_before, balance_before = _money(wired)
    assert ops.advance_gift_order(order_id, "ship").ok is True
    assert raw(wired, "SELECT status FROM gift_order WHERE id = ?", (order_id,))[0]["status"] == "shipped"
    assert ops.advance_gift_order(order_id, "complete").ok is True
    assert raw(wired, "SELECT status FROM gift_order WHERE id = ?", (order_id,))[0]["status"] == "completed"
    assert _money(wired) == (available_before, balance_before)      # 全程不动钱
    assert ops.advance_gift_order(order_id, "ship").ok is False      # 已完成 → 不能再发货


def test_advance_rejects_wrong_state_and_action(wired, monkeypatch) -> None:
    order_id = _order(wired, monkeypatch)               # 待支付
    assert ops.advance_gift_order(order_id, "ship").ok is False      # 未支付不能发货
    assert ops.advance_gift_order(order_id, "bogus").ok is False
    paid = _order(wired, monkeypatch, pay=True)
    assert ops.advance_gift_order(paid, "complete").ok is False      # 未发货不能完成


def test_cancel_paid_order_refunds(wired, monkeypatch) -> None:
    """「未发货可取消」：已支付（未发货）取消 → **退款回余额**；已发货则不可取消。"""
    order_id = _order(wired, monkeypatch, pay=True)
    total = raw(wired, "SELECT total FROM gift_order WHERE id = ?", (order_id,))[0]["total"]
    _, balance_before = _money(wired)
    result = ops.cancel_gift_order(order_id)
    assert result.ok is True and "退款" in result.message
    assert _money(wired)[1] == balance_before + total
    shipped = _order(wired, monkeypatch, pay=True)
    assert ops.advance_gift_order(shipped, "ship").ok is True
    blocked = ops.cancel_gift_order(shipped)
    assert blocked.ok is False and "未发货才可取消" in blocked.message
