"""写路径（卡 10 实现 / 10b 拆出 / card-24 泛化）：**所有写意图共用的四步骨架**。

铁律 3 的四步在这里走完，一步不少：

    SLOT_FILL（每意图自己的槽位整形）
      → PRECHECK（`guard.permission` 纯代码定档，LLM 无从参与 —— 铁律 1）
      → CONFIRM_CARD（确认卡；L2 再要 OTP）或 PENDING_REVIEW（L3：延迟窗口 + 撤销入口）
      → EXECUTE（工具层执行；OTP 校验位置由描述符声明）

**card-24 的泛化**：本模块只留「四步骨架 + 与意图无关的通用件」，
每个写意图的形状（槽位整形 / 凭证种类 / 卡面 / 执行调用）落在 `agent/write_intents.py` 的描述符里。
**新增写意图不需要改本文件** —— 加一条描述符即可。

**两种凭证**（描述符的 `credential_kind` / `otp_in_tool` 声明）：

- `preview_token`（T7 转账）：凭证由工具层 `preview_*` 签发，**OTP 校验与幂等在工具层**；
- `confirm_ref`（T11/T12/T15）：凭证由工具层私有约定 `issue_confirm_ref` 签发，
  这些工具签名**没有 otp 参数** → **OTP 闸门在本层**（`write_intents.otp_ok`），
  依据 `tools/cross_scene.py`「确认与 OTP 落在编排层（同 T11/T15 的分工）」。

**与 orchestrator 的边界**：本模块不 import `orchestrator`（否则回边/循环）。三段流程返回 `Step`
（状态轨迹 + 审计字段 + 回执），由 orchestrator 落成 `_finish`/`_clarify` 与审计 —— 状态机仍只有一个入口。
"""

from __future__ import annotations

import logging
import uuid
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from agent import confirm_card, templates, write_intents
from guard import permission

logger = logging.getLogger(__name__)

#: 写意图清单的唯一来源在 `agent/write_intents.py`（card-24 泛化拆出）。
#: 这里 import 即 **re-export** —— `orchestrator.WRITE_INTENTS` 与历史调用点指向同一份元组。
WRITE_INTENTS = write_intents.WRITE_INTENTS

#: `yuan_to_cents` 的实现已移到 `agent/write_intents.py`；这里 re-export 保住历史调用点
#: （`tests/test_confirm_flow.py` 直接调 `write_flow.yuan_to_cents`）。
yuan_to_cents = write_intents.yuan_to_cents


class Step(BaseModel):
    """写路径一步的结果（编排层契约，非冻结工具契约）。"""

    model_config = ConfigDict(extra="forbid")

    intent: str | None = None            # 在途确认时以在途意图为准
    states: list[str] = []               # 要进入的状态（按序）
    result: str = "rejected"             # 审计 result
    reply: str = ""
    ask: str | None = None
    tool: str | None = None
    tools: list[str] = []                # 本步调用过的工具（审计 tool_calls 用；tool 仅审计归属）
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

def _otp_rejected(session_id: str, step: Step, spec: write_intents.WriteSpec) -> Step:
    """OTP 被拒的统一处理：数会话级错误次数，错满 3 次锁会话（卡 10 第 4 条）。"""
    remaining = confirm_card.note_otp_error(session_id)
    if confirm_card.is_locked(session_id):
        confirm_card.clear(session_id)
        step.result, step.reply = "rejected", confirm_card.otp_locked_text(spec.what)
        return step
    step.reply = confirm_card.otp_wrong_text(remaining)
    step.ask = confirm_card.otp_prompt(spec.what)
    return step


def _execute(session_id: str, confirmation: "confirm_card.Confirmation", otp: str | None) -> Step:
    """EXECUTE：调描述符声明的执行函数；OTP 校验位置由描述符决定（工具层 / 本层）。"""
    spec = write_intents.spec_of(confirmation.intent)
    step = Step(states=["EXECUTE"], tool=spec.execute_tool, tier=confirmation.tier,
                intent=confirmation.intent)
    if not spec.otp_in_tool and not write_intents.otp_ok(otp):       # confirm_ref 族：本层闸门
        step.result = "error"
        return _otp_rejected(session_id, step, spec)
    result = spec.execute(confirmation.target_id, confirmation.credential, otp)
    if not result.ok:
        code = error_code_of(result)
        step.result, step.error_code = "error", code
        if code == "FORBIDDEN" and confirmation.requires_otp:        # OTP 错（判定权在工具层）
            return _otp_rejected(session_id, step, spec)
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
    spec = write_intents.spec_of(confirmation.intent)
    if confirm_card.is_locked(session_id):                             # 会话已锁：写操作一律拒绝
        confirm_card.clear(session_id)
        return Step(states=[], result="rejected", intent=confirmation.intent,
                    reply=confirm_card.otp_locked_text(spec.what), error_code="FORBIDDEN",
                    tier=confirmation.tier)
    if confirmation.stage == "otp":                                    # OTP 阶段
        return _execute(session_id, confirmation, text.strip())
    head = Step(states=["CONFIRM_CARD"], intent=confirmation.intent, tier=confirmation.tier)
    if not confirm_card.is_confirmation(text):
        confirm_card.clear(session_id)
        head.states.append("SLOT_FILL")
        head.missing = list(spec.missing)                              # 缺槽名由描述符声明
        return head
    if confirmation.requires_otp:                                      # L2：确认卡 + OTP（双因子）
        confirm_card.to_otp_stage(session_id)
        head.result, head.reply = "pending_confirm", confirm_card.otp_prompt(spec.what)
        head.ask = head.reply
        return head
    return _execute(session_id, confirmation, None)                    # L1：会话内确认卡即可


