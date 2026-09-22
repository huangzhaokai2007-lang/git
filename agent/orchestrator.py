"""编排层状态机（卡 09）：严格按 `docs/01-接口规格.md` §4 主线走，本卡只接通 8 个 L0 只读意图。

主线（规格 §4，**不可跳步**）::

    IDLE → CLASSIFY(LLM) → [unsafe_request → REFUSE] → [置信度 <0.6 → CLARIFY]
         → SLOT_FILL → PRECHECK(代码定档) → [L0] EXECUTE(工具,幂等)
         → VERIFY_NUMBERS(数字校验) → REPLY(模板优先,LLM 只润色) → AUDIT → IDLE

    本卡 PRECHECK 对只读意图恒为 **L0**；L1/L2 的 CONFIRM_CARD 与 L3 的 PENDING_REVIEW 属写操作，卡 10 接。

本卡红线（卡 09 第 5/6 条 + 铁律 1/2）：
- 回执优先用 `agent/templates.py` 的模板，**数字全部从 `ToolResult.facts` 注入**；
- LLM 只许润色**措辞**：润色结果必须过 `verify_numbers`；出现 facts 之外的数字 → 重生成一次 →
  仍不过 → **降级为模板原样回执**并写 `audit_log`（`error_code=HALLUCINATION_BLOCKED`，与卡 13 口径一致）；
- 本卡**不碰任何写操作**：非只读意图一律回「未接通」模板，**不执行任何工具**。

口径（规格未定义处，逐条进交付说明）：
- §4 的 `SLOT_FILL` 在本卡落地为「代码补齐 + 校验」：复用 classifier 已给出的 `slots`，
  **不额外调一次 LLM**（少一次网络往返、也少一次幻觉机会）；是否追问由本层的 `REQUIRED_SLOTS` 决定
  （classifier 的 `missing_slots` 只作参考 —— 铁律 1：代码说了算）。
- 相对时间（「上个月」/「本月」/`last_month` 等变体）由**代码**解析（剧本第 206 行的验收要求
  「上个月花了多少」必须真的调到 `analyze_spending`）：时间锚 = `data.seed.AS_OF`；分析类意图
  **完全没给** `period` 时取锚点当月（卡 09 口径），而**给了却认不出**（表外写法/其它相对说法）
  则判缺失 → CLARIFY 追问 —— 卡 17b 实测旧实现会静默回落当月，把「上个月」答成「本月 0.00 元」；
  `txn_query` 的显式区间**不猜** —— 缺了就用 `REQUIRED_SLOTS` 触发 CLARIFY 追问。
- CLARIFY 上限 2 轮（卡 09 第 3 条），超出回「转人工」话术；每请求一条 `audit_log`（编排层 AUDIT 状态；
  工具层的只读工具本身不写审计，两者不冲突）。
- `card_query` 已由**卡 23** 接通（只读工具 T18 `list_cards` → 路由在 `agent/read_routes.py`）：
  「我几张卡」「我的卡」「我有没有挂失的卡」都命中；§2 的**写**类卡片操作仍未接通。
"""

from __future__ import annotations

import logging
import uuid
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from agent import classifier, confirm_card, payee_flow, templates, write_flow
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

# ---------------- 阈值与契约常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
STATES = ("IDLE", "CLASSIFY", "CLARIFY", "SLOT_FILL", "PRECHECK", "CONFIRM_CARD", "PENDING_REVIEW",
          "EXECUTE", "VERIFY_NUMBERS", "REPLY", "REFUSE", "AUDIT")     # 来源：规格 §4 的状态名
CONFIDENCE_FLOOR = 0.6        # 来源：规格 §4「confidence < 0.6 → CLARIFY」
MAX_CLARIFY_ROUNDS = 2        # 来源：卡 09 第 3 条「最多 2 轮」
READ_TIER = "L0"              # 来源：规格 §2 的 8 个只读意图权限档均为 L0
RANGE_FIELDS = ("date_from", "date_to")

#: 卡 09 第 2 条点名的 8 个只读意图（其余意图本卡不执行）
READ_INTENTS = ("balance_query", "txn_query", "bill_analysis", "anomaly_check", "bill_report",
                "subscription_list", "card_query", "wealth_recommend")
