"""定时转账的三个工具（**T21 / T22 / T23**）—— F16（Q1–Q10 人类已批）。

**为什么单开一个模块**：`tools/transfer.py` 已 **296 行**（`CLAUDE.md` 单文件红线 300），装不下。
依据 `docs/superpowers/specs/2026-10-15-f16-scheduled-transfer-design.md`。

| 工具 | 谁触发 | 做什么 | 扣款吗 |
|---|---|---|---|
| **T21** `schedule_transfer(preview_token, otp)` | 用户（写路径 EXECUTE 步） | 消费预览凭证 → 落一条 `pending` 预约 | **不扣** |
| **T22** `execute_scheduled(schedule_id)` | **系统**（惰性结算器；不对应任何用户意图） | 抢锁 → 重检硬约束 → 扣款 + 流水 + 审计 | 扣 |
| **T23** `cancel_scheduled(schedule_id)` | 用户 | 仅 `pending` 可取消（Q7：**不做退款**） | 不扣 |

**Q5（最要紧的口径）**：`tier` 在 T21 落库时**冻结**，T22 **不重新定档**、只重检硬约束。
理由：到期时用户不在场，无法确认、更无法输 OTP；若重新定档，23:00 到期的预约会被判 **L3** →
要人工复核 → **预约永远无法完成**。

**Q6**：到期余额不足 → 置 `failed` + `fail_reason`，**不重试**（重试策略属另一张卡）。
**Q1**：执行触发是**惰性结算**（`agent/scheduled_runner.py` 在每轮入口结算到期项），本模块只提供原语。

**为什么直查 SQL**：`data/dao.py` 已正好 300 行，加不下本表的原语；仓内先例见
`tools/cross_scene.py:_savings_row`。**补 DAO 原语并拆 `dao.py` 属另一张卡**（已在 PR 登记）。

**幂等/并发**：T21 用预览 token 的 **CAS**（`_store_token(expect_json=...)`）保证同 token 只落一条；
T22/T23 用**条件更新抢锁**（`UPDATE … WHERE status='pending'`，受影响行 0 = 已被抢先）。
"""

from __future__ import annotations

import datetime
import logging
import uuid

from data import dao
from data.db import transaction
from tools import transfer
from tools._card_limit_transfer import restrict as _restrict_card_daily
from tools._query_common import (
    ToolError, _dao_reject, _fail, _money_facts, _ok, current_session_id, current_user_id,
    require_owned,
)
from tools._transfer_risk import SINGLE_TX_MAX_CENTS, _limits, _today_out_flows
from tools._transfer_token import _load_token_authoritative, _raw_snapshot, _store_token
from tools.schemas import ErrorCode, ToolResult

logger = logging.getLogger(__name__)

#: 预约状态机（DDL 的 `status` 取值域）。**只允许这四个值** —— 渲染与测试都按它比。
STATUS_PENDING, STATUS_SUCCESS = "pending", "success"
STATUS_FAILED, STATUS_CANCELLED = "failed", "cancelled"
SCHEDULE_STATUSES = (STATUS_PENDING, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED)

#: 审计里的 tool 名（与函数名一致）
SCHEDULE_TOOL, RUN_TOOL, CANCEL_TOOL = "schedule_transfer", "execute_scheduled", "cancel_scheduled"


def _row(schedule_id: str) -> dict | None:
    """按 id 取预约行（**不做归属判断** —— 越权判定在下面各函数里 fail-closed）。"""
    conn = dao.connection()
    row = conn.execute("SELECT * FROM scheduled_transfer WHERE id = ?", (schedule_id,)).fetchone()
    return None if row is None else dict(row)


def _owned(schedule_id: str, *, tool: str) -> dict:
    """取预约行并校验归属；不存在 → `NOT_FOUND`，他人的 → `FORBIDDEN`。"""
    row = _row(schedule_id)
    if row is None:
        raise ToolError(ErrorCode.NOT_FOUND, "找不到该预约")
    require_owned("预约", row["user_id"], row["id"], tool=tool, intent="transfer_scheduled")
    return row


