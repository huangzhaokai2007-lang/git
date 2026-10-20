"""转账族写描述符（T6/T7）：`preview_token` 族，**OTP 校验与幂等都在工具层**。

F15/F20 拆卡时从 `agent/write_intents.py` 按意图族拆出；核心契约类型与共享工具见
`agent/_write_common.py`，注册表见 `agent/write_intents.py`。

**本族覆盖两个意图**：`transfer_single`（即时）与 `transfer_scheduled`（**F16 定时转账**）。

F16 的关键差异只有两处：① PRECHECK 时把 `schedule` 一起传给 T7（此前不传，导致「明天转」被当即时转账）；
② EXECUTE 走 **T21 `schedule_transfer`**（落 `pending` 预约、**不扣款**），而不是 T8 立即扣款。
到点执行由 `agent/scheduled_runner.py` 的惰性结算调 T22 完成。
方案见 `docs/superpowers/specs/2026-10-15-f16-scheduled-transfer-design.md`（Q1–Q10 人类已批）。
"""

from __future__ import annotations

import datetime
from typing import Mapping

from agent import confirm_card, payee_clarify
from agent._write_common import Prepared, ShapeResult, _code_of, yuan_to_cents
from tools import transfer

#: 转账类的中文名与卡标题（只做措辞，不含任何业务数字）
TRANSFER_CN = {"transfer_single": "单笔转账", "transfer_scheduled": "定时转账"}

#: 预约时间认不出时的追问（纯措辞，不含业务数字）。**相对日（明天/下周一）暂不支持**（Q10）。
SCHEDULE_CLARIFY = ("请说明预约时间，格式如 2026-10-21 09:00（也接受 2026-10-21）；"
                    "「明天」「下周一」这类相对说法暂不支持。")


def _iso_schedule(value: object) -> str | None:
    """预约时间：接受 ISO8601 日期或日期时间；**认不出 → `None`**（当缺槽追问，绝不猜）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text).isoformat(timespec="minutes")
    except ValueError:
        return None


def _shape_scheduled(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：收款人 / 金额沿用转账口径，另加 `schedule`（ISO8601）。

    收款人解析与金额换算**直接复用 `_shape_transfer`**（同一份实现，不复制）—— 同名追问、
    脱敏手机号透出等 F17 行为因此自动一致。预约时间认不出 → 追问并给出格式。
    """
    shaped = _shape_transfer(slots)
    filled, missing = dict(shaped.filled), list(shaped.missing)
    when = _iso_schedule(filled.get("schedule"))
    if when is None:
        missing.append("schedule")
    else:
        filled["schedule"] = when
    clarify = shaped.clarify or (SCHEDULE_CLARIFY if "schedule" in missing else "")
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


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


# ---------------- 定时转账（F16）：预约时间进卡面、执行只落预约 ----------------

def _prepare_scheduled(filled: dict) -> Prepared:
    """T7 预览（**只算不执行**）：把 `schedule` 一起传进去 —— 这正是 F16 修掉的那个洞。

    预约**尚未落库**（落库在 EXECUTE 步调 T21）。事实包里额外放 `schedule` 字符串是必须的：
    卡面要印预约时间，而 `card_numbers_outside_facts` 会把「2026」「09」这类数字当成 facts
    之外的数字 → 整张卡降级（铁律 2 守门），故预约时间必须可溯源。
    """
    preview = transfer.preview_transfer(filled["payee_id"], filled["amount_cents"],
                                        schedule=str(filled["schedule"]))
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return Prepared(ok=True,
                    facts={**preview.facts, "payee_phone": masked,
                           "schedule": str(filled["schedule"])},
                    credential=str(preview.data["preview_token"]))


def _card_scheduled(intent: str, filled: dict, facts: Mapping, tier: str,
                    requires_otp: bool) -> confirm_card.CardInput:
    """定时转账确认卡：**多一行「预约时间」、且不印「预计到账」**。

    不印「预计到账」是刻意的 —— 那是即时转账的措辞，用在这里会被理解成「马上到账」，
    而 F16 验收第 2 条正是不许把预约当即时。
    """
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return confirm_card.CardInput(
        title="定时转账确认卡", intent_cn="定时转账",
        rows=[("收款人", f"{facts['payee_name']}（{masked}）"),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("预约时间", str(facts["schedule"]).replace("T", " ")),
              ("执行说明", "到预约时间后才扣款，此前不会动钱")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note(list(facts.get("factors") or [])))


def _plain_scheduled(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（定时转账的降级回执）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return (f"请确认：预约 {facts.get('schedule')} 向 {facts['payee_name']}（{masked}）转账 "
            f"{facts['amount_yuan']} 元，权限档 {tier}，到点之前不会扣款。")


def _book_scheduled(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T21** 落一条 `pending` 预约（**不扣款**）—— 验收第 2 条的落点。

    延迟 import：本模块属编排层，`_transfer_schedule` 属工具层且反过来依赖 `tools.transfer`，
    在函数内导入可彻底避开导入顺序问题（同 `tools/transfer.py` 调 `schedule_args_error` 的做法）。
    """
    from tools import _transfer_schedule
    return _transfer_schedule.schedule_transfer(confirmation.credential, otp)
