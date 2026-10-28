'Banking state machine: injection screening, context understanding, permission, tools, facts and audit.'

from __future__ import annotations

import logging
import uuid
from typing import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from agent import (classifier, confirm_card, payee_flow, pending_slots, read_flow, risk_flow,
                   scheduled_runner, session_memory, templates, write_flow, scene_runtime)
from agent.period import anchor_period, resolve_day, resolve_period
from agent.read_routes import TOOL_ROUTES
from data import dao
from data.seed import AS_OF
from guard import injection, permission
from tools._query_common import current_session_id
from tools.schemas import ToolResult

logger = logging.getLogger(__name__)

#: 卡 20：**表单提交入口**（导出给 `interfaces/` 用 —— 界面只调 `agent/`，不直连 `tools/`）。
#: 实现与理由见 `agent/payee_flow.py`（§5：`payee_add` → L1，不发确认卡、不要 OTP）。
submit_payee = payee_flow.submit_payee
#: F10：**风险测评问卷提交入口**（同上先例，见 `agent/risk_flow.py`；§2 第 108 行 L0 只读）。
submit_assessment = risk_flow.submit_assessment

# ---------------- 阈值与契约常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
STATES = ("IDLE", "CLASSIFY", "CLARIFY", "SLOT_FILL", "PRECHECK", "CONFIRM_CARD", "PENDING_REVIEW",
          "EXECUTE", "VERIFY_NUMBERS", "REPLY", "REFUSE", "AUDIT")     # 来源：规格 §4 的状态名
CONFIDENCE_FLOOR = 0.6        # 来源：规格 §4「confidence < 0.6 → CLARIFY」
MAX_CLARIFY_ROUNDS = 2        # 来源：卡 09 第 3 条「最多 2 轮」
#: `READ_TIER` / `RANGE_FIELDS` / `PERIOD_INTENTS` / `REQUIRED_SLOTS` 已随实现搬到
#: `agent/read_flow.py`（F10 接通后本文件超红线），在下面再导出。

#: 卡 09 的 8 个 + F10 `assess_risk` + SPEC-CHANGE 的 `payee_list`（T20，L0 无槽位）
READ_INTENTS = ("balance_query", "txn_query", "bill_analysis", "anomaly_check", "bill_report",
                "subscription_list", "card_query", "wealth_recommend", "risk_assess", "payee_list",
                "wealth_holdings", "wealth_all_products", "scheduled_list", "gift_order_list", "aa_list",
                "subscription_remind")

#: 写意图清单的唯一来源是 `agent/write_intents.py` 的写描述符表（card-24 泛化拆出），
#: 经 `write_flow.WRITE_INTENTS` 转发到这里 —— 全程只有一份，避免清单漂移。
WRITE_INTENTS = write_flow.WRITE_INTENTS

#: 相对时间的别名表 / 偏移 / 解析实现见 `agent/period.py`（卡 17b 拆出去：纯函数，也为守住单文件 ≤300 行）

#: 只读路由表在 `agent/read_routes.py`（卡 23 拆出）；这里 import 即再导出同一份表。

class Turn(BaseModel):
    """一次请求的处理结果（编排层 API，非冻结工具契约）。"""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    intent: str
    confidence: float = 0.0
    states: list[str] = []
    tool_calls: list[str] = []
    missing_slots: list[str] = []
    reply: str
    ask: str | None = None
    degraded: bool = False
    to_human: bool = False
    tier: str | None = None            # 卡 10：本次写操作的权限档（代码判定）
    executed: bool = False             # 卡 10：**只有**工具层执行成功才为 True
    pending_id: str | None = None      # 卡 10：L3 待复核编号（可撤销）
    error_code: str | None = None
    #: 只读回执的**事实包**（`ToolResult.facts` 原样透出）：界面要画图表、要给按钮带真实 id，
    #: 不该再去正则反解回执文本（那三条正则绑死在文案上，改一句措辞就断链 —— 见
    #: `interfaces/web/components.py` 的 `parse_bill`）。**只对只读路径填充**，写路径恒为空；
    #: 它不是工具契约的一部分，HTTP API 的冻结 6 字段（`interfaces/api/app.py`）不含它。
    facts: dict = Field(default_factory=dict)