#: 分析类意图：period 的缺省/解析口径（**完全没给**时取锚点当月；给了却认不出 → 追问，卡 17b）
PERIOD_INTENTS = ("bill_analysis", "anomaly_check", "bill_report")
#: 缺槽即追问（代码判定）：`txn_query` 的显式区间不猜；分析类意图的 `period` 认不出也不猜
REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {"txn_query": RANGE_FIELDS,
                                             **{intent: ("period",) for intent in PERIOD_INTENTS}}

#: 写意图清单定义在 `agent/write_flow.py`（写路径的唯一归属）；这里是引用别名，避免两份清单漂移
WRITE_INTENTS = write_flow.WRITE_INTENTS
#: 分析类意图的 period 缺省/解析口径（锚点当月）
PERIOD_INTENTS = ("bill_analysis", "anomaly_check", "bill_report")

#: 相对时间的别名表 / 偏移 / 解析实现见 `agent/period.py`（卡 17b 拆出去：纯函数，也为守住单文件 ≤300 行）

#: 只读意图 → (工具名, 调用器) 的那张表已移到 `agent/read_routes.py`（卡 23 拆出：本文件当时 293 行，
#: 而 card-23 要把 `card_query` 接上 T18）；这里 import 即 **re-export** ——
#: `orchestrator.TOOL_ROUTES` 仍指向**同一份表**（不是复制），历史调用点行为不变。

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


def _fill_period_slot(filled: dict) -> None:
    """period 槽位：**没给** → 锚点当月（卡 09 口径，未变）；**给了但认不出** → 清掉（→ CLARIFY）。"""
    raw = filled.get("period")
    resolved = anchor_period() if raw in (None, "") else resolve_period(raw)
    if resolved is None:
        filled.pop("period", None)
    else:
        filled["period"] = resolved


def _fill_slots(intent: str, slots: Mapping) -> tuple[dict, list[str]]:
    """代码补齐时间槽位，然后算缺失项（缺 → CLARIFY）。"""
    filled = {key: value for key, value in slots.items() if value not in (None, "")}
    if intent in PERIOD_INTENTS:
        _fill_period_slot(filled)
    if intent == "txn_query":
        for field, edge in (("date_from", "start"), ("date_to", "end")):
            resolved = resolve_day(filled.get(field), edge=edge)
            if resolved is None:
                filled.pop(field, None)
            else:
                filled[field] = resolved
    missing = [name for name in REQUIRED_SLOTS.get(intent, ()) if not filled.get(name)]
    return filled, missing


def _precheck(intent: str) -> str:
    """权限预检（代码定档）。本卡只接只读意图 → 恒 L0；写操作与风控因子属卡 10/12。"""
    return READ_TIER if intent in READ_INTENTS else "L1+"


def _code(result: ToolResult) -> str | None:
    """工具结果错误码字符串（实现唯一在 `write_flow.error_code_of`，这里只做转发）。"""
    return write_flow.error_code_of(result)


def _finish(ctx: _Ctx, reply: str, *, result: str, tool: str | None = None,
            error_code: str | None = None, ask: str | None = None, degraded: bool = False,
            to_human: bool = False) -> Turn:
    """收尾：补 REPLY（若还没进）→ AUDIT → 返回 Turn。"""
    if ctx.states[-1] != "REPLY":
        ctx.enter("REPLY")
    ctx.enter("AUDIT")
    _write_audit(ctx, tool=tool, result=result, error_code=error_code)
    return Turn(trace_id=ctx.trace_id, intent=ctx.intent, confidence=ctx.confidence, states=ctx.states,
                tool_calls=ctx.tool_calls, missing_slots=ctx.missing, reply=reply, ask=ask,
                degraded=degraded, to_human=to_human, error_code=error_code, tier=ctx.tier,
                executed=ctx.executed, pending_id=ctx.pending_id)


