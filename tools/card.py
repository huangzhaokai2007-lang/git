"""工具层 T12（任务卡 06）：卡片管理 —— apply / adjust_limit / set_txn_limit / lock / unlock / report_lost。

依据 `docs/01-接口规格.md` 第 2 节 T12 + 第 5 节权限矩阵 + 第 4 节状态机 + `CLAUDE.md` 铁律：
- 每个 action 都属**高危写**（§5：L2 = 确认卡 + OTP；L3 = 双因子 + 延迟 60s 生效），
  故一律要求本层签发的 `confirm_ref`（见 `tools/subscription.py` 的凭证机制）+ OTP，缺一 `FORBIDDEN`。
- 写路径在**一个事务**内（状态翻转 + 写审计），不嵌套 `BEGIN`（台账 R1）；
  `_CONFIRM_LOCK` 复用订阅侧的临界区（同一把锁保护凭证的校验→执行→写快照）。
- 归属：卡片必须属于当前用户（越权 → `FORBIDDEN`）；金额整数分、禁浮点。

口径（逐条进交付说明的「需要人类决定」）：
- **状态机矩阵**（本卡定稿；非法流转一律 `INVALID_STATE`）：
  `normal --lock--> locked`｜`locked --unlock--> normal`｜`任意非 lost --report_lost--> lost`
  `lost --unlock--> INVALID_STATE`（卡 06 第 4 条：已挂失只能补卡）｜限额类仅允许 `status == normal`
  （`lost`/`frozen`/`locked` 的卡不可调额）。
- **权限档映射**：规格只明确 `apply=L2`、`report_lost=L3`；`adjust_limit`/`set_txn_limit`/`lock` 按 §5
  「调额」类归 L2、`unlock` 按 §5「解锁」归 L3。
- `apply`（新卡申请）**不落库**：DDL 没有申请表、DAO 也没有建卡原语（`update_card` 只改现存行）。
  返回"申请快照（status=pending_review，不入 DDL 的卡片状态枚举）+ 审计"，属规格自家 mock 风格
  （同 T16 的"mock 预订"）；要真建卡得先补 DAO 原语 + 申请表。
- L3 的「延迟 60s 生效 + 可撤销」：**§4 PENDING_REVIEW + §5 L3 已把分层定死**（reviewer 拍：不是待定，是既定）——
  工具层只做 L3 档位 + 双因子 + facts 给 `l3_delay_seconds` / `revocable`（档位口径，
  供编排层渲染确认卡）。60s 延迟窗口的状态机（含撤销）是编排层 PENDING_REVIEW 的活（卡 10）：
  窗口在**执行之前**行使（撤销 = 窗口内不调用执行）；一旦本函数执行完，`report_lost` 后的卡是终局，
  不可逆（卡 06 第 4 条：已挂失只能补卡）→ 故 facts 另给 `l3_window_phase="pre_execution"` 说明窗口所处阶段。
- 越权/伪造被拒（`FORBIDDEN`，含 OTP 失败）→ 记 `audit_log.result='rejected'`（`reject_audit`，reviewer 钉死）。
- 数据面：规格 T12 的 data 写「card 快照」→ 本卡取**卡片行本体**（DDL 的 9 个列）为 data；
  `apply` 因为是申请单，返回同名列的"申请快照"+`request_id`。
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from data import dao
from data.db import transaction
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money_facts, _ok, current_user_id, require_owned,
)
from tools.schemas import ErrorCode, ToolResult
from tools.subscription import (
    _CONFIRM_LOCK, check_confirm_ref, confirmed_result, finish_confirm_ref, reject_audit,
)

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
L3_DELAY_SECONDS = 60           # 来源：规格 §5 L3「双因子 + 延迟 60s 生效（可撤销）」
OTP_CODE = "123456"             # 来源：规格 §5 L2「demo 中固定 123456」；**绝不写日志/facts/回执**
MANAGE_ACTION = "manage_card"   # 凭证绑定的 action 名（与工具名一致）
CARD_STATUSES = ("normal", "locked", "lost", "frozen")

#: 权限档映射（来源：规格 §2 T12 明写 apply=L2 / report_lost=L3；其余按 §5「调额/解锁」归类，待人类确认）
ACTION_TIERS = {"apply": "L2", "adjust_limit": "L2", "set_txn_limit": "L2", "lock": "L2",
                "unlock": "L3", "report_lost": "L3"}
#: 审计 intent（取自规格第 3 节的意图清单）
ACTION_INTENTS = {"apply": "card_apply", "adjust_limit": "card_limit_adjust",
                  "set_txn_limit": "card_limit_adjust", "lock": "card_lock",
                  "unlock": "card_unlock", "report_lost": "card_report_lost"}
LIMIT_FIELDS = ("credit_limit", "single_limit", "daily_limit")
#: 每个 action 允许出现的业务 kw（规格/卡 06 口径外的一律拒，**不许静默忽略**）
KW_BY_ACTION = {
    "apply": {"card_type"},
    "adjust_limit": {"credit_limit"},
    "set_txn_limit": {"single_limit", "daily_limit"},
    "lock": set(), "unlock": set(), "report_lost": set(),
}
STRICT = ConfigDict(strict=True, extra="forbid")


# ---------------- 出入参模型（卡 06 范围只列了 subscription/card/tests → 模型先放本文件） ----------------

class ManageCardReq(BaseModel):
    """`manage_card(card_id, action, **kw)` 的 kw 白名单：多余字段直接拒（`extra="forbid"`）。"""

    model_config = STRICT

    card_id: str = Field(min_length=1)
    action: Literal["apply", "adjust_limit", "set_txn_limit", "lock", "unlock", "report_lost"]
    credit_limit: int | None = Field(default=None, ge=0)
    single_limit: int | None = Field(default=None, ge=0)
    daily_limit: int | None = Field(default=None, ge=0)
    card_type: Literal["savings", "credit"] | None = None
    confirm_ref: str | None = None
    otp: str | None = None


class CardData(BaseModel):
    """card 快照：DDL 的 9 个列（`apply` 的申请快照同形，另有 request_id）。"""

    id: str
    user_id: str
    account_id: str
    card_no_mask: str
    type: str
    credit_limit: int | None = None
    single_limit: int | None = None
    daily_limit: int | None = None
    status: str


# ---------------- 内部辅助 ----------------

def _snapshot(row: dict) -> dict:
    """按 DDL 的列顺序取 card 快照（其余列不外泄）。"""
    return {key: row.get(key) for key in CardData.model_fields}


def _limit_facts(row: dict) -> dict:
    """三档限额进事实包（整数分本体 + 元展示串）；None 保持 None（该卡没有这档限额）。"""
    facts: dict = {}
    for name in LIMIT_FIELDS:
        value = row.get(name)
        facts[name] = value
        facts[f"{name}_yuan"] = None if value is None else _money_facts(value, name)[f"{name}_yuan"]
    return facts


def _l3_facts() -> dict:
    """L3 档位口径（只在 L3 事实包里出现；L2 完全不带这些键 —— 测试钉住）。

    键名按 reviewer 钉死：`l3_delay_seconds=60` / `revocable=True`（**档位口径**，不是"执行完还能撤"）。
    窗口由编排层在**执行之前**行使（撤销 = 窗口内不调用执行）；一旦本函数执行完，`report_lost` 后的卡
    是终局（卡 06 第 4 条）→ 故另给 `l3_window_phase` 说明窗口所处阶段，避免回执被读成事后承诺。
    """
    return {"l3_delay_seconds": L3_DELAY_SECONDS, "revocable": True,
            "l3_window_phase": "pre_execution", "to_human": True}


def _guard(action: str, row: dict, req: ManageCardReq) -> ToolResult | None:
    """写前置校验：kw 与 action 的匹配 → 状态流转合法性 → 限额参数齐备性。"""
    offenders = [name for name in LIMIT_FIELDS + ("card_type",)
                 if name not in KW_BY_ACTION[action] and getattr(req, name) is not None]
    if offenders:
        return _fail(ErrorCode.INVALID_ARGUMENT, f"{action} 不接受参数：{', '.join(offenders)}")
    status = row["status"]
    if action == "lock" and status != "normal":
        return _fail(ErrorCode.INVALID_STATE, "该卡当前状态不允许锁定")
    if action == "unlock" and status == "lost":
        return _fail(ErrorCode.INVALID_STATE, "已挂失的卡不能解挂，请走补卡")
    if action == "unlock" and status != "locked":
        return _fail(ErrorCode.INVALID_STATE, "该卡未锁定，无需解锁")
    if action == "report_lost" and status == "lost":
        return _fail(ErrorCode.INVALID_STATE, "该卡已处于挂失状态")
    if action in ("adjust_limit", "set_txn_limit") and status != "normal":
        return _fail(ErrorCode.INVALID_STATE, "该卡当前状态不允许调整限额")
    if action == "adjust_limit" and req.credit_limit is None:
        return _fail(ErrorCode.INVALID_ARGUMENT, "调整额度需要给出 credit_limit")
    if action == "set_txn_limit" and req.single_limit is None and req.daily_limit is None:
        return _fail(ErrorCode.INVALID_ARGUMENT, "设置交易限额需要给出 single_limit 或 daily_limit")
    return None


def _payload(action: str, req: ManageCardReq, row: dict) -> dict:
    """该 action 要写进 `update_card` 的字段（都是 DAO 的白名单字段）。"""
    if action == "lock":
        return {"status": "locked"}
    if action == "unlock":
        return {"status": "normal"}
    if action == "report_lost":
        return {"status": "lost"}
    if action == "adjust_limit":
        return {"credit_limit": req.credit_limit}
    return {name: getattr(req, name) for name in ("single_limit", "daily_limit")
            if getattr(req, name) is not None}


# ---------------- T12 manage_card ----------------

def manage_card(card_id: str, action: str, **kw: object) -> ToolResult:
    """T12 卡片管理（L2/L3）。data: card 快照。

    `kw` 白名单见 `ManageCardReq`（`confirm_ref` + `otp` 必填；限额类另给 `credit_limit` 等）；
    高危写一律要求确认凭证与 OTP，否则 `FORBIDDEN`；同凭证重复调用返回同一结果（**幂等**）。

    顺序：卡片存在 → 归属 → 凭证（`TOKEN_EXPIRED`/`FORBIDDEN`）→ OTP → 已执行则返回快照（幂等）
    → kw 与 action 匹配 → 状态流转 → 事务内（翻转 + 审计）→ 写快照。与 card-05 的 T8 同序（OTP 先于幂等）。
    """
    if (bad := _invalid(ManageCardReq, card_id=card_id, action=action, **kw)) is not None:
        return bad
    req = ManageCardReq(card_id=card_id, action=action, **kw)
    with _CONFIRM_LOCK:                                          # 校验 → 执行 → 写快照，整体串行
        return _manage_locked(req)


def _manage_locked(req: ManageCardReq) -> ToolResult:
    """`manage_card` 的临界区主体；调用方必须已持有 `_CONFIRM_LOCK`。"""
    action, tier = req.action, ACTION_TIERS[req.action]
    try:
        row = dao.get_card(req.card_id)
        if row is None:
            return _fail(ErrorCode.NOT_FOUND, "找不到该卡片")
        require_owned("卡片", row["user_id"], row["id"])
        if not req.confirm_ref:
            return _fail(ErrorCode.FORBIDDEN, "该操作需要确认凭证")
        record = check_confirm_ref(req.confirm_ref, MANAGE_ACTION, req.card_id)
        if req.otp is None:
            return _fail(ErrorCode.FORBIDDEN, "该操作需要验证码")
        if req.otp != OTP_CODE:
            logger.warning("OTP 校验失败：card=%s action=%s user=%s", req.card_id, action, current_user_id())
            return _fail(ErrorCode.FORBIDDEN, "验证码不正确")
        if (cached := confirmed_result(record)) is not None:
            return cached
        if (blocked := _guard(action, row, req)) is not None:
            return blocked
        if action == "apply":
            return _apply_mock(req, row, tier, record)
        conn = dao.connection()
        with transaction(conn):                                  # 单一事务：翻转 + 审计（不嵌套 BEGIN）
            updated = dao.update_card(req.card_id, **_payload(action, req, row))
            if updated is None:                                  # 防御性兜底，正常走不到
                raise ToolError(ErrorCode.NOT_FOUND, "卡片不存在")
            dao.insert_audit(record["trace_id"], record["session_id"], actor="agent",
                             intent=ACTION_INTENTS[action], tool=MANAGE_ACTION,
                             params_json={"card_id": req.card_id, "action": action,
                                          "status_before": row["status"], "tier": tier,
                                          **({} if tier == "L2" else _l3_facts())},
                             risk_level=tier, permission_tier=tier, result="success")
    except ToolError as exc:
        if exc.code == ErrorCode.FORBIDDEN:                  # 越权/伪造被拒 → 留痕（reviewer 钉死）
            reject_audit(MANAGE_ACTION, ACTION_INTENTS[action], req.card_id, exc.message, tier)
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    return _finalize(req, row, updated, tier, record)


def _apply_mock(req: ManageCardReq, row: dict, tier: str, record: dict) -> ToolResult:
    """`apply` 的 mock 落地：不建卡行（无 DAO 原语/无申请表），返回申请快照 + 写审计。

    申请单**还没有卡号**：`id` 置 `None`（**绝不复用参考卡的 id** —— 否则回执看起来像"那张卡被改了状态"），
    参考卡只作模板，另行记录 `ref_card_id`。
    """
    card_type = req.card_type or row["type"]
    credit_limit = row["credit_limit"] if card_type == "credit" else None
    snapshot = {"id": None, "user_id": current_user_id(), "account_id": row["account_id"],
                "card_no_mask": None, "type": card_type, "credit_limit": credit_limit,
                "single_limit": row["single_limit"], "daily_limit": row["daily_limit"],
                "status": "pending_review", "ref_card_id": req.card_id}
    dao.insert_audit(record["trace_id"], record["session_id"], actor="agent",
                     intent=ACTION_INTENTS["apply"], tool=MANAGE_ACTION,
                     params_json={"card_id": req.card_id, "action": "apply", "card_type": card_type,
                                  "tier": tier, "mocked": True, "pending": True},
                     risk_level=tier, permission_tier=tier, result="pending_confirm")
    request_id = f"cardreq_{uuid.uuid4().hex[:12]}"
    facts = {"card_id": req.card_id, "action": "apply", "tier": tier,
             "requires_otp": True, "pending": True, "mocked": True, "request_id": request_id,
             "card_type": card_type, "status_after": "pending_review", "human_review": True,
             **_limit_facts(snapshot)}
    message = (f"已受理 {card_type} 新卡申请（申请单 {request_id}），待人工复核后制卡；"
               f"本卡不建卡行（规格无申请表口径）。")
    finish_confirm_ref(req.confirm_ref or "", {**snapshot, "request_id": request_id}, facts, message)
    return _ok({**snapshot, "request_id": request_id}, facts, message)


def _finalize(req: ManageCardReq, row: dict, updated: dict, tier: str, record: dict) -> ToolResult:
    """把写后快照整理成回执（facts 覆盖金额与状态流转，message 只引用 facts 里的数字）。"""
    data = _snapshot(updated)
    facts = {"card_id": req.card_id, "action": req.action, "tier": tier, "requires_otp": True,
             "status_before": row["status"], "status_after": updated["status"], **_limit_facts(updated)}
    if tier == "L3":
        facts.update(_l3_facts())
        message = (f"已完成 {req.action}（档位 L3：双因子校验通过、已标记人工复核）；"
                   f"卡片状态 {facts['status_before']} → {facts['status_after']}。")
    else:
        message = (f"已完成 {req.action}（档位 L2）：卡片状态 {facts['status_before']} → "
                   f"{facts['status_after']}。")
    finish_confirm_ref(req.confirm_ref or "", data, facts, message)
    return _ok(data, facts, message)
