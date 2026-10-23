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
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict

from agent import card_draft, confirm_card, payee_picker, templates, write_intents
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

def _otp_rejected(session_id: str, step: Step, spec: write_intents.WriteSpec,
                 masked_phone: str = "") -> Step:
    """OTP 被拒的统一处理：数会话级错误次数，错满 3 次锁会话（卡 10 第 4 条）。"""
    remaining = confirm_card.note_otp_error(session_id)
    if confirm_card.is_locked(session_id):
        confirm_card.clear(session_id)
        step.result, step.reply = "rejected", confirm_card.otp_locked_text(spec.what)
        return step
    step.reply = confirm_card.otp_wrong_text(remaining)
    step.ask = confirm_card.otp_prompt(spec.what, masked_phone)
    return step


def _execute(session_id: str, confirmation: "confirm_card.Confirmation", otp: str | None) -> Step:
    """EXECUTE：调描述符声明的执行函数；OTP 校验位置由描述符决定（工具层 / 本层）。"""
    spec = write_intents.spec_of(confirmation.intent)
    step = Step(states=["EXECUTE"], tool=spec.execute_tool, tier=confirmation.tier,
                intent=confirmation.intent)
    # `confirm_ref` 族且**本档需要 OTP** 时才在编排层设闸门；L1 意图（如 AA 收款，T9 无 OTP）不走闸门。
    if not spec.otp_in_tool and confirmation.requires_otp and not write_intents.otp_ok(otp):
        step.result = "error"
        return _otp_rejected(session_id, step, spec, str(confirmation.masked_phone or ""))
    result = spec.execute(confirmation, otp)                         # 入参是整张确认卡（含槽位快照）
    if not result.ok:
        code = error_code_of(result)
        step.result, step.error_code = "error", code
        if code == "FORBIDDEN" and confirmation.requires_otp:        # OTP 错（判定权在工具层）
            return _otp_rejected(session_id, step, spec, str(confirmation.masked_phone or ""))
        step.reply = (f"状态：{confirm_card.state_line(confirm_card.STATE_FAILED)}\n"
                      f"{templates.tool_error(code, result.message)}")
        return step
    confirm_card.clear(session_id)                                    # 一次性确认：用完即清
    step.executed, step.result = True, "success"
    step.states.append("VERIFY_NUMBERS")
    step.reply, step.degraded = compose_result_reply(result.message, result.facts)
    #: 2026-10-24（人类规格「卡片需包含状态」）：成功回执挂状态行；在润色**之后**加，避免被改写。
    step.reply = f"状态：{confirm_card.state_line(confirm_card.STATE_DONE)}\n{step.reply}"
    if step.degraded:
        step.error_code = "HALLUCINATION_BLOCKED"
    return step


def resume(session_id: str, text: str,
           *, classify: "Callable[[str], Any] | None" = None) -> Step:
    """接续在途确认：等确认 / 等 OTP；回复非确认内容 → 回到 SLOT_FILL（本笔作废、不执行）。

    S5（可编辑卡片）：给了 `classify` 回调时，非确认回复先当成**改卡**试一次 ——
    分类结果与在途意图一致 → 走 `edit()`（旧卡字段 + 新值 → 重走 shape/风控 → 出新卡）；
    意图不一致 → 维持旧行为（清卡、回 SLOT_FILL 追问）。
    """
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
        #: S5 改卡：先走**确定性句式**（「金额改成 50 元」不赌 LLM 脾气），
        #: 刻板句式命中 → 直接改；否则再试 LLM 分类兜底（自由措辞的改卡）。
        updates = card_draft.parse_edit(text, confirmation.intent)
        if updates is not None:
            return edit(session_id, updates)
        if classify is not None:                                       # S5：改卡（同意图才算）
            try:
                verdict = classify(text)
            except Exception:                                          # 分类失败 → 走旧行为，不崩
                verdict = None
            if verdict is not None and verdict.intent == confirmation.intent:
                return edit(session_id, verdict.slots)
        confirm_card.clear(session_id)
        head.states.append("SLOT_FILL")
        head.missing = list(spec.missing)                              # 缺槽名由描述符声明
        return head
    if confirmation.requires_otp:                                      # L2：确认卡 + OTP（双因子）
        confirm_card.to_otp_stage(session_id)
        head.result = "pending_confirm"
        head.reply = confirm_card.otp_prompt(spec.what, str(confirmation.masked_phone or ""))
        head.ask = head.reply
        return head
    return _execute(session_id, confirmation, None)                    # L1：会话内确认卡即可


# ---------------- S5：可编辑卡片 ----------------

