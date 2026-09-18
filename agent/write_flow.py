"""写路径（卡 10 实现 / 10b 从 `orchestrator.py` 与 `confirm_card.py` 拆出）：转账端到端。

铁律 3 的四步在这里走完，一步不少：

    SLOT_FILL（payee→payee_id、amount 元→整数分）
      → PRECHECK（`guard.permission` 纯代码定档，LLM 无从参与 —— 铁律 1）
      → CONFIRM_CARD（确认卡；L2 再要 OTP）或 PENDING_REVIEW（L3：延迟窗口 + 撤销入口）
      → EXECUTE（`tools.transfer.execute_transfer`：OTP 校验与幂等都在工具层，本层不重写）

**与 orchestrator 的边界**：本模块不 import `orchestrator`（否则回边/循环）。三段流程返回 `Step`
（状态轨迹 + 审计字段 + 回执），由 orchestrator 落成 `_finish`/`_clarify` 与审计 —— 状态机仍只有一个入口。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from agent import confirm_card, templates
from guard import permission
from tools import transfer

logger = logging.getLogger(__name__)

#: 金额文本形状：最多两位小数的十进制（禁科学计数/负号/多余位数）
_YUAN = re.compile(r"\d+(?:\.\d{1,2})?")

#: 卡 10 接通的写意图（转账端到端）；其余写意图仍需专门的卡
WRITE_INTENTS = ("transfer_single", "transfer_scheduled")

#: 确认卡上的中文意图名（只做措辞，不含任何业务数字）
INTENT_CN = {"transfer_single": "单笔转账", "transfer_scheduled": "定时转账"}


class Step(BaseModel):
    """写路径一步的结果（编排层契约，非冻结工具契约）。"""

    model_config = ConfigDict(extra="forbid")

    intent: str | None = None            # 在途确认时以在途意图为准
    states: list[str] = []               # 要进入的状态（按序）
    result: str = "rejected"             # 审计 result
    reply: str = ""
    ask: str | None = None
    tool: str | None = None
    tier: str | None = None
    executed: bool = False
    pending_id: str | None = None
    degraded: bool = False
    to_human: bool = False
    error_code: str | None = None
    missing: list[str] = []              # 非空 → 交回 orchestrator 走 CLARIFY（轮次上限由它管）


def error_code_of(result) -> str | None:
    """工具结果的错误码字符串（审计与回执都用它；ErrorCode 枚举与裸字符串都接受）。"""
    code = result.error_code
    return str(getattr(code, "value", code)) if code else None


def inflight(session_id: str) -> str | None:
    """在途确认的意图（有则说明这一轮是"确认/OTP/其它"的回复，不再重新分类）。"""
    confirmation = confirm_card.current(session_id)
    return confirmation.intent if confirmation is not None else None


# ---------------- 槽位整形（对齐工具层签名） ----------------

def yuan_to_cents(value: object) -> int | None:
    """元 → 整数分：**字符串拆分整数运算，禁 float**（analyst 裁决；避免 0.1 类精度问题）。

    认不出或非正数 → None（由调用方判为缺槽并追问）。
    """
    text = str(value).strip().replace(",", "").replace("元", "")
    if not _YUAN.fullmatch(text):
        return None
    whole, _, frac = text.partition(".")
    cents = int(whole) * 100 + int((frac + "00")[:2])
    return cents if cents > 0 else None                              # 非正数 → 视为缺槽（不猜 0 元）


def resolve_target(intent: str, slots: Mapping) -> tuple[dict, list[str]]:
    """SLOT_FILL 对齐工具层签名：`payee`（名字/手机号）→ `payee_id`；`amount`（元）→ 整数分。

    没找到或同名多个 → 记缺槽（**绝不擅自选一个**）；金额认不出 → 记缺槽。
    """
    del intent                                                                  # 两种转账同口径
    filled = dict(slots)
    missing: list[str] = []
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
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    return filled, missing


def build_card(intent: str, facts: Mapping, *, payee_name: str, masked_phone: str,
               requires_otp: bool) -> confirm_card.CardInput:
    """从工具层预览事实包拼确认卡：金额、档位、风险因子**全部来自 facts**。"""
    return confirm_card.CardInput(
        intent_cn=INTENT_CN.get(intent, intent), payee_name=payee_name,
        masked_phone=confirm_card.mask_phone(masked_phone), amount_yuan=str(facts["amount_yuan"]),
        tier=str(facts["tier"]), requires_otp=requires_otp,
        expected_arrival=confirm_card.expected_arrival(str(facts["tier"])),
        risk_note=confirm_card.risk_note(list(facts.get("factors") or [])))


def card_numbers_outside_facts(card_text: str, facts: Mapping) -> list[str]:
    """确认卡上的数字是否都来自 facts（空列表 = 通过）；铁律 2 的守门。"""
    return sorted(templates.verify_numbers(card_text, facts))


def compose_result_reply(text: str, facts: Mapping) -> tuple[str, bool]:
    """写操作回执：与 `templates.compose_reply` 同一策略 —— 润色 → 数字校验 → 降级。

    回执正文由工具层 `message` 提供（数字全来自执行事实包）。
    """
    hallucinated = False
    for _ in range(templates.POLISH_ATTEMPTS):
        candidate = templates.polish(text, facts)
        if candidate is None:                                        # 润色不可用 → 用工具层原文，不算降级
            return text, hallucinated
        if not templates.verify_numbers(candidate, facts):
            return candidate, False
        hallucinated = True
    return text, True


# ---------------- EXECUTE ----------------

def _execute(session_id: str, confirmation: "confirm_card.Confirmation", otp: str | None) -> Step:
    """EXECUTE：OTP 校验与幂等**都在工具层**（card-05 已 PASS），本层只数会话级错误次数。"""
    step = Step(states=["EXECUTE"], tool="execute_transfer", tier=confirmation.tier,
                intent=confirmation.intent)
    result = transfer.execute_transfer(confirmation.preview_token, otp)
    if not result.ok:
        code = error_code_of(result)
        step.result, step.error_code = "error", code
        if code == "FORBIDDEN" and confirmation.requires_otp:        # OTP 错（判定权在工具层）
            remaining = confirm_card.note_otp_error(session_id)
            if confirm_card.is_locked(session_id):                   # 错满 3 次 → 锁会话
                confirm_card.clear(session_id)
                step.result, step.reply = "rejected", confirm_card.otp_locked_text()
                return step
            step.reply = confirm_card.otp_wrong_text(remaining)
            step.ask = confirm_card.otp_prompt()
            return step
        step.reply = templates.tool_error(code, result.message)
        return step
    confirm_card.clear(session_id)                                    # 一次性确认：用完即清
    step.executed, step.result = True, "success"
    step.states.append("VERIFY_NUMBERS")
    step.reply, step.degraded = compose_result_reply(result.message, result.facts)
    if step.degraded:
        step.error_code = "HALLUCINATION_BLOCKED"
    return step


def resume(session_id: str, text: str) -> Step:
    """接续在途确认：等确认 / 等 OTP；回复非确认内容 → 回到 SLOT_FILL（本笔作废、不执行）。"""
    confirmation = confirm_card.current(session_id)
    assert confirmation is not None                                   # 调用方已判存在
    if confirm_card.is_locked(session_id):                             # 会话已锁：写操作一律拒绝
        confirm_card.clear(session_id)
        return Step(states=[], result="rejected", intent=confirmation.intent,
                    reply=confirm_card.otp_locked_text(), error_code="FORBIDDEN",
                    tier=confirmation.tier)
    if confirmation.stage == "otp":                                    # OTP 阶段：交工具层校验
        return _execute(session_id, confirmation, text.strip())
    head = Step(states=["CONFIRM_CARD"], intent=confirmation.intent, tier=confirmation.tier)
    if not confirm_card.is_confirmation(text):
        confirm_card.clear(session_id)
        head.states.append("SLOT_FILL")
        head.missing = ["payee", "amount"]
        return head
    if confirmation.requires_otp:                                      # L2：确认卡 + OTP（双因子）
        confirm_card.to_otp_stage(session_id)
        head.result, head.reply = "pending_confirm", confirm_card.otp_prompt()
        head.ask = head.reply
        return head
    return _execute(session_id, confirmation, None)                    # L1：会话内确认卡即可


# ---------------- PRECHECK：定档 → 待复核 / 确认卡 ----------------

def _to_pending(confirmation_ctx: dict, verdict: permission.TierVerdict) -> Step:
    """L3：登记待复核（60s 延迟窗口 + 撤销入口），**不执行**。"""
    pending_id = f"pend-{uuid.uuid4().hex[:8]}"
    confirm_card.remember_pending(confirm_card.Pending(
        pending_id=pending_id, session_id=confirmation_ctx["session_id"],
        preview_token=confirmation_ctx["preview_token"],
        amount_cents=confirmation_ctx["amount_cents"], created_at=confirm_card.now_iso()))
    return Step(states=["PENDING_REVIEW"], result="pending_confirm", intent=confirmation_ctx["intent"],
                tier=verdict.tier, pending_id=pending_id, to_human=verdict.to_human,
                reply=confirm_card.pending_text(pending_id, verdict.factors))


def _to_confirmation_card(confirmation_ctx: dict, verdict: permission.TierVerdict, slots: dict,
                          preview) -> Step:
    """L1/L2：出确认卡并登记待确认（数字全部来自预览事实包，卡上多一个数字就降级为纯事实回执）。"""
    card = build_card(confirmation_ctx["intent"], preview.facts,
                      payee_name=str(preview.facts["payee_name"]),
                      masked_phone=slots.get("masked_phone", ""), requires_otp=verdict.requires_otp)
    card_text, degraded = confirm_card.render(card), False
    card_facts = {**preview.facts, "payee_phone": card.masked_phone}   # 手机号来自 resolve_payee
    if (stray := card_numbers_outside_facts(card_text, card_facts)):   # 铁律 2 守门
        logger.error("确认卡出现 facts 之外的数字，降级为纯事实回执：%s", stray)
        card_text, degraded = confirm_card.plain_confirm_text(card.payee_name, card.masked_phone,
                                                              card.amount_yuan, card.tier), True
    confirm_card.start(confirm_card.Confirmation(
        session_id=confirmation_ctx["session_id"], intent=confirmation_ctx["intent"],
        preview_token=confirmation_ctx["preview_token"], payee_id=slots["payee_id"],
        payee_name=card.payee_name, masked_phone=card.masked_phone, amount_yuan=card.amount_yuan,
        amount_cents=slots["amount_cents"], tier=verdict.tier, requires_otp=verdict.requires_otp,
        card_text=card_text))
    return Step(states=["CONFIRM_CARD"], result="pending_confirm", intent=confirmation_ctx["intent"],
                tier=verdict.tier, reply=card_text, ask=card_text, degraded=degraded)


def start(intent: str, slots: Mapping, session_id: str) -> Step:
    """新起一笔写操作：SLOT_FILL → PRECHECK（定档）→ CONFIRM_CARD / PENDING_REVIEW。"""
    filled, missing = resolve_target(intent, slots)
    if missing:                                                        # 缺收款人/金额 → 追问，不猜
        return Step(states=["SLOT_FILL"], intent=intent, missing=missing)
    if confirm_card.is_locked(session_id):
        return Step(states=["PRECHECK"], intent=intent, result="rejected",
                    reply=confirm_card.otp_locked_text(), error_code="FORBIDDEN")
    preview = transfer.preview_transfer(filled["payee_id"], filled["amount_cents"])   # 只算不执行
    if not preview.ok:
        code = error_code_of(preview)
        return Step(states=["PRECHECK"], intent=intent, result="error", tool="preview_transfer",
                    error_code=code, reply=templates.tool_error(code, preview.message))
    verdict = permission.assess_write(intent, tier=preview.facts["tier"],            # 纯代码档位
                                      factors=preview.facts["factors"])
    context = {"session_id": session_id, "intent": intent, "amount_cents": filled["amount_cents"],
               "preview_token": str(preview.data["preview_token"])}
    branch = (_to_pending(context, verdict) if verdict.delayed                       # L3：延迟 + 撤销
              else _to_confirmation_card(context, verdict, filled, preview))         # L1/L2：确认卡
    branch.states = ["SLOT_FILL", "PRECHECK", *branch.states]
    branch.tier = verdict.tier
    return branch
