"""送礼订单的两个**状态流转**写描述符族（T39 取消 / T40 支付）—— F20 追加。

口径与「预约操作」逐项同构：**都从只读 T37 取那一笔**、都不直查库。

| 意图 | 工具 | 档位 | 资金 |
|---|---|---|---|
| `gift_order_cancel`（取消订单） | T39 `cancel_gift_order` | **L1**（只解锁，不动余额） | `available += total` |
| `gift_order_pay`（支付订单） | T40 `pay_gift_order` | **L2**（要验证码：真扣款） | `balance -= total` |
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card
from agent._write_common import Prepared, ShapeResult, _code_of
from tools.schemas import ToolResult

#: 档位
CANCEL_TIER, PAY_TIER = "L1", "L2"
#: 多笔未支付订单时的追问
PICK_AMBIGUOUS = "您有多笔未支付的送礼订单，请说明是哪一笔（可以说出订单号或收货人）。"


def _pending_orders() -> tuple[list[dict], str | None]:
    """只读 T37 取「created」订单；返回 (items, 错误码)。"""
    from tools import _gift_order
    listed = _gift_order.list_gift_orders("created")
    if not listed.ok:
        return [], _code_of(listed)
    return list(listed.facts.get("items") or []), None


def _pick(slots: Mapping) -> tuple[dict | None, str | None, list[str], str]:
    """挑一笔未支付订单：给了 `order_id`（界面按钮）就用它；否则一笔→直接定，多笔→追问。"""
    filled = dict(slots)
    items, code = _pending_orders()
    if code is not None:
        return None, code, [], "取不到您的送礼订单"
    if filled.get("order_id"):
        row = next((it for it in items if it["order_id"] == filled["order_id"]), None)
        if row is None:
            return None, "NOT_FOUND", [], "这笔订单已不在未支付状态"
        return row, None, [], ""
    if not items:
        return None, "NOT_FOUND", [], "您没有未支付的送礼订单"
    if len(items) > 1:
        return None, None, ["order_id"], PICK_AMBIGUOUS
    return items[0], None, [], ""


def _shape(slots: Mapping) -> ShapeResult:
    """SLOT_FILL（取消/支付共用）：挑一笔未支付订单。"""
    filled = dict(slots)
    row, code, missing, clarify = _pick(filled)
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message=clarify)
    if row is None:
        return ShapeResult(filled=filled, missing=missing, clarify=clarify)
    filled["order_id"] = row["order_id"]
    filled["amount_cents"] = row["total"]
    return ShapeResult(filled=filled)


def _prepare(slots_filled: dict, tier: str) -> Prepared:
    """只读那一笔（不调工具）：事实包给卡面（订单号/收货人/金额/状态）。"""
    items, code = _pending_orders()
    if code is not None:
        return Prepared(ok=False, error_code=code, message="取不到您的送礼订单")
    row = next((it for it in items if it["order_id"] == slots_filled.get("order_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="这笔订单已不在未支付状态")
    return Prepared(ok=True, facts={**row, "tier": tier}, credential="")


def _prepare_cancel(filled: dict) -> Prepared:
    return _prepare(filled, CANCEL_TIER)


def _prepare_pay(filled: dict) -> Prepared:
    return _prepare(filled, PAY_TIER)


def _card(intent: str, filled: dict, facts: Mapping, tier: str,
          requires_otp: bool) -> confirm_card.CardInput:
    """取消/支付确认卡（同一个卡面，按意图换标题与说明）。"""
    paying = intent == "gift_order_pay"
    return confirm_card.CardInput(
        title="支付订单确认卡" if paying else "取消订单确认卡",
        intent_cn="支付订单" if paying else "取消订单",
        rows=[("订单号", str(facts["order_id"])),
              ("收货人", str(facts["contact"])),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("送达日期", str(facts["gift_date"])),
              ("说明", "从储蓄账户余额中真实扣款" if paying else "解锁已冻结的金额，余额不动")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "支付订单" if paying else "取消订单"))


def _plain(intent: str, filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    paying = intent == "gift_order_pay"
    return (f"请确认：{'支付' if paying else '取消'}订单 {facts.get('order_id')}"
            f"（{facts.get('contact')}，{facts.get('amount_yuan')} 元），权限档 {tier}。")


def _execute_cancel(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T39** 取消（解锁资金，余额不动）。L1，无 OTP。"""
    del otp
    from tools import _gift_order_ops
    return _gift_order_ops.cancel_gift_order(str(confirmation.filled["order_id"]))