def edit(session_id: str, updates: Mapping) -> Step:
    """**改卡 = 重走校验**（设计 §4）：把在途确认卡的关键字段改掉，重新 `shape → 风控 → 出新卡`。

    不是直接改卡 —— 卡上数字必须来自 facts；这里只做「旧卡字段 + 新值 → 重跑 start()」，
    由工具层重新出事实包、重新定档。只允许在 confirm 阶段改（OTP 阶段先完成或撤销）。
    """
    confirmation = confirm_card.current(session_id)
    if confirmation is None:
        return Step(states=[], result="rejected", reply="当前没有待确认的操作",
                    error_code="NOT_FOUND")
    if confirmation.stage != "confirm":
        return Step(states=[], result="rejected",
                    reply="验证码阶段不能修改卡片，请先完成或撤销", error_code="INVALID_STATE")
    spec = write_intents.spec_of(confirmation.intent)
    raw = {k: confirmation.filled.get(k) for k in spec.missing
           if confirmation.filled.get(k) is not None}
    if confirmation.intent == "aa_collect":                           # AA：用**姓名**重解析（id 不认名字匹配）
        names = list(confirmation.filled.get("aa_names") or [])
        if names:
            raw["payee_ids"] = names
        if "aa_target" in confirmation.filled:
            raw["aa_target"] = confirmation.filled["aa_target"]
    #: 改哪个槽位，就把它的**派生键**摘掉，逼 shape 重新算（`amount_cents`/`payee_id` 等）
    derived = {"amount": ("amount_cents",), "payee": ("payee_id", "masked_phone"),
               "contact": ("payee_id",), "budget": ("amount_cents",),
               "amounts": ("per_person_cents", "amount_cents", "amount"),
               "schedule": (), "date": ()}
    for key, value in updates.items():
        if value in (None, ""):
            raw.pop(key, None)
            continue
        raw[key] = str(value)
        for stale in derived.get(key, ()):
            raw.pop(stale, None)
    return start(confirmation.intent, raw, session_id)


# ---------------- PRECHECK：定档 → 待复核 / 确认卡 ----------------

def _to_pending(context: dict, verdict: permission.TierVerdict) -> Step:
    """L3：登记待复核（60s 延迟窗口 + 撤销入口），**不执行**。"""
    spec, prepared = context["spec"], context["prepared"]
    existing = confirm_card.find_pending(context["session_id"], str(context["target_id"]),
                                         int(context["amount_cents"]))
    if existing is not None:                           # 已有同对象的待复核 → 复用，不重复登记
        confirm_card.clear(context["session_id"])
        return Step(states=["PENDING_REVIEW"], result="pending_confirm",
                    intent=context["intent"], tier=verdict.tier,
                    pending_id=existing.pending_id, to_human=verdict.to_human,
                    reply=(f"这笔{spec.what}已在待人工复核（同一笔不会重复登记）。"
                           + confirm_card.pending_text(existing.pending_id,
                                                       verdict.factors, spec.what)))
    pending_id = f"pend-{uuid.uuid4().hex[:8]}"
    confirm_card.remember_pending(confirm_card.Pending(
        pending_id=pending_id, session_id=context["session_id"],
        credential_kind=spec.credential_kind, credential=prepared.credential,
        target_id=context["target_id"], amount_cents=context["amount_cents"],
        created_at=confirm_card.now_iso()))
    confirm_card.clear(context["session_id"])        # 在途一次性：避免再回「确认」重复建一笔复核
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
        target_name=str(prepared.facts.get("target_name") or prepared.facts.get("merchant")
                        or prepared.facts.get("payee_name") or ""),
        masked_phone=confirm_card.mask_phone(str(filled.get("masked_phone") or "")),
        amount_yuan=str(prepared.facts.get("amount_yuan") or ""),
        amount_cents=context["amount_cents"], tier=verdict.tier,
        requires_otp=verdict.requires_otp, card_text=card_text, filled=dict(filled)))
    return Step(states=["CONFIRM_CARD"], result="pending_confirm", intent=context["intent"],
                tier=verdict.tier, reply=card_text, ask=card_text, degraded=degraded)


def start(intent: str, slots: Mapping, session_id: str) -> Step:
    """新起一笔写操作：SLOT_FILL → PRECHECK（定档）→ CONFIRM_CARD / PENDING_REVIEW。"""
    spec = write_intents.spec_of(intent)
    if spec is None:                                                   # 调用方已按 WRITE_INTENTS 过滤
        return Step(states=["SLOT_FILL"], intent=intent)
    shaped = spec.shape(slots)
    payee_picker.clear(session_id)                                     # 任何一次新解析都先清旧待选
    if shaped.error_code:                                              # 零命中 → 报错（不猜）
        return Step(states=["SLOT_FILL"], intent=intent, result="error",
                    error_code=shaped.error_code,
                    reply=templates.tool_error(shaped.error_code, shaped.message))
    if shaped.missing:                                                 # 缺槽 → 追问，不猜
        if shaped.candidates:                                          # 同名多命中 → 界面出**解析卡**
            slot = ("payee_ids" if isinstance(slots.get("payee_ids"), (list, tuple))
                    else "payee")
            payee_picker.capture(session_id, intent, dict(slots), shaped.candidates,
                                 slot=slot)
        return Step(states=["SLOT_FILL"], intent=intent, missing=shaped.missing,
                    ask=shaped.clarify or None)      # F17：描述符给了更具体的追问就用它
    filled = shaped.filled
    if confirm_card.is_locked(session_id):
        return Step(states=["PRECHECK"], intent=intent, result="rejected",
                    reply=confirm_card.otp_locked_text(spec.what), error_code="FORBIDDEN")
    prepared = spec.prepare(filled)                                    # 工具层事实包 + 签发凭证
    if not prepared.ok:
        return Step(states=["PRECHECK"], intent=intent, result="error",
                    tool=spec.tools[0] if spec.tools else None,
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
    #: AA 收款（T9 无 `preview_*`）在 PRECHECK 阶段**不调任何工具** → `spec.tools` 为空，
    #: 工具名必须留空，否则 `/api/chat` 的 `tool_calls` 会谎报“已调用”，与 F15 验收点
    #: 「确认前不发起请求」直接冲突。
    branch.tools = list(spec.tools)
    branch.tool = branch.tool or (spec.tools[0] if spec.tools else None)
    branch.tier = verdict.tier
    return branch
