"""工具层 T10–T11（任务卡 06）：订阅列表（含僵尸订阅标注）+ 取消订阅（L2 高危写，confirm_ref 确认闭环）。

依据 `docs/01-接口规格.md` 第 2 节 T10–T11、第 5 节权限矩阵 + `CLAUDE.md` 铁律：
- T10 只读（L0）；T11 = **L2 高危写**，必须持有本层签发的确认凭证，否则 `FORBIDDEN`。
- 写路径在**一个事务**内（状态翻转 + 写审计），复用 DAO `_writing()` 的 `in_transaction` 检测，
  绝不嵌套 `BEGIN`（台账 R1）；`_CONFIRM_LOCK` 把「校验凭证 → 执行 → 写快照」整体串行（卡 04b 的 TOCTOU 教训）。
- 归属：订阅必须属于当前用户（越权 → `FORBIDDEN`）；金额整数分、禁浮点。

口径（逐条写进交付说明的「需要人类决定」）：
- **`confirm_ref` 是自包含凭证**（reviewer 开工前依赖提醒的定稿口径，仿 card-05 `preview_token`）：
  本层 `issue_confirm_ref(action, target_id)` 签发、本层校验、TTL 300s、绑定（action + target_id + 当前用户），
  一次性消费并把结果快照写回 → 同 ref 重复调用返回同一结果（**幂等**）。
  `issue_confirm_ref` 是**工具层私有约定**（非规格冻结的 17 个工具函数，对齐 §6 `set_current_user` 的认可口径）。
  错误码边界（reviewer 钉死）：**不存在 / 非本人 / 绑定不符 / AI 自造串 → `FORBIDDEN`**（越权或伪造，
  记 `audit_log.result='rejected'`）；**存在但超 TTL → `TOKEN_EXPIRED`**（良性超时，不记审计）。
  真·确认卡绑定的状态机属编排层（卡 09/10）—— 本层只保证"凭证由本层签发、且绑定本次操作"。
- 疑似僵尸订阅 = 进行中订阅中，最近 `ZOMBIE_WINDOW_MONTHS` 个月内**没有对应使用记录**的。使用记录口径
  （reviewer 钉死）：该订阅的**扣费流水** —— `txn.counterparty == merchant` 的流水，**或** `subscription.source_txn_id`
  指向的流水，其 `ts` 落在窗口内即算有使用（不是"用户打开过 app"这种不可观测信号）。
  窗口锚点用 `data.seed.AS_OF`（数据集的"今天"，固定常量 → 跨天可复现，**不用** `datetime.now()`）。
  年费订阅（如 `sub_0004`，上次扣费 2025-11）按字面口径会被标僵尸，**这是字面口径的必然结果**（同卡 04
  固定还款命中），处置 = 如实登记，不给自己加 cycle 豁免规则。
  标注走 **facts**：规格 T10 的 item 字段冻结为 5 个，不得增字段。
- 取消**立即生效**：`effective_date` = 操作当日（规格未定义生效日口径）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from data import dao
from data.db import transaction
from data.seed import AS_OF
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money_facts, _ok, _owned_account_ids, current_user_id,
    current_session_id, month_windows, require_owned,
)
from tools.schemas import MAX_LIMIT, ErrorCode, ToolResult

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
CONFIRM_TTL_SECONDS = 300       # 来源：卡 06 口径（自包含 ref，仿 card-05 preview_token 同族 TTL 300s）
ZOMBIE_WINDOW_MONTHS = 3        # 来源：卡 06 第 1 条「最近 3 个月无对应使用记录」
CANCEL_TIER = "L2"              # 来源：规格 §2 T11 权限档列 = L2（高危，confirm_ref 必须来自确认卡）
CANCEL_ACTION = "cancel_subscription"   # 凭证绑定的 action 名（与工具名一致）

STRICT = ConfigDict(strict=True, extra="forbid")
SUBSCRIPTION_STATUSES = ("active", "cancelled", "paused")
#: 周期展示文案（**仅展示**，不改枚举值）。与 `tools/_query_analysis.py` 的 CYCLE_LABELS 同口径，
#: 测试里钉住两者一致（漂移即红）；`06b` 抽共享层时合并。
CYCLE_LABELS = {"monthly": "每月", "yearly": "每年"}


# ---------------- 出入参模型 ----------------
# 卡 06 的范围只列了 tools/subscription.py / tools/card.py / tests/（该范围行早于 04b/05b 的
# 「模型归位 schemas」决议）。**严格守范围**：模型先放本文件，等 analyst 点头再机械搬到
# tools/schemas.py（与 05b 把会话 helper 归 _query_common 同理，已在交付说明里记了这条）。

class ListSubscriptionsReq(BaseModel):
    model_config = STRICT

    status: Literal["active", "cancelled", "paused"] = "active"


class SubscriptionItem(BaseModel):
    """规格 T10 的 item 字段（冻结为 5 个，不得增删）；僵尸标注走 facts。"""

    id: str
    merchant: str
    amount: int
    cycle: str
    next_charge_date: str


class ListSubscriptionsData(BaseModel):
    items: list[SubscriptionItem]


class CancelSubscriptionReq(BaseModel):
    model_config = STRICT

    sub_id: str = Field(min_length=1)
    confirm_ref: str = Field(min_length=1)


class CancelSubscriptionData(BaseModel):
    sub_id: str
    status: str
    effective_date: str


# ---------------- 确认凭证（confirm_ref）机制 ----------------

_CONFIRM_REFS: dict[str, dict] = {}
#: 幂等临界区：凭证校验 → 执行 → 写快照必须整体串行（同 token 并发调用绝不重复执行）。
_CONFIRM_LOCK = threading.Lock()


def _now() -> datetime:
    """取当前时间（单独抽出来是为了让测试能钉住「凭证过期 / 僵尸窗口」这类时间口径）。"""
    return datetime.now()


def issue_confirm_ref(action: str, target_id: str) -> str:
    """签发确认凭证（**工具层私有约定，不属于规格冻结的 17 个工具函数**）。

    编排层（卡 09/10）渲染确认卡、用户确认后调用本函数，把返回的 ref 交给 `cancel_subscription`
    / `manage_card`。凭证绑定（action + target_id + 当前用户 + 签发时刻），TTL `CONFIRM_TTL_SECONDS`，
    一次性消费。真·确认卡绑定留卡 09/10 —— 本层不假装已经过确认。
    """
    if not isinstance(action, str) or not action.strip():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "action 不能为空")
    if not isinstance(target_id, str) or not target_id.strip():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "target_id 不能为空")
    ref = f"cf_{uuid.uuid4().hex}"
    _CONFIRM_REFS[ref] = {"action": action, "target_id": target_id, "user_id": current_user_id(),
                          "session_id": current_session_id(), "trace_id": f"trace-{uuid.uuid4().hex[:12]}",
                          "created_at": _now(), "snapshot": None}
    return ref


def check_confirm_ref(confirm_ref: str, action: str, target_id: str) -> dict:
    """校验并取回凭证记录（不消费）。非法一律抛 `ToolError`；已执行过的记录里带 `snapshot`。

    错误码边界（reviewer 钉死）：**不存在 / 非本人 / 绑定不符 → `FORBIDDEN`**（越权或伪造）；
    **存在但超 TTL → `TOKEN_EXPIRED`**（良性超时，并摘除过期凭证）。
    """
    ref = _CONFIRM_REFS.get(confirm_ref)
    if ref is None:
        logger.warning("确认凭证不存在（疑似伪造）：ref=%s action=%s", confirm_ref[:16], action)
        raise ToolError(ErrorCode.FORBIDDEN, "确认凭证无效：不是本会话签发的凭证")
    if ref["user_id"] != current_user_id():
        raise ToolError(ErrorCode.FORBIDDEN, "该确认凭证不属于当前用户")
    if ref["action"] != action or ref["target_id"] != target_id:
        logger.warning("确认凭证绑定不符：ref=%s want=%s/%s got=%s/%s",
                       confirm_ref[:8], action, target_id, ref["action"], ref["target_id"])
        raise ToolError(ErrorCode.FORBIDDEN, "确认凭证与本次操作不匹配")
    if _now() - ref["created_at"] > timedelta(seconds=CONFIRM_TTL_SECONDS):
        _CONFIRM_REFS.pop(confirm_ref, None)
        raise ToolError(ErrorCode.TOKEN_EXPIRED, "确认凭证已过期，请重新确认")
    if ref["snapshot"] is not None:
        logger.info("确认凭证已执行过，返回既有结果（幂等）：ref=%s", confirm_ref[:8])
    return ref


def reject_audit(tool: str, intent: str, target_id: str, reason: str, tier: str) -> None:
    """越权/伪造被拒时留痕：`audit_log.result='rejected'`（reviewer 钉死；schema 的官方取值之一）。

    写审计失败**绝不允许**掩盖拒绝本身（凭据校验是安全边界，不是可用性边界）→ 异常只记日志。
    """
    try:
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent=intent, tool=tool,
                         params_json={"target_id": target_id, "reason": reason, "tier": tier,
                                      "decision": "forbidden", "result": "rejected"},
                         risk_level=tier, permission_tier=tier, result="rejected")
    except Exception:                                    # noqa: BLE001 —— 审计失败不能改变拒绝结论
        logger.exception("写 rejected 审计失败（已忽略）：tool=%s target=%s", tool, target_id)


def finish_confirm_ref(confirm_ref: str, data: dict, facts: dict, message: str) -> None:
    """执行成功后把结果快照写回凭证 —— 之后同 ref 重复调用返回同一结果（幂等）。"""
    record = _CONFIRM_REFS.get(confirm_ref)
    if record is not None:
        record["snapshot"] = {"data": data, "facts": facts, "message": message}


def confirmed_result(record: dict) -> ToolResult | None:
    """凭证已执行过 → 直接返回既有快照（幂等的唯一出口）；未执行 → `None`。"""
    snapshot = record.get("snapshot")
    if snapshot is None:
        return None
    return _ok(snapshot["data"], snapshot["facts"], snapshot["message"])


# ---------------- T10 list_subscriptions ----------------

def _months_ago(day: date, months: int) -> date:
    """N 个月前的**月初**（僵尸窗口起点；整月覆盖，避免"月中截断"造成的误判）。"""
    year, month = divmod(day.year * 12 + (day.month - 1) - months, 12)
    return date(year, month + 1, 1)


def _zombie_ids(subs: list[dict]) -> list[str]:
    """疑似僵尸订阅 id：窗口内没有对应使用记录的订阅（口径见文件头，锚点用 `AS_OF` 保证跨天可复现）。

    **兜底（卡 06b / 台账 RISK-3）**：窗口按月分窗取流水，单窗超 `MAX_LIMIT` 直接
    `TOO_MANY_ROWS` —— 否则会被 DAO 的 500 行上限**静默截断**，把有扣费记录的商户误判成僵尸。
    """
    floor = _months_ago(AS_OF, ZOMBIE_WINDOW_MONTHS)
    rows = _window_flows(floor)
    charged = {row["counterparty"] for row in rows}          # 扣费流水按商户名匹配
    fresh_txns = {row["id"] for row in rows}                 # 或 source_txn_id 指向的流水落在窗口内
    return [sub["id"] for sub in subs
            if sub["merchant"] not in charged and sub.get("source_txn_id") not in fresh_txns]


def _window_flows(floor: date) -> list[dict]:
    """取 `[floor, AS_OF]` 窗口内**当前用户账户**的流水：按月分窗 + 单窗上限兜底（见 `_zombie_ids`）。"""
    owned, rows = _owned_account_ids(), []
    for start, end in month_windows(floor, AS_OF):
        page = dao.list_txn(start, end, limit=MAX_LIMIT)
        if page["total_count"] > MAX_LIMIT:
            raise ToolError(ErrorCode.TOO_MANY_ROWS, "该窗口的使用记录过多，请缩小范围")
        rows.extend(row for row in page["items"] if row["account_id"] in owned)
    return rows


def list_subscriptions(status: str = "active") -> ToolResult:
    """T10 订阅列表（L0）。data: `items[{id,merchant,amount,cycle,next_charge_date}]`（字段冻结）。

    「疑似僵尸订阅」按卡 06 第 1 条自动标注：不在 data 里增字段（规格冻结 5 个），
    而是把 `zombie_ids` / `zombie_count` / `zombie_window_months` / `zombie_as_of` 放进 **facts**，
    回执据此点名。窗口锚点 = `data.seed.AS_OF`（数据集的"今天"）。
    """
    if (bad := _invalid(ListSubscriptionsReq, status=status)) is not None:
        return bad
    try:
        subs = dao.list_subscriptions(current_user_id(), status)
        zombies = _zombie_ids(subs)                 # 窗口数据超上限 → TOO_MANY_ROWS（卡 06b 兜底）
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    items = [{key: sub[key] for key in ("id", "merchant", "amount", "cycle", "next_charge_date")}
             for sub in subs]
    data = ListSubscriptionsData(items=[SubscriptionItem(**item) for item in items])
    facts = {"status": status, "subscription_count": len(items), "zombie_count": len(zombies),
             "zombie_ids": zombies, "zombie_window_months": ZOMBIE_WINDOW_MONTHS,
             "zombie_as_of": AS_OF.isoformat(),
             "items": [{**item, **_money_facts(item["amount"], "amount")} for item in items]}
    if not items:
        return _ok(data.model_dump(), facts, "当前没有符合条件的订阅。")
    # 卡 20：面向用户不说"僵尸订阅"这种行话（与 agent/templates.py 的模板逐字一致）
    n, w = facts["subscription_count"], ZOMBIE_WINDOW_MONTHS
    message = f"当前有 {n} 个订阅，最近 {w} 个月都有正常扣费记录。" if not facts["zombie_count"] else (
        f"当前有 {n} 个订阅；其中 {facts['zombie_count']} 个仍在「进行中」，但最近 {w} 个月没有任何扣费记录"
        f" —— 怀疑是您忘了取消的订阅，建议核对一下。")
    return _ok(data.model_dump(), facts, message)


# ---------------- T11 cancel_subscription ----------------

def cancel_subscription(sub_id: str, confirm_ref: str) -> ToolResult:
    """T11 取消订阅（L2 高危写）。data: `sub_id` / `status` / `effective_date`。

    顺序：参数 → 订阅存在 → 归属 → 凭证校验（不存在/过期 `TOKEN_EXPIRED`、非本人或绑定不符 `FORBIDDEN`）
    → 已执行则返回快照（**幂等**）→ 状态流转合法性（已取消 → `INVALID_STATE`）→ 事务内（翻转 + 审计）→ 写快照。
    """
    if (bad := _invalid(CancelSubscriptionReq, sub_id=sub_id, confirm_ref=confirm_ref)) is not None:
        return bad
    with _CONFIRM_LOCK:                                          # 校验 → 执行 → 写快照，整体串行
        return _cancel_locked(sub_id, confirm_ref)


def _cancel_locked(sub_id: str, confirm_ref: str) -> ToolResult:
    """`cancel_subscription` 的临界区主体；调用方必须已持有 `_CONFIRM_LOCK`。"""
    try:
        sub = dao.get_subscription(sub_id)
        if sub is None:
            return _fail(ErrorCode.NOT_FOUND, "找不到该订阅")
        require_owned("订阅", sub["user_id"], sub["id"], tool="_cancel_locked")
        record = check_confirm_ref(confirm_ref, CANCEL_ACTION, sub_id)
        if (cached := confirmed_result(record)) is not None:
            return cached
        if sub["status"] == "cancelled":
            return _fail(ErrorCode.INVALID_STATE, "该订阅已是取消状态，无需重复操作")
        effective = _now().date().isoformat()
        conn = dao.connection()
        with transaction(conn):                                  # 单一事务：翻转 + 审计（不嵌套 BEGIN）
            updated = dao.update_subscription(sub_id, status="cancelled")
            if updated is None:                                  # 防御性兜底，正常走不到
                raise ToolError(ErrorCode.NOT_FOUND, "订阅不存在")
            dao.insert_audit(record["trace_id"], record["session_id"], actor="agent",
                             intent="subscription_cancel", tool=CANCEL_ACTION,
                             params_json={"sub_id": sub_id, "merchant": sub["merchant"],
                                          "status_before": sub["status"]},
                             risk_level=CANCEL_TIER, permission_tier=CANCEL_TIER, result="success")
    except ToolError as exc:
        if exc.code == ErrorCode.FORBIDDEN:                  # 越权/伪造被拒 → 留痕（reviewer 钉死）
            reject_audit(CANCEL_ACTION, "subscription_cancel", sub_id, exc.message, CANCEL_TIER)
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    data = CancelSubscriptionData(sub_id=sub_id, status="cancelled", effective_date=effective)
    facts = {"sub_id": sub_id, "merchant": sub["merchant"], "cycle": sub["cycle"],
             "status": "cancelled", "status_before": sub["status"], "effective_date": effective,
             "tier": CANCEL_TIER, "requires_otp": True,
             **_money_facts(sub["amount"], "amount")}
    message = (f"已取消订阅 {sub['merchant']}（{facts['amount_yuan']} 元／{CYCLE_LABELS[sub['cycle']]}），"
               f"{effective} 起不再扣费。")
    finish_confirm_ref(confirm_ref, data.model_dump(), facts, message)
    return _ok(data.model_dump(), facts, message)