# ---------------- PRECHECK：定档 → 待复核 / 确认卡 ----------------

def _to_pending(context: dict, verdict: permission.TierVerdict) -> Step:
    """L3：登记待复核（60s 延迟窗口 + 撤销入口），**不执行**。"""
    spec, prepared = context["spec"], context["prepared"]
    pending_id = f"pend-{uuid.uuid4().hex[:8]}"
    confirm_card.remember_pending(confirm_card.Pending(
        pending_id=pending_id, session_id=context["session_id"],
        credential_kind=spec.credential_kind, credential=prepared.credential,
        target_id=context["target_id"], amount_cents=context["amount_cents"],
        created_at=confirm_card.now_iso()))
    return Step(states=["PENDING_REVIEW"], result="pending_confirm", intent=context["intent"],
                tier=verdict.tier, pending_id=pending_id, to_human=verdict.to_human,
                reply=confirm_card.pending_text(pending_id, verdict.factors, spec.what))


def _to_confirmation_card(context: dict, verdict: permission.TierVerdict, filled: dict,
                          prepared: write_intents.Prepared) -> Step:
    """L1/L2：出确认卡并登记待确认（数字全部来自事实包，卡上多一个数字就降级为纯事实回执）。"""
    spec = context["spec"]
    card = spec.card(context["intent"], filled, prepared.facts, verdict.tier, verdict.requires_otp)
    card_text, degraded = confirm_card.render(card), False
    # 卡上的「权限档」是 guard 纯代码判的，一并作为可溯源事实（转账的 tier 本来就来自工具层预览，
    # 这里合并是同值覆盖）；其余数字必须来自工具层事实包，否则降级（铁律 2）。
    card_facts = {**prepared.facts, "tier": verdict.tier}
    if (stray := card_numbers_outside_facts(card_text, card_facts)):       # 铁律 2 守门
        logger.error("确认卡出现 facts 之外的数字，降级为纯事实回执：%s", stray)
        card_text, degraded = spec.plain(filled, card_facts, verdict.tier), True
    confirm_card.start(confirm_card.Confirmation(
        session_id=context["session_id"], intent=context["intent"],
        credential_kind=spec.credential_kind, credential=prepared.credential,
        target_id=context["target_id"],
        target_name=str(prepared.facts.get("merchant") or prepared.facts.get("payee_name") or ""),
        masked_phone=confirm_card.mask_phone(str(filled.get("masked_phone") or "")),
        amount_yuan=str(prepared.facts.get("amount_yuan") or ""),
        amount_cents=context["amount_cents"], tier=verdict.tier,
        requires_otp=verdict.requires_otp, card_text=card_text))
    return Step(states=["CONFIRM_CARD"], result="pending_confirm", intent=context["intent"],
                tier=verdict.tier, reply=card_text, ask=card_text, degraded=degraded)


def start(intent: str, slots: Mapping, session_id: str) -> Step:
    """新起一笔写操作：SLOT_FILL → PRECHECK（定档）→ CONFIRM_CARD / PENDING_REVIEW。"""
    spec = write_intents.spec_of(intent)
    if spec is None:                                                   # 调用方已按 WRITE_INTENTS 过滤
        return Step(states=["SLOT_FILL"], intent=intent)
    shaped = spec.shape(slots)
    if shaped.error_code:                                              # 零命中 → 报错（不猜）
        return Step(states=["SLOT_FILL"], intent=intent, result="error",
                    error_code=shaped.error_code,
                    reply=templates.tool_error(shaped.error_code, shaped.message))
    if shaped.missing:                                                 # 缺槽 → 追问，不猜
        return Step(states=["SLOT_FILL"], intent=intent, missing=shaped.missing)
    filled = shaped.filled
    if confirm_card.is_locked(session_id):
        return Step(states=["PRECHECK"], intent=intent, result="rejected",
                    reply=confirm_card.otp_locked_text(spec.what), error_code="FORBIDDEN")
    prepared = spec.prepare(filled)                                    # 工具层事实包 + 签发凭证
    if not prepared.ok:
        return Step(states=["PRECHECK"], intent=intent, result="error", tool=spec.tools[0],
                    error_code=prepared.error_code,
                    reply=templates.tool_error(prepared.error_code, prepared.message))
    verdict = permission.assess_write(intent, tier=prepared.facts.get("tier"),       # 纯代码档位
                                      factors=prepared.facts.get("factors"))
    context = {"session_id": session_id, "intent": intent, "spec": spec, "prepared": prepared,
               "amount_cents": filled.get("amount_cents") or 0,
               "target_id": str(filled.get(spec.target_key) or "")}
    branch = (_to_pending(context, verdict) if verdict.delayed                       # L3：延迟 + 撤销
              else _to_confirmation_card(context, verdict, filled, prepared))        # L1/L2：确认卡
    branch.states = ["SLOT_FILL", "PRECHECK", *branch.states]
    branch.tool = branch.tool or spec.tools[0]                         # 铁律 3 第一步：只算不执行
    branch.tools = list(spec.tools)
    branch.tier = verdict.tier
    return branch
