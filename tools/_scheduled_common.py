"""定时转账的**共享助手**（状态机 / 行读取 / 抢锁 / 事实包 / 参数校验）。

**为什么单开一个模块**（F16 Stage 2 拆卡）：`tools/_transfer_schedule.py` 加上 T24 后到
**331 行**，超出 `CLAUDE.md` 单文件 300 行红线。照仓内先例（`_transfer_token.py` /
`_transfer_risk.py` / `_aa_split.py`）把与四个工具**共用**的部分搬出来：

| 内容 | 谁用 |
|---|---|
| `SCHEDULE_STATUSES` 状态机 | T21–T24 + 编排层 + 测试 |
| `_row` / `_owned` | T22 / T23 |
| `_claim`（条件更新抢锁） | T22 / T23 |
| `_schedule_facts` | T22 / T23 |
| `_mark_failed`（Q6 不重试） | T22 |
| `schedule_args_error`（T7 参数校验） | `tools/transfer.py:preview_transfer` |

依赖单向：`_scheduled_common → {data.dao, tools.transfer}`，`_transfer_schedule → _scheduled_common`，
无回边。`transfer.py` 由**延迟 import** 取 `schedule_args_error`（避免与 `_transfer_schedule`
对本模块的依赖成环）。
"""

from __future__ import annotations

import datetime
import logging

from data import dao
from data.db import transaction
from tools import transfer
from tools._query_common import ToolError, _money_facts, require_owned
from tools.schemas import ErrorCode

logger = logging.getLogger(__name__)

#: 预约状态机（DDL 的 `status` 取值域）。**只允许这四个值** —— 渲染与测试都按它比。
STATUS_PENDING, STATUS_SUCCESS = "pending", "success"
STATUS_FAILED, STATUS_CANCELLED = "failed", "cancelled"
SCHEDULE_STATUSES = (STATUS_PENDING, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED)

#: 审计里的 tool 名（与函数名一致）
SCHEDULE_TOOL, RUN_TOOL, CANCEL_TOOL = "schedule_transfer", "execute_scheduled", "cancel_scheduled"


def _row(schedule_id: str) -> dict | None:
    """按 id 取预约行（**不做归属判断** —— 越权判定在调用方 fail-closed）。"""
    row = dao.connection().execute("SELECT * FROM scheduled_transfer WHERE id = ?",
                                   (schedule_id,)).fetchone()
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
    sql = "UPDATE scheduled_transfer SET status = ?"
    params: list[object] = [status]
    if settled_at is not None:
        sql += ", settled_at = ?"
        params.append(settled_at)
    sql += " WHERE id = ? AND status = ?"
    params += [schedule_id, STATUS_PENDING]
    return dao.connection().execute(sql, tuple(params)).rowcount > 0


def _schedule_facts(row: dict) -> dict:
    """预约的事实包：金额整数分 + `*_yuan` 展示串 + 状态/时间（回执要引用的数字都在这）。"""
    return {"schedule_id": row["id"], "status": row["status"], "tier": row["tier"],
            "payee_id": row["payee_id"], "execute_at": row["execute_at"],
            **_money_facts(row["amount"], "amount")}


def _mark_failed(schedule_id: str, reason: str) -> None:
    """把 `pending` 置为 `failed` 并记原因（Q6：**不重试**）。单独的写入，不受结算事务回滚影响。"""
    conn = dao.connection()
    with transaction(conn):
        conn.execute("UPDATE scheduled_transfer SET status = ?, settled_at = ?, fail_reason = ?"
                     " WHERE id = ? AND status = ?",
                     (STATUS_FAILED, transfer._now().isoformat(timespec="seconds"), reason,
                      schedule_id, STATUS_PENDING))


def schedule_args_error(schedule: str | None, split_with: list[str] | None) -> str | None:
    """T7 的 `schedule` / `split_with` 参数校验（**F16 补的语义**）；返回错误文案或 `None`。

    放本模块而不是 `transfer.py`：后者已 296 行（红线 300）塞不下，而 `transfer.py`
    用**延迟 import** 调它，故不成环。`split_with`（拆分转账）仍属后续卡 —— 本卡只拒不做。
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
