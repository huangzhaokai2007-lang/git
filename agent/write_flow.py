'写路径（卡 10 实现 / 10b 拆出 / card-24 泛化）：**所有写意图共用的四步骨架**。'

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict

from agent import card_draft, confirm_card, payee_picker, pending_slots, templates, write_intents
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
        step.result = "rejected"
        step.reply = (f"状态：{confirm_card.state_line(confirm_card.STATE_EXPIRED)}\n"
                      f"{confirm_card.otp_locked_text(spec.what)}")
        return step
    step.reply = confirm_card.otp_wrong_text(remaining)
    step.ask = confirm_card.otp_prompt(spec.what, masked_phone)
    return step


def _execute(session_id: str, confirmation: "confirm_card.Confirmation", otp: str | None) -> Step:
    """EXECUTE：调描述符声明的执行函数；OTP 校验位置由描述符决定（工具层 / 本层）。"""
    spec = write_intents.spec_of(confirmation.intent)
    step = Step(states=["EXECUTE"], tool=spec.execute_tool, tier=confirmation.tier,
                intent=confirmation.intent)
    # `confirm_ref` 族且**本档需要 OTP** 时才在编排层设闸门；L1 意图（如 群收款，T9 无 OTP）不走闸门。
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
    cancelled = confirmation.intent.endswith("_cancel") or "cancel" in confirmation.intent
    state = confirm_card.STATE_CANCELLED if cancelled else confirm_card.STATE_DONE
    step.reply = f"状态：{confirm_card.state_line(state)}\n{step.reply}"
    if step.degraded:
        step.error_code = "HALLUCINATION_BLOCKED"
    return step


def resume(session_id: str, text: str,
           *, classify: "Callable[[str], Any] | None" = None) -> Step:
    '接续在途确认：等确认 / 等 OTP；回复非确认内容 → 回到 SLOT_FILL（本笔作废、不执行）。'
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
    '**改卡 = 重走校验**（设计 §4）：把在途确认卡的关键字段改掉，重新 `shape → 风控 → 出新卡`。'
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
    #: 2026-10-31（用户实测「该凭证已使用，请重新发起转账」）：重检必须**重新签发**凭据，
    #: 把卡上残留的凭据类键一律摘掉，避免把旧 token 带进下一次预览。
    for stale_key in [k for k in list(raw) if "token" in str(k) or "credential" in str(k)]:
        raw.pop(stale_key, None)
    if confirmation.intent == "aa_collect":                           # AA：用**姓名**重解析（id 不认名字匹配）
        names = list(confirmation.filled.get("aa_names") or [])
        if names:
            raw["payee_ids"] = names
        if "aa_target" in confirmation.filled:
            raw["aa_target"] = confirmation.filled["aa_target"]
    #: 2026-10-30（用户实测「点修改后收款人显示成电话号码」）：卡面显示姓名，
    #: 但**重检**不能拿姓名再解析一遍（同名会再弹解析卡）。用户没改收款人时，
    #: 直接用卡上已经解析好的脱敏手机号（解析关卡里唯一可辨）。
    if "payee" not in updates:
        masked = str(((confirmation.filled or {}).get("masked_phone") or "")).strip()
        if masked and "payee" in raw:
            raw["payee"] = masked
    #: 改哪个槽位，就把它的**派生键**摘掉，逼 shape 重新算（`amount_cents`/`payee_id` 等）
    derived = {"amount": ("amount_cents",), "payee": ("payee_id", "masked_phone"),
               "contact": ("payee_id",), "budget": ("amount_cents",),
               "amounts": ("per_person_cents", "amount_cents", "amount"),
               "schedule": (), "date": ()}
    for key, value in updates.items():
        if value in (None, ""):
            raw.pop(key, None)
            continue
        if key == "payee_card":                  #: 2026-10-31：换收款卡 → 工具层参数名是 payee_card_id
            raw["payee_card_id"] = str(value)
            continue
        raw[key] = str(value)
        for stale in derived.get(key, ()):
            raw.pop(stale, None)
    if confirmation.intent in ('transfer_single', 'transfer_scheduled'):
        from tools._scene_drafts import revoke
        if not revoke(confirmation.credential_kind, confirmation.credential):
            return Step(states=[], result='rejected', intent=confirmation.intent,
                        reply='原草稿已失效，请重新发起办理。', error_code='INVALID_STATE')
    result = start(confirmation.intent, raw, session_id)
    from agent import scene_context
    scene_context.remember_write(session_id, confirmation.intent, raw, status=result.result, reply=result.reply)
    return result


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
        requires_otp=verdict.requires_otp, card_text=card_text, filled=dict(filled),
        trace_id=str(context.get("trace_id") or ""),
        evidence=dict(prepared.facts or {})))     # 依据面板：限额/额度/因子的快照
    return Step(states=["CONFIRM_CARD"], result="pending_confirm", intent=context["intent"],
                tier=verdict.tier, reply=card_text, ask=card_text, degraded=degraded)


#: 缺槽时给用户看的字段名（**只影响措辞**，不含任何业务数字）
SLOT_LABELS = {
    "payee": "收款人", "payee_ids": "收款人", "amount": "总金额", "date": "日期",
    "schedule": "预约时间", "budget": "预算", "contact": "联系人",
    "platform": "平台", "address": "收货地址", "order_no": "平台订单号",
    "tags": "标签", "note": "备注", "card_no": "银行卡号",
    "quota": "专属额度", "expire": "有效期", "reason": "原因", "weekday": "星期几",
}


