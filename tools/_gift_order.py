"""送礼**订单**（T37 查 / T38 落）—— F20 追加：给用户一个可查的订单号与状态。

**为什么单开一个模块**：`tools/cross_scene.py` 已 224 行、`tools/transfer.py` 300 行顶格，
按仓内先例（`_aa_split` / `_transfer_schedule_ops`）单开。

| 工具 | 触发 | 做什么 |
|---|---|---|
| **T37** `list_gift_orders(status=None)` | 用户（L0 只读） | 列出当前用户的送礼订单（按创建时间倒序） |
| **T38** `record_gift_order(...)` | **编排层**（T16 锁款成功后） | 落一条 `created` 订单，返回订单号 |

状态机（本卡只到 `created`；`paid`/`cancelled` 的流转属下一张卡）：
`created`（已下单、资金已锁）→ `paid`（已支付/发货）/ `cancelled` / `failed`。
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid

from pydantic import BaseModel, Field

from data import dao
from data.db import transaction
from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _invalid, _ok, current_session_id, current_user_id,
    require_owned,
)
from tools.schemas import ErrorCode, ToolResult

logger = logging.getLogger(__name__)


class GiftOrderItem(BaseModel):
    """订单里的一个礼物（快照，来自 T16 事实包）。"""

    item_id: str
    name: str = ""
    price: int = Field(ge=0)


class RecordGiftOrderReq(BaseModel):
    """T38 入参（F20 追加）。"""

    plan_id: str
    lock_id: str
    contact: str
    gift_date: str
    budget: int = Field(ge=0)
    total: int = Field(ge=0)
    items: list[GiftOrderItem]

LIST_TOOL, RECORD_TOOL = "list_gift_orders", "record_gift_order"
#: 订单状态（与 `schema.sql` 的注释一致）
GIFT_ORDER_STATUSES = ("created", "paid", "shipped", "completed", "cancelled", "failed")
#: 状态中文（纯文案；未知值原样透传）
#: 2026-10-24：按人类规格改 5 状态词表（待支付 / 已支付 / 已发货 / 已完成 / 已取消）
STATUS_CN = {"created": "待支付", "paid": "已支付", "shipped": "已发货",
             "completed": "已完成", "cancelled": "已取消", "failed": "失败"}


def record_gift_order(plan_id: str, lock_id: str, contact: str, gift_date: str,
                      budget: int, total: int, items: list[dict]) -> ToolResult:
    """T38 落一条送礼订单（**T16 锁款成功后**由编排层调）。`data` = `{order_id, status, total}`。

    只落**快照**（清单/预算/合计），不重复锁款、不改余额 —— 钱在 T16 已锁。
    """
    if (bad := _invalid(RecordGiftOrderReq, plan_id=plan_id, lock_id=lock_id, contact=contact,
                        gift_date=gift_date, budget=budget, total=total, items=items)) is not None:
        return bad
    user_id = current_user_id()
    try:
        require_owned("该用户的送礼订单", DEMO_USER_ID, user_id,
                      tool=RECORD_TOOL, intent="gift_plan")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    order_id = f"ord_{uuid.uuid4().hex[:12]}"
    payload = json.dumps([{"item_id": str(i.get("item_id") or ""), "name": str(i.get("name") or ""),
                           "price": int(i.get("price") or 0)} for i in items], ensure_ascii=False)
    conn = dao.connection()
    with transaction(conn):
        conn.execute(
            "INSERT INTO gift_order (id, user_id, contact, gift_date, budget, total, items_json,"
            " status, plan_id, lock_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)",
            (order_id, user_id, contact, gift_date, int(budget), int(total), payload,
             plan_id, lock_id, datetime.datetime.now().isoformat(timespec="seconds")))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="gift_plan", tool=RECORD_TOOL,
                         params_json={"order_id": order_id, "contact": contact, "total": int(total)},
                         risk_level="L2", permission_tier="L2", result="success", error_code=None)
    return _ok({"order_id": order_id, "status": "created", "total": int(total)},
               {"order_id": order_id, "status_cn": STATUS_CN["created"]}, f"订单号 {order_id}")


def list_gift_orders(status: str | None = None) -> ToolResult:
    """T37 列出当前用户的送礼订单（L0 只读）。`data` = `{items, total_count}`。

    `status` 省略 = 全部；认不出的取值 → `INVALID_ARGUMENT`（不静默忽略）。
    """
    if status is not None and status not in GIFT_ORDER_STATUSES:
        return _fail(ErrorCode.INVALID_ARGUMENT, "订单状态取值不合法")
    user_id = current_user_id()
    try:
        require_owned("该用户的送礼订单", DEMO_USER_ID, user_id,
                      tool=LIST_TOOL, intent="gift_order_list")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    sql = "SELECT * FROM gift_order WHERE user_id = ?"
    params: tuple[object, ...] = (user_id,)
    if status is not None:
        sql += " AND status = ?"
        params = (user_id, status)
    rows = [dict(row) for row in dao.connection().execute(sql + " ORDER BY created_at DESC, id",
                                                          params)]
    items = [{"order_id": row["id"], "contact": row["contact"], "gift_date": row["gift_date"],
              "total": row["total"], "status": row["status"],
              "status_cn": STATUS_CN.get(row["status"], row["status"]),
              "amount_yuan": f"{row['total'] / 100:.2f}"} for row in rows]
    return _ok({"items": items, "total_count": len(items)},
               {"total_count": len(items), "items": items}, f"共 {len(items)} 笔送礼订单。")
