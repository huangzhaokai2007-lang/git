"""理财申购/赎回族写描述符（T15）：`confirm_ref` 族，**OTP 闸门在编排层**。

F12/F13 接入写路径；核心契约类型与共享工具见 `agent/_write_common.py`，
注册表见 `agent/write_intents.py`。

**为什么 OTP 在本层**：`trade_wealth(product_id, action, amount, confirm_ref)` 的签名
**没有 `otp` 参数**（规格 §2 T15 冻结），故 `WriteSpec.otp_in_tool=False`，
闸门落在 `agent/write_flow._execute`。

**金额从哪来**：T15 需要 `amount`，而确认卡是唯一携带它的对象 ——
`_execute_trade` 读 `confirmation.amount_cents`。
"""

from __future__ import annotations

from functools import partial
from typing import Mapping

from agent import confirm_card
from agent._write_common import Prepared, ShapeResult, _code_of, yuan_to_cents
from tools import wealth
from tools.schemas import ToolResult

BUY, REDEEM = "buy", "redeem"
_VERB = {BUY: "申购", REDEEM: "赎回"}


def _term_cn(days: object) -> str:
    """期限中文：0 = 活期（货币基金），其余按天。"""
    return "活期" if int(days) == 0 else f"{int(days)} 天"


def _shape_wealth(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`amount`（元）→ 整数分；产品名落到 `product_id` 供工具层定位。

    槽位键用 **`product`（产品名）而不是 `product_id`**：实测带测评历史时，LLM 面对
    `product_id` 会误以为要填 ID 而**整项不填**（4 次里 2 次），改成 `product` 后 6/6 命中。
    仍兼容旧键 `product_id`（历史用例与旧脚本）。

    金额认不出 / 非正数 → 记 `amount` 缺槽（不猜 0 元）；产品没给 → 记 `product_id` 缺槽
    （`templates.SLOT_CN` 已把它映射成「产品」）。
    """
    filled, missing = dict(slots), []
    product = str(filled.get("product") or filled.get("product_id") or "").strip()
    if not product:
        missing.append("product_id")
    else:
        filled["product_id"] = product
    if filled.get("amount_cents") is None:
        amount = filled.get("amount")
        cents = yuan_to_cents(amount) if amount is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    return ShapeResult(filled=filled, missing=missing)


def _prepare_trade(action: str, filled: dict) -> Prepared:
    """PRECHECK：`preview_trade` **只读校验**（复用 `_buy_plan` / `_redeem_plan` 同一套规则）
    + 签发确认凭证。

    顺手把 `product_id` **规范化成真实 id**：用户说的是产品名（如「稳盈货币基金」），
    而 `trade_wealth` 按 id 精确查 —— 不回填就会在执行时 NOT_FOUND。凭证绑的也是这个 id。
    """
    preview = wealth.preview_trade(str(filled["product_id"]), action, filled["amount_cents"])
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    filled["product_id"] = str(preview.facts["product_id"])
    return Prepared(ok=True, facts=preview.facts,
                    credential=wealth.issue_confirm_ref(wealth.TRADE_ACTION, filled["product_id"]))


def _card_trade(action: str, intent: str, filled: dict, facts: Mapping, tier: str,
                requires_otp: bool) -> confirm_card.CardInput:
    """申购/赎回确认卡：产品、等级、年化、期限、起购额、金额**全部来自 facts**（铁律 2）。"""
    verb = _VERB[action]
    rows = [("产品", str(facts["product_name"])),
            ("风险等级", str(facts.get("product_risk_label") or facts["product_risk_level"])),
            ("预期年化", f"{facts['yield']}（非保本，收益随市场波动）"),
            ("期限", _term_cn(facts["term_days"])),
            ("起购金额", f"{facts['min_amount_yuan']} 元"),
            (f"{verb}金额", f"{facts['amount_yuan']} 元")]
    if facts.get("risk_desc"):                       # 让用户明确知道这个等级大概会亏多少
        rows.append(("风险说明", str(facts["risk_desc"])))
    if action == BUY:
        rows.append(("您的风险等级", str(facts.get("user_risk_label") or facts["user_risk_level"])))
    else:
        rows.append(("可赎持仓", f"{facts['holding_amount_yuan']} 元"))
    return confirm_card.CardInput(
        title=f"理财{verb}确认卡", intent_cn=f"理财{verb}", rows=rows,
        tier=tier, requires_otp=requires_otp, risk_note=confirm_card.risk_note([], verb))


def _plain_trade(action: str, filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本：确认卡出现 facts 之外的数字时的降级回执。"""
    verb = _VERB[action]
    return (f"请确认：{verb} {facts['product_name']}（{facts['product_risk_level']} 级）"
            f"{facts['amount_yuan']} 元，权限档 {tier}。")


def _execute_trade(action: str, confirmation: confirm_card.Confirmation,
                   otp: str | None) -> ToolResult:
    """OTP 闸门**已在编排层判过**（`otp_in_tool=False`）；金额与凭证都在确认卡上。"""
    del otp
    return wealth.trade_wealth(confirmation.target_id, action, confirmation.amount_cents,
                               confirmation.credential)


def specs() -> dict[str, object]:
    """F12/F13 的描述符（`action` 用 `partial` 绑进四个钩子，其余共用一份实现）。"""
    from agent.write_intents import WriteSpec                 # 函数内 import：避免模块级环

    built: dict[str, object] = {}
    for action in (BUY, REDEEM):
        verb = _VERB[action]
        built[f"wealth_{action}"] = WriteSpec(
            intent=f"wealth_{action}", intent_cn=f"理财{verb}", title=f"理财{verb}确认卡",
            credential_kind="confirm_ref", otp_in_tool=False,
            missing=("product_id", "amount"), tools=("preview_trade",), execute_tool="trade_wealth",
            target_key="product_id", what=verb,
            shape=_shape_wealth, prepare=partial(_prepare_trade, action),
            card=partial(_card_trade, action), plain=partial(_plain_trade, action),
            execute=partial(_execute_trade, action))
    return built