def _claim(schedule_id: str, status: str, *, settled_at: str | None = None) -> bool:
    """**条件更新抢锁**：仅当仍是 `pending` 才改成 `status`。返回是否抢到（受影响行 > 0）。

    这是 T22/T23 的唯一并发防线 —— 受影响行 0 = 已被别的调用抢先，调用方据此幂等返回。
    """
    conn = dao.connection()
    if settled_at is None:
        cur = conn.execute("UPDATE scheduled_transfer SET status = ? WHERE id = ? AND status = ?",
                           (status, schedule_id, STATUS_PENDING))
    else:
        cur = conn.execute("UPDATE scheduled_transfer SET status = ?, settled_at = ?"
                           " WHERE id = ? AND status = ?",
                           (status, settled_at, schedule_id, STATUS_PENDING))
    return cur.rowcount > 0


def _schedule_facts(row: dict) -> dict:
    """预约的事实包：金额整数分 + `*_yuan` 展示串 + 状态/时间（回执要引用的数字都在这）。"""
    return {"schedule_id": row["id"], "status": row["status"], "tier": row["tier"],
            "payee_id": row["payee_id"], "execute_at": row["execute_at"],
            **_money_facts(row["amount"], "amount")}


def schedule_args_error(schedule: str | None, split_with: list[str] | None) -> str | None:
    """T7 的 `schedule` / `split_with` 参数校验（**F16 补的语义**）；返回错误文案或 `None`。

    **为什么放本模块而不是 `transfer.py`**：后者已 296 行（红线 300），塞不下；
    由 `preview_transfer` **延迟 import** 调用，避免与本模块对 `transfer` 的依赖成环。
    `split_with`（拆分转账）仍属后续卡 —— 本卡只拒不做。
    """
    if split_with is not None:
        logger.warning("本卡未实现拆分转账：split_with=%r", split_with)
        return "本卡还不支持拆分转账"
    if schedule is None:
        return None
    try:
        ok = datetime.datetime.fromisoformat(schedule) > transfer._now()
    except ValueError:
        ok = False
    return None if ok else "预约时间必须是晚于当前时间的 YYYY-MM-DDTHH:MM"


def _mark_failed(schedule_id: str, reason: str) -> None:
    """把 `pending` 置为 `failed` 并记原因（Q6：**不重试**）。单独的写入，不受结算事务回滚影响。"""
    conn = dao.connection()
    with transaction(conn):
        conn.execute("UPDATE scheduled_transfer SET status = ?, settled_at = ?, fail_reason = ?"
                     " WHERE id = ? AND status = ?",
                     (STATUS_FAILED, transfer._now().isoformat(timespec="seconds"), reason,
                      schedule_id, STATUS_PENDING))


# ---------------- T21 schedule_transfer（落预约 · 不扣款） ----------------