class _Ctx:
    """一次请求的上下文（状态轨迹 + 审计字段）。"""

    def __init__(self, trace_id: str, session_id: str, clarify_round: int) -> None:
        self.trace_id, self.session_id, self.clarify_round = trace_id, session_id, clarify_round
        self.states: list[str] = []
        self.tool_calls: list[str] = []
        self.intent, self.confidence = "out_of_scope", 0.0
        self.slots: dict = {}
        self.missing: list[str] = []
        self.tier: str | None = None                 # 卡 10：权限档（guard 判定）
        self.executed = False                        # 卡 10：工具层执行成功才置 True
        self.pending_id: str | None = None           # 卡 10：L3 待复核编号
        #: 卡 25：只读路径的**事实包**（`ToolResult.facts` 原样透出），给界面画图表/挂按钮用。
        #: 写路径与各种提前返回的路径恒为 `{}` —— 只有**工具真的执行成功**才填，避免界面拿
        #: 到半成品数据画出空图。
        self.facts: dict = {}

    def enter(self, state: str) -> None:
        """进入一个状态（规格 §4 的状态名；顺序在返回结果里可查，便于断言「不可跳步」）。"""
        self.states.append(state)


# ---------------- 时间口径（代码判定，非 LLM） ----------------
# 实现见 `agent/period.py`（卡 17b 拆出：纯函数 + 守住单文件 ≤300 行）；
# 这里 import 进来的 `anchor_period / resolve_period / resolve_day` 同时也是**再导出**
# （历史调用点写 `orchestrator.resolve_period` 仍然有效，指向同一份实现）。


# ---------------- 主状态机 ----------------

def _with_date_context(text: str) -> str:
    """把「今天是几号」作为**数据**附在 user 消息里（铁律 7：不进 system prompt），便于解析相对时间。"""
    return f"{text}\n（当前日期：{AS_OF.isoformat()}）"


#: 只读路径的槽位填充与定档已移到 `agent/read_flow.py`（F10 接通后本文件到 310 行，超红线 300）。
#: 那些是**纯函数**（时间口径 + F10 问卷答案解析 + L0 定档），天然可独立；这里 import 即
#: **re-export**（同一份实现，不是副本），历史调用点写 `orchestrator._fill_slots` 仍然有效。
_fill_slots = read_flow.fill_slots
_fill_period_slot = read_flow.fill_period_slot
_precheck = lambda intent: read_flow.precheck(intent, READ_INTENTS)   # noqa: E731 —— 绑定本层清单

#: 阈值与契约常量的**唯一来源**已随实现搬到 `read_flow.py`；这里再导出（同一批对象，不是副本）。
PERIOD_INTENTS = read_flow.PERIOD_INTENTS
REQUIRED_SLOTS = read_flow.REQUIRED_SLOTS
RANGE_FIELDS = read_flow.RANGE_FIELDS
READ_TIER = read_flow.READ_TIER


def _code(result: ToolResult) -> str | None:
    """工具结果错误码字符串（唯一实现见 `write_flow.error_code_of`）。"""
    return write_flow.error_code_of(result)


def _finish(ctx: _Ctx, reply: str, *, result: str, tool: str | None = None,
            error_code: str | None = None, ask: str | None = None, degraded: bool = False,
            to_human: bool = False) -> Turn:
    """收尾：补 REPLY（若还没进）→ AUDIT → 返回 Turn。"""
    if ctx.states[-1] != "REPLY":
        ctx.enter("REPLY")
    ctx.enter("AUDIT")
    scene_runtime.remember(ctx, result, reply)
    _write_audit(ctx, tool=tool, result=result, error_code=error_code)
    return Turn(trace_id=ctx.trace_id, intent=ctx.intent, confidence=ctx.confidence, states=ctx.states,
                tool_calls=ctx.tool_calls, missing_slots=ctx.missing, reply=reply, ask=ask,
                degraded=degraded, to_human=to_human, error_code=error_code, tier=ctx.tier,
                executed=ctx.executed, pending_id=ctx.pending_id, facts=ctx.facts)


def _write_audit(ctx: _Ctx, *, tool: str | None, result: str, error_code: str | None) -> None:
    """编排层 AUDIT：每请求一条审计（铁律 5）。写审计失败只记日志，不改变本次回执。"""
    try:
        dao.insert_audit(ctx.trace_id, ctx.session_id, actor="agent", intent=ctx.intent, tool=tool,
                         params_json={"trace_id": ctx.trace_id, "slots": ctx.slots,
                                      "scene": getattr(ctx, 'scene_request', None),
                                      "states": ctx.states, "confidence": ctx.confidence,
                                      "missing_slots": ctx.missing, "tool_calls": ctx.tool_calls},
                         #: 写步骤要记**实际档位**（`ctx.tier` 由 write_flow 的回执带入）；
                         #: 只读/追问才记 READ_TIER。否则审计回放会把它看成 L0（实测）。
                         risk_level=ctx.tier or READ_TIER,
                         permission_tier=ctx.tier or READ_TIER, result=result,
                         error_code=error_code)
    except Exception:                                        # noqa: BLE001 —— 审计隔离于回执
        logger.exception("写审计失败（回执照常返回，但必须排查）：trace=%s", ctx.trace_id)


