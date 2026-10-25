"""送礼订单的**状态流转**（T39 取消 / T40 支付）—— F20 追加。

| 工具 | 档位 | 做什么 | 资金动作 |
|---|---|---|---|
| **T39** `cancel_gift_order(order_id)` | **L1** | `created → cancelled`（只对 `created` 有效） | `available += total`（**解锁**，余额不变 —— T16 锁的是 available） |
| **T40** `pay_gift_order(order_id)` | **L2** | `created → paid`（只对 `created` 有效） | `balance -= total`（**真扣款**；`available` 在 T16 已扣过，不能重复扣） |

口径（与预约一致）：
- **只有 `created` 能流转**；已 `paid`/`cancelled`/`failed` → `INVALID_STATE`（幂等语义：重复取消不二次解锁）；
- 钱的计算全部来自订单行的 `total`（**不重算、不猜**）；
- 锁款/解锁沿用 `tools/cross_scene._lock_funds` 的同一套账户口径（`available` 与 `balance` 分开）。
"""

from __future__ import annotations

import logging
import uuid

from data import dao
from data.db import transaction
from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _money_facts, _ok, current_session_id, current_user_id,
    require_owned,
)
from tools.schemas import ErrorCode, ToolResult

logger = logging.getLogger(__name__)

CANCEL_TOOL, PAY_TOOL = "cancel_gift_order", "pay_gift_order"
#: 只能从 `created` 流转
_CANCELLABLE, _PAYABLE = "created", "created"