def schedule_transfer(preview_token: str, otp: str | None = None) -> ToolResult:
    """T21 落一条定时转账预约。**此刻不扣款** —— 「预约时间到达前不能按即时转账扣款」。

    闸门顺序与 T8 `execute_transfer` 一致（凭证不存在/非本人 → 幂等 → TTL → OTP → L3 拒绝），
    差别只在最后一步：不扣款，而是插一条 `pending` 预约并**用预览 token 的 CAS 翻转**。
    整个落库（预约行 + 审计 + token 翻转）在**一个事务**里 —— 要么都成，要么都不成。
    """
    token = _load_token_authoritative(preview_token)
    if token is None or token.get("user_id") != current_user_id():
        return _fail(ErrorCode.NOT_FOUND, "找不到该预览凭证，请重新发起转账")
    if token.get("state") == "scheduled" or (token.get("data") or {}).get("schedule_id"):
        return _ok(token["data"], token["facts"], token["message"])        # 幂等：同一份预约
    if token.get("state") != "preview":
        return _fail(ErrorCode.INVALID_STATE, "该凭证已使用，请重新发起转账")
    if transfer._now() - token["created_at"] > datetime.timedelta(seconds=transfer.PREVIEW_TTL_SECONDS):
        return _fail(ErrorCode.TOKEN_EXPIRED, "预览凭证已过期，请重新发起转账")
    if token["requires_otp"] and str(otp or "").strip() != transfer.OTP_CODE:
        return _fail(ErrorCode.FORBIDDEN, "验证码不正确")
    if token["tier"] == "L3":
        return _fail(ErrorCode.INVALID_STATE, "该笔需人工复核，不能落预约")
    execute_at = token.get("schedule")
    if not execute_at:
        return _fail(ErrorCode.INVALID_ARGUMENT, "缺少预约时间")
    schedule_id = f"sch_{uuid.uuid4().hex[:12]}"
    facts = {"schedule_id": schedule_id, "payee_id": token["payee_id"],
             "payee_name": token["payee_name"], "execute_at": execute_at,
             "status": STATUS_PENDING, "tier": token["tier"],
             **_money_facts(token["amount"], "amount")}
    data = {"schedule_id": schedule_id, "execute_at": execute_at}
    message = (f"已预约 {execute_at} 向 {token['payee_name']} 转账 {facts['amount_yuan']} 元，"
               f"到点之前不会扣款。")
    marked = {**token, "state": "scheduled", "data": data, "facts": facts, "message": message}
    expect = _raw_snapshot(preview_token)
    conn = dao.connection()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO scheduled_transfer (id, user_id, payee_id, amount, execute_at, status,"
                " tier, token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (schedule_id, current_user_id(), token["payee_id"], token["amount"], execute_at,
                 STATUS_PENDING, token["tier"], preview_token,
                 transfer._now().isoformat(timespec="seconds")))
            dao.insert_audit(token["trace_id"], current_session_id(), actor="agent",
                             intent="transfer_scheduled", tool=SCHEDULE_TOOL,
                             params_json={"schedule_id": schedule_id, "payee_id": token["payee_id"],
                                          "amount": token["amount"], "execute_at": execute_at,
                                          "tier": token["tier"]},
                             risk_level=token["tier"], permission_tier=token["tier"], result="success")
            if not _store_token(preview_token, marked, expect_json=expect):   # 真 CAS
                raise ToolError(ErrorCode.INVALID_STATE, "该凭证已被使用，请重新发起转账")
    except ToolError as exc:                                   # 事务已回滚：预约行不会残留
        winner = _load_token_authoritative(preview_token) or {}
        if (winner.get("data") or {}).get("schedule_id"):
            return _ok(winner["data"], winner["facts"], winner["message"])     # 幂等：返回赢家
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    return _ok(data, facts, message)


# ---------------- T22 execute_scheduled（系统触发 · 扣款） ----------------