def _execute_pay(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T40** 支付（真扣款）。L2，OTP 已由 write_flow 校验。"""
    del otp
    from tools import _gift_order_ops
    return _gift_order_ops.pay_gift_order(str(confirmation.filled["order_id"]))


# ---------------- 状态推进（发货 / 完成，L1）（2026-10-24 人类规格 5 状态） ----------------

ADVANCE_TIER = "L1"


def _shape_advance(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：哪一笔 + 发什么动作（ship / complete）。"""
    filled = dict(slots)
    action = str(filled.get("action") or "").strip()
    word = {"发货": "ship", "已发货": "ship", "完成": "complete", "已完成": "complete"}.get(action, action)
    if word in ("ship", "complete"):
        filled["action"] = word
    items, code = _pending_orders()
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message="取不到您的送礼订单")
    ready = [it for it in items if it["status"] == ("paid" if word == "ship" else "shipped")]
    if not ready:                                          # 退回全量列表 → 交给 prepare 给出准确状态
        ready = items
    if filled.get("order_id"):
        chosen = next((it for it in ready if it["order_id"] == filled["order_id"]), None)
        if chosen is None:
            return ShapeResult(filled=filled, error_code="NOT_FOUND", message="找不到这笔订单")
    elif len(ready) == 1:
        chosen = ready[0]
    elif not ready:
        return ShapeResult(filled=filled, error_code="NOT_FOUND", message="您没有可推进的送礼订单")
    else:
        return ShapeResult(filled=filled, missing=["order_id"],
                           clarify="您有多笔订单，请说明是哪一笔（可以报订单号）。")
    filled["order_id"] = chosen["order_id"]
    if "action" not in filled:
        return ShapeResult(filled=filled, missing=["action"],
                           clarify="请说明是「标记发货」还是「标记完成」。")
    return ShapeResult(filled=filled)


def _all_orders() -> tuple[list[dict], str | None]:
    """只读 T37 取**全部**订单（推进动作要能报出当前状态，不能只看 created）。"""
    from tools import _gift_order
    listed = _gift_order.list_gift_orders()
    if not listed.ok:
        return [], _code_of(listed)
    return list(listed.facts.get("items") or []), None


def _prepare_advance(filled: dict) -> Prepared:
    items, code = _all_orders()
    if code is not None:
        return Prepared(ok=False, error_code=code, message="取不到您的送礼订单")
    row = next((it for it in items if it["order_id"] == filled.get("order_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到这笔订单")
    return Prepared(ok=True, facts={**row, "tier": ADVANCE_TIER}, credential="")


def _card_advance(intent: str, filled: dict, facts: Mapping, tier: str,
                  requires_otp: bool) -> confirm_card.CardInput:
    label = "标记已发货" if str(filled.get("action")) == "ship" else "标记已完成"
    return confirm_card.CardInput(
        title="订单状态确认卡", intent_cn=label,
        rows=[("订单号", str(facts["order_id"])), ("收货人", str(facts["contact"])),
              ("金额", f"{facts['amount_yuan']} 元"), ("当前状态", str(facts["status_cn"])),
              ("动作", label + "（不动钱，只改状态）")],
        tier=tier, requires_otp=requires_otp, risk_note=confirm_card.risk_note([], label))


def _plain_advance(filled: dict, facts: Mapping, tier: str) -> str:
    return (f"请确认：把订单 {facts.get('order_id')}（{facts.get('status_cn')}）"
            f"{'标记已发货' if str(filled.get('action')) == 'ship' else '标记已完成'}，权限档 {tier}。")


def _execute_advance(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    del otp
    from tools import _gift_order_ops
    return _gift_order_ops.advance_gift_order(str(confirmation.filled["order_id"]),
                                              str(confirmation.filled["action"]))
