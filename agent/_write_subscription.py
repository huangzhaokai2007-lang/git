"""订阅取消族写描述符（T11）：`confirm_ref` 族，**OTP 闸门在编排层**。

F15/F20 拆卡时从 `agent/write_intents.py` 按意图族拆出；核心契约类型与共享工具见
`agent/_write_common.py`，注册表见 `agent/write_intents.py`。

**为什么 OTP 在本层**：`cancel_subscription(sub_id, confirm_ref)` 的签名**没有 `otp` 参数**
（规格 §2 T11 冻结），故 `WriteSpec.otp_in_tool=False`，闸门落在 `agent/write_flow._execute`。
依据 `tools/cross_scene.py`「确认与 OTP 落在编排层（同 T11/T15 的分工）」。
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card
from agent._write_common import Prepared, ShapeResult, _code_of, normalize_query
from tools import subscription

#: 订阅取消（T11）：工具层的动作名必须与 `tools/subscription.CANCEL_ACTION` 同值
CANCEL_ACTION = "cancel_subscription"
CANCEL_CN = "订阅取消"


def _shape_subscription(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`merchant` → `sub_id`（照 `resolve_payee` 先例）。

    唯一命中 → 补 `sub_id`；**重名 → 追问**（CLARIFY）；**零命中 → 报错**（NOT_FOUND）。
    这两条是 F04 的验收点，与转账「一律追问」的口径不同。
    """
    filled = dict(slots)
    if filled.get("sub_id"):
        return ShapeResult(filled=filled)
    query = normalize_query(filled.get("merchant"))
    if not query:
        return ShapeResult(filled=filled, missing=["merchant"])
    listed = subscription.list_subscriptions()
    if not listed.ok:
        return ShapeResult(filled=filled, error_code=_code_of(listed), message=listed.message)
    items = listed.facts.get("items") or []
    hits = [item for item in items if query in str(item.get("merchant") or "")]
    if not hits:
        return ShapeResult(filled=filled, error_code="NOT_FOUND", message="找不到匹配的订阅")
    if len(hits) > 1:
        return ShapeResult(filled=filled, missing=["merchant"])       # 重名：追问，绝不擅自选一个
    filled["sub_id"], filled["merchant"] = hits[0]["id"], hits[0]["merchant"]
    return ShapeResult(filled=filled)


def _prepare_subscription(filled: dict) -> Prepared:
    """订阅取消**没有** `preview_*`（T11 签名就是 `(sub_id, confirm_ref)`）：

    事实包取自 T10 的列表（F19 为 F04 提供事实 —— 卡面数字必须有来源），
    凭证由工具层私有约定 `issue_confirm_ref` 签发，绑定 `(action + target_id + 当前用户)`。
    """
    sub_id = filled["sub_id"]
    listed = subscription.list_subscriptions()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    item = next((row for row in (listed.facts.get("items") or []) if row.get("id") == sub_id), None)
    if item is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该订阅")
    facts = {"merchant": item["merchant"], "cycle": item["cycle"],
             "next_charge_date": item["next_charge_date"], "amount_yuan": item["amount_yuan"]}
    return Prepared(ok=True, facts=facts,
                    credential=subscription.issue_confirm_ref(CANCEL_ACTION, sub_id))


def _cycle_cn(cycle: object) -> str:
    """周期中文（与 `tools/subscription.CYCLE_LABELS` 同源，缺省原样回显）。"""
    return subscription.CYCLE_LABELS.get(str(cycle), str(cycle))


def _card_subscription(intent: str, filled: dict, facts: Mapping, tier: str,
                       requires_otp: bool) -> confirm_card.CardInput:
    """订阅取消确认卡：商户、金额、周期、下次扣费日**全部来自 facts**（T10 的事实包）。"""
    return confirm_card.CardInput(
        title="订阅取消确认卡", intent_cn=CANCEL_CN,
        rows=[("商户", str(facts["merchant"])),
              ("金额", f"{facts['amount_yuan']} 元／{_cycle_cn(facts['cycle'])}"),
              ("下次扣费", str(facts["next_charge_date"]))],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "取消订阅"))


def _plain_subscription(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（订阅版降级回执）。"""
    return (f"请确认：取消订阅 {facts['merchant']}（{facts['amount_yuan']} 元／"
            f"{_cycle_cn(facts['cycle'])}），权限档 {tier}。")


def _execute_subscription(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """T11 的 OTP 闸门**在编排层**（工具签名没有 otp 参数），本函数只把 `(sub_id, ref)` 交给工具层。"""
    del otp
    return subscription.cancel_subscription(confirmation.target_id, confirmation.credential)
