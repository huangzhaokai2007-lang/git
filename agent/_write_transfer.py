"""转账族写描述符（T6/T7）：`preview_token` 族，**OTP 校验与幂等都在工具层**。

F15/F20 拆卡时从 `agent/write_intents.py` 按意图族拆出；核心契约类型与共享工具见
`agent/_write_common.py`，注册表见 `agent/write_intents.py`。

**本族覆盖两个意图**：`transfer_single`（完整可用）与 `transfer_scheduled`（**F16 未接通，
当前为「止损」状态**：`shape` 一律走 `_shape_scheduled` 追问转即时，绝不降级成即时转账）。
`preview_transfer` 的 `schedule` 参数语义属 F16，见 `docs/superpowers/specs/2026-10-15-f16-scheduled-transfer-design.md`。
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card, payee_clarify
from agent._write_common import Prepared, ShapeResult, _code_of, yuan_to_cents
from tools import transfer

#: 转账类的中文名与卡标题（只做措辞，不含任何业务数字）
TRANSFER_CN = {"transfer_single": "单笔转账", "transfer_scheduled": "定时转账"}

#: F16 未接通期间的止损文案（纯措辞，不含任何业务数字）
SCHEDULED_UNSUPPORTED = ("定时转账还没接通（F16 排 W3），预约暂时无法受理。"
                         "如需立即转账，请说「立即给某某转多少钱」。")


def _shape_scheduled(slots: Mapping) -> ShapeResult:
    """F16 **止损**：定时转账一律追问转即时，**绝不降级成即时转账**。

    修复的问题（实机复现）：此前 `transfer_scheduled` 复用 `_shape_transfer` / `_prepare_transfer`，
    而 `_prepare_transfer` **不传 `schedule`** —— 于是「明天给王五转100元」会出**即时转账**
    确认卡（卡面写「预计到账：实时到账」），L1 档点一下确认**钱立刻出去**，预约日期被静默丢弃。
    这违反 F16 验收第 2 条「**预约时间到达前不能按即时转账扣款**」。

    止损口径（与完整方案 `docs/superpowers/specs/2026-10-15-f16-scheduled-transfer-design.md` 解耦）：
    **不论用户有没有给时间，一律不出确认卡** —— 给了时间我们也做不到（F16 未实现）；
    没给时间说明模型识别到了时间却没抽出来。两种情况都**不能**当成即时转账。

    走 `ShapeResult.clarify`（追问渠道）而非 `error_code`：后者会被 `templates.tool_error`
    套上「这次没能查到您要的信息」，用在这里语义不对；追问还能给出可执行的下一步。
    """
    return ShapeResult(filled=dict(slots), missing=["schedule"], clarify=SCHEDULED_UNSUPPORTED)


def _shape_transfer(slots: Mapping) -> ShapeResult:
    """SLOT_FILL 对齐工具层签名：`payee`（名字/手机号）→ `payee_id`；`amount`（元）→ 整数分。

    没找到或同名多个 → 记缺槽（**绝不擅自选一个**）；金额认不出 → 记缺槽。

    缺收款人时把 `resolve_payee` 的**事实包**交给 `payee_clarify.ask_for` 生成追问（F17）：
    候选的姓名与脱敏手机号由它透出给用户 —— 编排层不自己拼手机号、不自己数候选（铁律 2）。
    """
    filled, missing = dict(slots), []
    payee_facts: Mapping = {}
    if not filled.get("payee_id"):
        query = str(filled.get("payee") or "").strip()
        found = transfer.resolve_payee(query) if query else None
        candidates = (found.facts.get("candidates") or []) if found is not None and found.ok else []
        if len(candidates) == 1:
            filled["payee_id"] = candidates[0]["id"]
            filled["payee_name"] = candidates[0]["name"]
            filled["masked_phone"] = candidates[0]["phone"]
        else:
            missing.append("payee")
            payee_facts = found.facts if found is not None and found.ok else {}
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    clarify = payee_clarify.ask_for(payee_facts, missing) if payee_facts else ""
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _prepare_transfer(filled: dict) -> Prepared:
    """T7 预览：**只算不执行**，凭证由工具层签发（编排层不造串）。

    事实包补一个 `payee_phone`（脱敏手机号）—— 它来自工具层 `resolve_payee` 的候选，不是编排层编的；
    确认卡上要显示它，`verify_numbers` 必须能在事实包里找到（铁律 2）。

    ⚠️ **F16 未实现**：本函数**不传** `schedule`，故 `transfer_scheduled` 实际走的是即时转账语义 ——
    「明天转」会被当即时转账出卡并立刻扣款。F16 的验收第 1 条正是禁止这一点，待 F16 卡修复。
    """
    preview = transfer.preview_transfer(filled["payee_id"], filled["amount_cents"])
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return Prepared(ok=True, facts={**preview.facts, "payee_phone": masked},
                    credential=str(preview.data["preview_token"]))


def _card_transfer(intent: str, filled: dict, facts: Mapping, tier: str,
                   requires_otp: bool) -> confirm_card.CardInput:
    """转账确认卡：金额、档位、风险因子**全部来自 facts**（卡面行与 card-10 逐字一致）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return confirm_card.CardInput(
        title="转账确认卡", intent_cn=TRANSFER_CN.get(intent, intent),
        rows=[("收款人", f"{facts['payee_name']}（{masked}）"),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("预计到账", confirm_card.expected_arrival(tier))],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note(list(facts.get("factors") or [])))


def _plain_transfer(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本：确认卡出现 facts 之外的数字时的降级回执（只引用传入值）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return (f"请确认：向 {facts['payee_name']}（{masked}）转账 {facts['amount_yuan']} 元，"
            f"权限档 {tier}。")


def _execute_transfer(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """OTP 校验与幂等**都在工具层**（card-05 已 PASS）；本层不重写。"""
    return transfer.execute_transfer(confirmation.credential, otp)   # 工具层只认 token，不看 payee_id