def _owned_order(order_id: str, *, tool: str) -> dict:
    """取本人订单（越权 fail-closed）；找不到 → `NOT_FOUND`。"""
    user_id = current_user_id()
    try:
        require_owned("该送礼订单", DEMO_USER_ID, user_id, tool=tool, intent="gift_order_cancel")
    except ToolError:
        raise
    row = dao.connection().execute("SELECT * FROM gift_order WHERE id = ?", (order_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise ToolError(ErrorCode.NOT_FOUND, "找不到这笔送礼订单")
    return dict(row)


def _settle(order_id: str, *, from_status: str, to_status: str, tool: str, intent: str,
            money_sql: str | None, tier: str) -> None:
    """状态流转 + 资金动作 + 审计（同一事务；条件更新抢锁，重复调用不会二次动钱）。"""
    conn = dao.connection()
    with transaction(conn):
        changed = conn.execute("UPDATE gift_order SET status = ? WHERE id = ? AND status = ?",
                               (to_status, order_id, from_status)).rowcount
        if not changed:                                    # 并发/重复：已被别人改过
            raise ToolError(ErrorCode.INVALID_STATE, "订单状态刚刚变了，请刷新后再试")
        if money_sql is not None:                          # None = 只改状态、不动钱（发货/完成）
            conn.execute(money_sql, (order_id,))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent=intent, tool=tool, params_json={"order_id": order_id},
                         risk_level=tier, permission_tier=tier, result="success", error_code=None)


#: 解锁：available 回补（余额不动）。子查询只按当前用户的储蓄账户定位，`DEMO_USER_ID` 是常量。
_RELEASE_SQL = (f"UPDATE account SET available = available + (SELECT total FROM gift_order"
                f" WHERE id = ?) WHERE user_id = '{DEMO_USER_ID}' AND type = 'savings'")
#: 真扣款：balance 减少（available 在 T16 已扣过，不重复）
_CHARGE_SQL = (f"UPDATE account SET balance = balance - (SELECT total FROM gift_order"
               f" WHERE id = ?) WHERE user_id = '{DEMO_USER_ID}' AND type = 'savings'")
#: 退款：已支付的订单取消 → 钱回到余额（与 `_CHARGE_SQL` 互为反操作）
_REFUND_SQL = (f"UPDATE account SET balance = balance + (SELECT total FROM gift_order"
               f" WHERE id = ?) WHERE user_id = '{DEMO_USER_ID}' AND type = 'savings'")


def _facts(row: dict) -> dict:
    """订单事实包（金额一律来自订单行的 `total`）。"""
    return {"order_id": row["id"], "contact": row["contact"], "gift_date": row["gift_date"],
            "status": row["status"], **_money_facts(row["total"], "total")}


def cancel_gift_order(order_id: str) -> ToolResult:
    """T39 取消送礼订单（L1）。人类规格：**未发货可取消，已发货不可取消**。

    - `created`（待支付）→ 取消 = **解锁**冻结金额（`available += total`）；
    - `paid`（已支付、未发货）→ 取消 = **退款**（`balance += total`）；
    - `shipped`/`completed` → `INVALID_STATE`（已发货不可取消）。
    """
    try:
        row = _owned_order(order_id, tool=CANCEL_TOOL)
        status = row["status"]
        if status not in ("created", "paid"):
            return _fail(ErrorCode.INVALID_STATE,
                         f"这笔订单已是 {status} 状态，不能取消（未发货才可取消）")
        money_sql = _RELEASE_SQL if status == "created" else _REFUND_SQL
        _settle(order_id, from_status=status, to_status="cancelled", tool=CANCEL_TOOL,
                intent="gift_order_cancel", money_sql=money_sql, tier="L1")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _fail(ErrorCode.INTERNAL, str(exc))
    facts = {**_facts(row), "released_yuan": f"{row['total'] / 100:.2f}"}
    action = "解锁" if status == "created" else "退款"
    return _ok({"order_id": order_id, "status": "cancelled"},
               {**facts, **_money_facts(row["total"], "released")},
               f"已取消订单 {order_id}，{action} {facts['released_yuan']} 元")


def pay_gift_order(order_id: str) -> ToolResult:
    """T40 支付送礼订单（L2）：`created → paid` 并**真扣款**（`balance -= total`）。"""
    try:
        row = _owned_order(order_id, tool=PAY_TOOL)
        if row["status"] != _PAYABLE:
            return _fail(ErrorCode.INVALID_STATE, f"这笔订单已是 {row['status']} 状态，不能支付")
        account = dao.get_balance("savings")
        if account is None or account["balance"] < row["total"]:
            return _fail(ErrorCode.INSUFFICIENT_FUNDS, "储蓄账户余额不足，无法支付这笔订单")
        _settle(order_id, from_status=_PAYABLE, to_status="paid", tool=PAY_TOOL,
                intent="gift_order_pay", money_sql=_CHARGE_SQL, tier="L2")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _fail(ErrorCode.INTERNAL, str(exc))
    facts = _facts(row)
    return _ok({"order_id": order_id, "status": "paid"}, facts,
               f"订单 {order_id} 已支付 {facts['total_yuan']} 元，礼物将按 {row['gift_date']} 送达")


# ---------------- T49 状态推进（发货 / 完成）（2026-10-24 人类规格 5 状态） ----------------

ADVANCE_TOOL = "advance_gift_order"
#: 动作 → (前置状态, 目标状态)
_ADVANCE = {"ship": ("paid", "shipped"), "complete": ("shipped", "completed")}


def advance_gift_order(order_id: str, action: str) -> ToolResult:
    """T49 推进送礼订单状态（L1：不动钱，只改状态 + 留痕）。

    `action=ship`：`paid → shipped`（已支付 → 已发货）；`action=complete`：`shipped → completed`。
    动作非法 → `INVALID_ARGUMENT`；前置状态不符 → `INVALID_STATE`（幂等语义：重复发货不二次生效）。
    """
    key = str(action or "").strip()
    if key not in _ADVANCE:
        return _fail(ErrorCode.INVALID_ARGUMENT, "只支持下两个动作：ship（发货）/ complete（完成）")
    from_status, to_status = _ADVANCE[key]
    try:
        row = _owned_order(order_id, tool=ADVANCE_TOOL)
        if row["status"] != from_status:
            return _fail(ErrorCode.INVALID_STATE,
                         f"这笔订单当前是 {row['status']} 状态，不能" +
                         ("发货（需已支付）" if key == "ship" else "标记完成（需已发货）"))
        _settle(order_id, from_status=from_status, to_status=to_status, tool=ADVANCE_TOOL,
                intent="gift_order_advance", money_sql=None, tier="L1")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _fail(ErrorCode.INTERNAL, str(exc))
    facts = _facts(row)
    label = "已发货" if key == "ship" else "已完成"
    return _ok({"order_id": order_id, "status": to_status}, facts,
               f"订单 {order_id} 已标记{label}")