def _write_audit(ctx: _Ctx, *, tool: str | None, result: str, error_code: str | None) -> None:
    """编排层 AUDIT：每请求一条审计（铁律 5）。写审计失败只记日志，不改变本次回执。"""
    try:
        dao.insert_audit(ctx.trace_id, ctx.session_id, actor="agent", intent=ctx.intent, tool=tool,
                         params_json={"trace_id": ctx.trace_id, "slots": ctx.slots,
                                      "states": ctx.states, "confidence": ctx.confidence,
                                      "missing_slots": ctx.missing, "tool_calls": ctx.tool_calls},
                         risk_level=READ_TIER, permission_tier=READ_TIER, result=result,
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
        return _clarify(ctx, templates.clarify_missing(step.missing), reason="missing_slots")
    return _finish(ctx, step.reply, result=step.result, tool=step.tool, error_code=step.error_code,
                   ask=step.ask, degraded=step.degraded, to_human=step.to_human)


def handle(text: str, *, history: list[str] | None = None, clarify_round: int = 0,
           session_id: str | None = None) -> Turn:
    """处理一句用户输入，返回 `Turn`（含到达过的状态序列与调用过的工具）。

    `clarify_round`：调用方在追问后续接时自增（0 → 最多 2 轮追问后转人工）。
    写操作走 `_start_write` / `_resume_write`（preview → 档位 → 确认/OTP → 幂等执行）；
    档位与因子由 `guard.permission` 纯代码判定，本函数只调度（铁律 1）。
    """
    ctx = _Ctx(f"trace-{uuid.uuid4().hex[:12]}", session_id or current_session_id(), clarify_round)
    ctx.enter("IDLE")
    screen = injection.detect(text)                        # 规则层先行：确定性，命中就不调 LLM（卡 12b）
    if screen.blocked:
        ctx.intent = injection.UNSAFE_INTENT
        ctx.enter("REFUSE")
        return _finish(ctx, templates.refuse(screen.reason), result="rejected")
    if (inflight := write_flow.inflight(ctx.session_id)) is not None:      # 在途确认优先：短回复不再分类
        ctx.intent = inflight
        return _apply(ctx, write_flow.resume(ctx.session_id, text))
    ctx.enter("CLASSIFY")
    verdict = classifier.classify(_with_date_context(text), history)
    ctx.intent, ctx.confidence = verdict.intent, verdict.confidence
    ctx.slots = dict(verdict.slots)
    if verdict.intent == "unsafe_request":
        ctx.enter("REFUSE")
        return _finish(ctx, templates.refuse(verdict.unsafe_reason), result="rejected")
    if ctx.confidence < CONFIDENCE_FLOOR:
        return _clarify(ctx, templates.clarify_low_confidence(), reason="low_confidence")
    if ctx.intent == payee_flow.INTENT:                                  # 卡 20：只回引导语，界面据此弹表单
        ctx.enter("PRECHECK")
        ctx.tier = payee_flow.PAYEE_ADD_TIER
        return _finish(ctx, payee_flow.PROMPT, result="pending_confirm")
    if ctx.intent in WRITE_INTENTS:                                      # 卡 10：写路径
        return _apply(ctx, write_flow.start(ctx.intent, ctx.slots, ctx.session_id))
    return _read_flow(ctx)


def _read_flow(ctx: _Ctx) -> Turn:
    """只读路径（卡 09）：SLOT_FILL → PRECHECK → EXECUTE → VERIFY_NUMBERS → 回执。"""
    if ctx.intent not in READ_INTENTS:
        ctx.enter("PRECHECK")
        return _finish(ctx, templates.unsupported(ctx.intent), result="rejected")
    ctx.enter("SLOT_FILL")
    ctx.slots, ctx.missing = _fill_slots(ctx.intent, ctx.slots)
    if ctx.missing:
        return _clarify(ctx, templates.clarify_missing(ctx.missing), reason="missing_slots")
    ctx.enter("PRECHECK")
    if _precheck(ctx.intent) != READ_TIER or ctx.intent not in TOOL_ROUTES:
        return _finish(ctx, templates.unsupported(ctx.intent), result="rejected")   # 无路由=未接通
    name, call = TOOL_ROUTES[ctx.intent]
    ctx.enter("EXECUTE")
    ctx.tool_calls.append(name)
    result = call(ctx.slots)
    if not result.ok:
        code = _code(result)
        return _finish(ctx, templates.tool_error(code, result.message), result="error",
                       tool=name, error_code=code)
    ctx.enter("VERIFY_NUMBERS")
    reply, degraded, _attempts = templates.compose_reply(ctx.intent, ctx.slots, result)
    return _finish(ctx, reply, result="success", tool=name,
                   error_code="HALLUCINATION_BLOCKED" if degraded else None, degraded=degraded)