def execute_scheduled(schedule_id: str) -> ToolResult:
    """T22 结算一条到期预约（**系统触发**；不对应任何用户意图）。

    **Q5：不重新定档** —— `tier` 用落库时冻结的值，不因夜间/高频等降级因子升档。
    只重检**硬约束**：收款人仍属于本人、单笔上限、今日剩余额度、余额充足。
    **Q6：失败不重试** —— 置 `failed` + `fail_reason`。
    并发：抢锁是 `UPDATE … WHERE status='pending'`，与扣款同处一个事务；受影响行 0 = 已被抢先。
    """
    try:
        row = _owned(schedule_id, tool=RUN_TOOL)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    if row["status"] != STATUS_PENDING:                        # 幂等：已结算过就返回既有状态
        return _ok(_schedule_facts(row), _schedule_facts(row), f"该预约已是 {row['status']} 状态")
    now = transfer._now()
    try:
        payee = dao.get_payee(row["payee_id"])
        if payee is None:
            _mark_failed(schedule_id, "PAYEE_NOT_FOUND")
            return _fail(ErrorCode.NOT_FOUND, "收款人已不存在，预约未执行")
        account = dao.get_balance("savings")
        if account is None or account["user_id"] != current_user_id():
            _mark_failed(schedule_id, "FORBIDDEN")
            return _fail(ErrorCode.FORBIDDEN, "储蓄账户不属于当前用户")
        limits = _restrict_card_daily(_limits(_today_out_flows(now)), account["id"])
        if row["amount"] > SINGLE_TX_MAX_CENTS or limits["daily_used"] + row["amount"] > limits["daily_max"]:
            _mark_failed(schedule_id, "OVER_LIMIT")
            return _fail(ErrorCode.OVER_LIMIT, "超过转账限额，预约未执行")
        if account["balance"] < row["amount"]:
            _mark_failed(schedule_id, "INSUFFICIENT_FUNDS")
            return _fail(ErrorCode.INSUFFICIENT_FUNDS, "储蓄账户余额不足，预约未执行")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    conn = dao.connection()
    try:
        with transaction(conn):                                # 抢锁 + 扣款 + 流水 + 审计同一事务
            claimed = conn.execute(
                "UPDATE scheduled_transfer SET status = ?, settled_at = ?"
                " WHERE id = ? AND status = ?",
                (STATUS_SUCCESS, now.isoformat(timespec="seconds"), schedule_id, STATUS_PENDING))
            if claimed.rowcount == 0:                          # 已被抢先 → 幂等返回既有状态
                raise ToolError(ErrorCode.INVALID_STATE, "该预约已被结算")
            updated = dao.update_account_balance(account["id"], -row["amount"])
            if updated is None:
                raise ToolError(ErrorCode.NOT_FOUND, "储蓄账户不存在")
            balance_after = updated["balance"]
            txn = dao.insert_txn(account["id"], now.isoformat(timespec="seconds"), -row["amount"],
                                 "out", balance_after, counterparty=payee["name"], category="转账",
                                 channel="预约转账", memo=f"预约转账给{payee['name']}")
            conn.execute("UPDATE scheduled_transfer SET txn_id = ? WHERE id = ?", (txn["id"], schedule_id))
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="system",
                             intent="transfer_scheduled", tool=RUN_TOOL,
                             params_json={"schedule_id": schedule_id, "payee_id": row["payee_id"],
                                          "amount": row["amount"], "execute_at": row["execute_at"],
                                          "tier": row["tier"], "txn_id": txn["id"]},
                             risk_level=row["tier"], permission_tier=row["tier"], result="success")
    except ToolError as exc:
        settled = _row(schedule_id) or row
        return _ok(_schedule_facts(settled), _schedule_facts(settled),
                   f"该预约已是 {settled['status']} 状态")
    except ValueError as exc:
        return _dao_reject(exc)
    settled = _row(schedule_id) or row
    facts = {**_schedule_facts(settled), "payee_name": payee["name"], "txn_id": txn["id"],
             "balance_after": balance_after, **_money_facts(balance_after, "balance_after")}
    message = (f"预约转账已执行：向 {payee['name']} 转账 {facts['amount_yuan']} 元，"
               f"账户余额 {facts['balance_after_yuan']} 元。")
    return _ok({"schedule_id": schedule_id, "txn_id": txn["id"], "amount": row["amount"]},
               facts, message)


# ---------------- T23 cancel_scheduled（用户取消 · 不退款） ----------------

def cancel_scheduled(schedule_id: str) -> ToolResult:
    """T23 取消一条**尚未执行**的预约。`pending` → `cancelled`。

    **Q7：不做「已扣款后退款」** —— 已 `success` 的预约调本函数返回 `INVALID_STATE`。
    **取消不动钱**：预约期从未扣款，故取消也不需要退钱（这正是 Q5「确认前不锁款」的收益）。
    幂等：已 `cancelled` 的再取消返回同一结果。
    """
    try:
        row = _owned(schedule_id, tool=CANCEL_TOOL)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    if row["status"] == STATUS_CANCELLED:
        return _ok(_schedule_facts(row), _schedule_facts(row), "该预约已取消")
    if row["status"] != STATUS_PENDING:
        return _fail(ErrorCode.INVALID_STATE, f"该预约已 {row['status']}，不能取消")
    conn = dao.connection()
    try:
        with transaction(conn):
            if not _claim(schedule_id, STATUS_CANCELLED,
                          settled_at=transfer._now().isoformat(timespec="seconds")):
                settled = _row(schedule_id) or row
                raise ToolError(ErrorCode.INVALID_STATE, f"该预约已 {settled['status']}，不能取消")
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="transfer_scheduled", tool=CANCEL_TOOL,
                             params_json={"schedule_id": schedule_id, "execute_at": row["execute_at"],
                                          "amount": row["amount"], "tier": row["tier"]},
                             risk_level=row["tier"], permission_tier=row["tier"], result="success")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    settled = _row(schedule_id) or row
    facts = _schedule_facts({**settled, "status": STATUS_CANCELLED})
    message = (f"已取消 {row['execute_at']} 向 {row['payee_id']} 转账 "
               f"{facts['amount_yuan']} 元的预约，未产生任何扣款。")
    return _ok({"schedule_id": schedule_id, "status": STATUS_CANCELLED}, facts, message)