#: 少数意图里同一个槽位说法不同：赎回谈「赎回金额」，申购谈「申购金额」，
#: 而全局 `SLOT_LABELS["amount"]` 是「总金额」（AA 群收款那边才该叫总金额）。
_INTENT_SLOT_LABELS = {
    "wealth_redeem": {"amount": "赎回金额", "product": "产品"},
    "wealth_buy": {"amount": "申购金额", "product": "产品"},
}


def slot_label(slot: str, intent: str = "") -> str:
    """槽位的中文名：**先看意图覆盖，再落全局**。"""
    override = _INTENT_SLOT_LABELS.get(intent) or {}
    return override.get(slot) or SLOT_LABELS.get(slot, slot)


def missing_hint(missing: list[str], intent: str = "") -> str:
    """缺槽追问（**兜底**：描述符没给 clarify 时用）。

    2026-10-24 修：以前这里可能返回空串 —— 界面按钮/解析卡路径只读 Step.reply，
    于是「选完同名收款人后还缺金额」时界面**什么都不显示**（用户以为卡住了）。
    措辞复用 	emplates.clarify_missing（全仓唯一追问口径，不另造一套）。
    """
    if not missing:
        return ""
    labels = [slot_label(slot, intent) for slot in missing]
    return templates.clarify_missing(labels)


#: 网页端「群收款先出编辑卡」的开关（agent 层用例默认关闭 = 旧的"直接出确认卡"契约）
_AA_EDIT_FIRST = False


def set_aa_edit_first(enabled: bool) -> None:
    """界面每次渲染时设置：网页里群收款要先看到可编辑的发起卡。"""
    global _AA_EDIT_FIRST
    _AA_EDIT_FIRST = bool(enabled)


def aa_edit_first_enabled() -> bool:
    return _AA_EDIT_FIRST


def start(intent: str, slots: Mapping, session_id: str, *, trace_id: str = "",
          from_card: bool = False) -> Step:
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
        hint = missing_hint(shaped.missing, intent)
        ask = shaped.clarify or hint                   # 描述符优先，否则兜底
        #: 描述符的追问如果**没提到**缺的那几项，就补一句 —— 用户一次知道要补什么，
        #: 不会出现「只问了标签、其实还差收款人」这种半句追问（实测）。
        labels = [slot_label(slot, intent) for slot in shaped.missing]
        absent = [label for label in dict.fromkeys(labels) if label not in shaped.clarify]
        if shaped.clarify and absent:                  # 只补**没说到的**那几项，不重复
            ask = f"{shaped.clarify}\n请补充：{'、'.join(absent)}。"
        #: 2026-10-27：登记「还差什么」，让用户下一句短答能**续接补槽**（不再被当新意图）。
        #: 2026-10-30：登记**解析之后**的槽位（`filled` 里有 payee_id / aa_names），
        #: 否则下一轮卡面拿到的是原始姓名，同名就没法回填（用户实测"解析完人没进参与人"）。
        pending_slots.remember(session_id, intent, {**dict(slots), **dict(shaped.filled)},
                               list(shaped.missing))
        return Step(states=["SLOT_FILL"], intent=intent, missing=shaped.missing,
                    ask=ask, reply=ask)              # reply 也带上：按钮路径只读 reply
    #: 2026-10-31（用户要求）：群收款在**聊天里**说全了也先铺发起卡（可改人、可改钱），
    #: 只有点卡上的「发起群收款」（`from_card=True`）才继续预检、出确认卡。
    if intent == "aa_collect" and not from_card and aa_edit_first_enabled():
        pending_slots.remember_prefill(session_id, intent,
                                       {**dict(slots), **dict(shaped.filled)})
        return Step(states=["SLOT_FILL"], intent=intent,
                    reply="已按你说的铺好群收款卡：核对参与人与金额，点「发起群收款」我再出确认卡。",
                    ask="aa_prefill")
    #: 2026-10-30（用户实测）：走到这里说明**不再缺槽**（要出卡/要执行了），
    #: 把之前的补槽记录清掉 —— 否则「还差这几项」表单会在随后几轮一直挂着。
    pending_slots.clear(session_id)
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
               "trace_id": str(trace_id or ""),
               "amount_cents": filled.get("amount_cents") or 0,
               "target_id": str(filled.get(spec.target_key) or "")}
    branch = (_to_pending(context, verdict) if verdict.delayed                       # L3：延迟 + 撤销
              else _to_confirmation_card(context, verdict, filled, prepared))        # L1/L2：确认卡
    branch.states = ["SLOT_FILL", "PRECHECK", *branch.states]
    #: 群收款（T9 无 `preview_*`）在 PRECHECK 阶段**不调任何工具** → `spec.tools` 为空，
    #: 工具名必须留空，否则 `/api/chat` 的 `tool_calls` 会谎报“已调用”，与 F15 验收点
    #: 「确认前不发起请求」直接冲突。
    branch.tools = list(spec.tools)
    branch.tool = branch.tool or (spec.tools[0] if spec.tools else None)
    branch.tier = verdict.tier
    return branch