def _clarify(ctx: _Ctx, ask: str, *, reason: str) -> Turn:
    """CLARIFY：追问（≤2 轮），超出则转人工。理由只进日志，不回给用户。"""
    ctx.enter("CLARIFY")
    logger.info("CLARIFY(%s)：trace=%s missing=%s", reason, ctx.trace_id, ctx.missing)
    if ctx.clarify_round >= MAX_CLARIFY_ROUNDS:
        return _finish(ctx, templates.clarify_to_human(), result="rejected", ask=None, to_human=True)
    return _finish(ctx, ask, result="rejected", ask=ask)


def _apply(ctx: _Ctx, step: write_flow.Step) -> Turn:
    """把写路径的 `Step` 落成状态轨迹与审计（写路径不碰审计、不碰追问轮次上限）。"""
    if step.intent:
        ctx.intent = step.intent
    for state in step.states:
        ctx.enter(state)
    ctx.tool_calls.extend(step.tools or ([step.tool] if step.tool else []))   # 写路径的工具轨迹
    ctx.tier, ctx.executed, ctx.pending_id = step.tier, step.executed, step.pending_id
    if step.missing:
        ctx.missing = step.missing
        ask = step.ask or templates.clarify_missing(step.missing)   # F17：写路径给了更具体的追问就用它
        return _clarify(ctx, ask, reason="missing_slots")
    return _finish(ctx, step.reply, result=step.result, tool=step.tool, error_code=step.error_code,
                   ask=step.ask, degraded=step.degraded, to_human=step.to_human)


def handle(text: str, *, history: list[str] | None = None, clarify_round: int = 0,
           session_id: str | None = None, card_reader: Callable[[dict], ToolResult] | None = None,
           intent_resolver: Callable[[str, list[str] | None], classifier.IntentOut] | None = None) -> Turn:
    """**对外唯一入口**（F16）：先惰性结算到期预约 + 24h 提醒，再处理本次请求。

    结算/提醒都拼在回执**前面**且出错不影响本次请求；追问续接的上下文也在这里统一给。
    """
    sid = session_id or current_session_id()
    notices = [*scheduled_runner.settle_due(), *scheduled_runner.due_soon_notices(sid)]
    turn = _handle(text, history=session_memory.recent(sid, history), clarify_round=clarify_round,
                   session_id=sid, card_reader=card_reader, intent_resolver=intent_resolver)
    session_memory.remember(sid, text, turn.reply)     # 先记（不含结算告知），再做前缀
    if notices:
        turn.reply = "\n".join([*notices, turn.reply])
    return turn


def _handle(text: str, *, history: list[str] | None = None, clarify_round: int = 0,
            session_id: str | None = None, card_reader: Callable[[dict], ToolResult] | None = None,
            intent_resolver: Callable[[str, list[str] | None], classifier.IntentOut] | None = None) -> Turn:
    '处理一句用户输入，返回 `Turn`（含到达过的状态序列与调用过的工具）。'
    ctx = _Ctx(f"trace-{uuid.uuid4().hex[:12]}", session_id or current_session_id(), clarify_round)
    ctx.enter("IDLE")
    screen = injection.detect(text)                        # 规则层先行：确定性，命中就不调 LLM（卡 12b）
    if screen.blocked:
        ctx.intent = injection.UNSAFE_INTENT
        ctx.enter("REFUSE")
        return _finish(ctx, templates.refuse(screen.reason), result="rejected")
    if (amended := scene_runtime.amend_inflight(text, ctx.session_id)) is not None:
        return _apply(ctx, amended)
    if (inflight := write_flow.inflight(ctx.session_id)) is not None:      # 在途确认优先：短回复不再分类
        ctx.intent = inflight
        in_flight = confirm_card.current(ctx.session_id)
        if in_flight is not None and in_flight.trace_id:   # 同一笔业务沿用出卡时的 trace（审计可回放整条链）
            ctx.trace_id = in_flight.trace_id
        return _apply(ctx, write_flow.resume(ctx.session_id, text, classify=lambda t: (intent_resolver or classifier.classify)(_with_date_context(t), history)))
    ctx.enter("CLASSIFY")
    verdict = (scene_runtime.plan(ctx, text)
               or (intent_resolver or classifier.classify)(_with_date_context(text), history))
    corrected = classifier.correct_misroute(text, verdict.intent)   # 明显误判 → 纠正
    if corrected is not None:
        verdict = corrected
    #: 2026-10-27：追问后的这一句按分类结果合并回原意图（详见 `pending_slots.resume`）。
    if (answered := pending_slots.resume(ctx.session_id, text, verdict,
                                         trace_id=ctx.trace_id)) is not None:
        return _apply(ctx, answered)
    ctx.intent, ctx.confidence = verdict.intent, verdict.confidence
    ctx.slots = dict(verdict.slots)
    if verdict.intent == "unsafe_request":
        ctx.enter("REFUSE")
        return _finish(ctx, templates.refuse(verdict.unsafe_reason), result="rejected")
    if ctx.confidence < CONFIDENCE_FLOOR:
        return _clarify(ctx, templates.clarify_low_confidence(), reason="low_confidence")
    scene_runtime.enrich(ctx, text)
    if (response := scene_runtime.response(ctx)) is not None:
        return _apply(ctx, response)
    if ctx.intent == payee_flow.INTENT:                                  # 卡 20：只回引导语，界面据此弹表单
        ctx.enter("PRECHECK")
        ctx.tier = payee_flow.PAYEE_ADD_TIER
        payee_flow.capture_pending(ctx.session_id, ctx.slots)            # 表单预填：姓名/手机号已说过
        return _finish(ctx, payee_flow.PROMPT, result="pending_confirm")
    if ctx.intent == risk_flow.INTENT and not risk_flow.answers_ready(ctx.slots.get("answers")):
        #: F10：聊天里只说"做测评"（或一个答案都没说出来）→ 回引导语、界面弹问卷（**不计分**）；
        #: 若用户已经把 5 题一并说了 → 落回下面的只读路径一次算完，不弹表单。判断用 `answers_ready`：
        #: 它认两种形态（已解析的 dict / 待解析的一句话），**不依赖 `_fill_slots` 是否已跑过**
        #: （本分支在 SLOT_FILL 之前执行，那时 `answers` 还是 LLM 抽出来的原始字符串）。
        #: **说了一部分**（解析得出 1..4 题）不算这里，让它落到 `_read_flow` 的 SLOT_FILL ——
        #: 那里 `answers` 会解析成 dict、判出缺哪些题走 CLARIFY 追问，比"让他重填整张卷"友好。
        if not risk_flow.answers_started(ctx.slots.get("answers")):
            ctx.enter("PRECHECK")
            ctx.tier = risk_flow.TIER
            return _finish(ctx, risk_flow.PROMPT, result="pending_confirm")
    if ctx.intent in WRITE_INTENTS:                                      # 卡 10：写路径
        return _apply(ctx, write_flow.start(ctx.intent, ctx.slots, ctx.session_id,
                                        trace_id=ctx.trace_id))
    return _read_flow(ctx, card_reader=card_reader)


def _read_flow(ctx: _Ctx, *, card_reader: Callable[[dict], ToolResult] | None = None) -> Turn:
    """只读路径（卡 09）：SLOT_FILL → PRECHECK → EXECUTE → VERIFY_NUMBERS → 回执。"""
    if ctx.intent not in READ_INTENTS:
        ctx.enter("PRECHECK")
        return _finish(ctx, templates.unsupported(ctx.intent), result="rejected")
    ctx.enter("SLOT_FILL")
    ctx.slots, ctx.missing = (ctx.slots, []) if getattr(ctx, "scene_request", None) else _fill_slots(ctx.intent, ctx.slots)
    if ctx.missing:
        return _clarify(ctx, templates.clarify_missing(ctx.missing, ctx.intent), reason="missing_slots")
    ctx.enter("PRECHECK")
    if _precheck(ctx.intent) != READ_TIER or ctx.intent not in TOOL_ROUTES:
        return _finish(ctx, templates.unsupported(ctx.intent), result="rejected")   # 无路由=未接通
    name, call = scene_runtime.route(ctx) or TOOL_ROUTES[ctx.intent]
    if ctx.intent == "card_query" and card_reader is not None and not getattr(ctx, "scene_request", None):
        name, call = "list_bound_cards", card_reader
    ctx.enter("EXECUTE")
    ctx.tool_calls.append(name)
    result = call(ctx.slots)
    if not result.ok:
        code = _code(result)
        return _finish(ctx, templates.tool_error(code, result.message), result="error",
                       tool=name, error_code=code)
    ctx.enter("VERIFY_NUMBERS")
    ctx.read_result = result
    if getattr(ctx, "scene_request", None):
        reply, degraded = result.message, bool(templates.verify_numbers(result.message, result.facts))
        if degraded:
            return _finish(ctx, "查询结果校验未通过，请稍后重试。", result="error", tool=name, error_code="HALLUCINATION_BLOCKED")
    else:
        reply, degraded, _attempts = templates.compose_reply(ctx.intent, ctx.slots, result)
    ctx.facts = dict(result.facts or {})
    return _finish(ctx, reply, result="success", tool=name,
                   error_code="HALLUCINATION_BLOCKED" if degraded else None, degraded=degraded)
