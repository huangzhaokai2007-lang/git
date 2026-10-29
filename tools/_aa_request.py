"""群收款的**请求生命周期**（T41 落 / T42 查 / T43 每人已付 / T44 撤销）—— F22 追加。

人类需求（v3 清单）：AA「进行中 / 每人已付 / 24h 失效 / 撤销」。
T9 / T31 只写一条审计（无表），本模块把 AA 变成**可跟踪的请求**：

| 工具 | 触发 | 做什么 |
|---|---|---|
| **T41** `open_aa_request(payee_ids, amounts, total, mode, title)` | 编排层（T9/T31 成功后） | 落 `active` 请求 + 每人一份 `aa_share`（`expire_at = 创建 + 24h`） |
| **T42** `list_aa_requests(status=None)` | 用户（L0 只读） | 列出请求 + 每人已付状态（`paid_count`/`total_count`/`expired`） |
| **T43** `mark_aa_share_paid(request_id, payee_id)` | 用户 | 把某人的份额标 `paid`（全部付清 → 请求 `done`） |
| **T44** `cancel_aa_request(request_id)` | 用户 | `active → cancelled`（撤销；已付份额不回滚） |

**24h 失效（惰性）**：`active` 且 `expire_at` 已过 → **读取时**判为 `expired`（`facts` 里带真实状态），
写操作遇到过期 → `INVALID_STATE`「已超过 24 小时」。**不在读路径上写库**（读不产生副作用）。
"""

from __future__ import annotations

import datetime
import logging
import uuid

from pydantic import BaseModel, Field

from data import dao
from data.db import transaction
from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _invalid, _money_facts, _ok, current_session_id,
    current_user_id, require_owned,
)
from tools.schemas import ErrorCode, ToolResult

logger = logging.getLogger(__name__)

OPEN_TOOL, LIST_TOOL = "open_aa_request", "list_aa_requests"
PAID_TOOL, CANCEL_TOOL = "mark_aa_share_paid", "cancel_aa_request"
#: 请求状态
STATUSES = ("active", "done", "expired", "cancelled")
#: 状态中文（纯文案）
STATUS_CN = {"active": "进行中", "done": "已完成", "expired": "已失效", "cancelled": "已撤销"}
#: 失效窗口（小时）—— 人类口径「24h 失效」
TTL_HOURS = 24


class OpenAaRequestReq(BaseModel):
    """T41 入参（编排层内部调用）。"""

    payee_ids: list[str]
    amounts: list[int] = Field(min_length=1)
    total: int = Field(ge=0)
    mode: str = "equal"
    title: str = "群收款"


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _effective(row: dict) -> str:
    """真实状态：`active` 且已过 `expire_at` → `expired`（**只算不写**）。"""
    if row["status"] != "active":
        return row["status"]
    try:
        deadline = datetime.datetime.fromisoformat(str(row["expire_at"]))
    except ValueError:
        return row["status"]
    return "expired" if _now() >= deadline else "active"


def _shares(request_id: str) -> list[dict]:
    rows = dao.connection().execute(
        "SELECT * FROM aa_share WHERE request_id = ? ORDER BY rowid", (request_id,)).fetchall()
    return [dict(row) for row in rows]


def open_aa_request(payee_ids: list[str], amounts: list[int], total: int, mode: str,
                    title: str) -> ToolResult:
    """T41 落一条 群收款请求 + 每人份额（`active`，24h 后惰性失效）。`data` = `{request_id, expire_at}`。"""
    if (bad := _invalid(OpenAaRequestReq, payee_ids=payee_ids, amounts=amounts,
                        total=total, mode=mode, title=title)) is not None:
        return bad
    if len(payee_ids) != len(amounts):
        return _fail(ErrorCode.INVALID_ARGUMENT, "每人金额的个数必须与收款人一致")
    user_id = current_user_id()
    try:
        require_owned("这些收款人", DEMO_USER_ID, user_id, tool=OPEN_TOOL, intent="aa_collect")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    names: list[str] = []
    for payee_id in payee_ids:
        row = dao.get_payee(payee_id)
        if row is None or row["user_id"] != user_id:
            return _fail(ErrorCode.NOT_FOUND, "找不到收款人")
        names.append(row["name"])
    now = _now()
    request_id = f"aa_{uuid.uuid4().hex[:12]}"
    expire_at = (now + datetime.timedelta(hours=TTL_HOURS)).isoformat(timespec="minutes")
    conn = dao.connection()
    with transaction(conn):
        conn.execute(
            "INSERT INTO aa_request (id, user_id, title, total, split_mode, status, created_at,"
            " expire_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            (request_id, user_id, title or "群收款", int(total), mode or "equal",
             now.isoformat(timespec="seconds"), expire_at))
        for payee_id, name, cents in zip(payee_ids, names, amounts):
            conn.execute(
                "INSERT INTO aa_share (id, request_id, payee_id, payee_name, amount, status)"
                " VALUES (?, ?, ?, ?, ?, 'pending')",
                (f"aas_{uuid.uuid4().hex[:12]}", request_id, payee_id, name, int(cents)))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="aa_collect", tool=OPEN_TOOL,
                         params_json={"request_id": request_id, "total": int(total),
                                      "payees": len(payee_ids)},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    return _ok({"request_id": request_id, "expire_at": expire_at},
               {"request_id": request_id, "expire_at": expire_at,
                "expire_label": expire_at.replace("T", " ")},
               f"群收款请求 {request_id} 已发起，24 小时内有效（{expire_at.replace('T', ' ')} 截止）")


def list_aa_requests(status: str | None = None) -> ToolResult:
    """T42 列出当前用户的 群收款请求（L0 只读）：含每人已付状态与**真实状态**（过期惰性判）。"""
    if status is not None and status not in STATUSES:
        return _fail(ErrorCode.INVALID_ARGUMENT, "AA 状态取值不合法")
    user_id = current_user_id()
    try:
        require_owned("该用户的 群收款请求", DEMO_USER_ID, user_id, tool=LIST_TOOL, intent="aa_list")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    rows = [dict(row) for row in dao.connection().execute(
        "SELECT * FROM aa_request WHERE user_id = ? ORDER BY created_at DESC, id", (user_id,))]
    items = []
    for row in rows:
        real = _effective(row)
        if status is not None and real != status:               # 按**真实状态**筛（不是库里那个旧值）
            continue
        shares = _shares(row["id"])
        paid = [s for s in shares if s["status"] == "paid"]
        items.append({"request_id": row["id"], "title": row["title"], "total": row["total"],
                      "total_yuan": f"{row['total'] / 100:.2f}", "split_mode": row["split_mode"],
                      "status": real, "status_cn": STATUS_CN[real],
                      "expire_at": row["expire_at"].replace("T", " "),
                      "paid_count": len(paid), "share_count": len(shares),
                      "shares": [{"payee_id": s["payee_id"], "payee_name": s["payee_name"],
                                  "amount_yuan": f"{s['amount'] / 100:.2f}",
                                  "status": s["status"],
                                  "status_cn": "已付" if s["status"] == "paid" else "待付"}
                                 for s in shares]})
    return _ok({"items": items, "total_count": len(items)},
               {"total_count": len(items), "items": items}, f"共 {len(items)} 笔 群收款。")


def _owned_request(request_id: str, *, tool: str) -> dict:
    """取本人请求（越权 fail-closed）；找不到 → NOT_FOUND。"""
    user_id = current_user_id()
    try:
        require_owned("该 群收款请求", DEMO_USER_ID, user_id, tool=tool, intent="aa_list")
    except ToolError:
        raise
    row = dao.connection().execute("SELECT * FROM aa_request WHERE id = ?", (request_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise ToolError(ErrorCode.NOT_FOUND, "找不到这笔 群收款请求")
    return dict(row)


def mark_aa_share_paid(request_id: str, payee_id: str) -> ToolResult:
    """T43 把某人的份额标 `paid`；全部付清 → 请求转 `done`。`data` = `{request_id, status}`。"""
    try:
        row = _owned_request(request_id, tool=PAID_TOOL)
        real = _effective(row)
        if real == "expired":
            return _fail(ErrorCode.INVALID_STATE, "这笔 AA 已超过 24 小时，不能登记付款")
        if real != "active":
            return _fail(ErrorCode.INVALID_STATE, f"这笔 AA 已是{STATUS_CN[real]}状态")
        share = dao.connection().execute(
            "SELECT * FROM aa_share WHERE request_id = ? AND payee_id = ?",
            (request_id, payee_id)).fetchone()
        if share is None:
            return _fail(ErrorCode.NOT_FOUND, "这笔 AA 里没有该收款人")
        if share["status"] == "paid":
            return _fail(ErrorCode.INVALID_STATE, f"{share['payee_name']} 已经登记过已付了")
        conn = dao.connection()
        with transaction(conn):
            conn.execute("UPDATE aa_share SET status = 'paid', paid_at = ? WHERE request_id = ?"
                         " AND payee_id = ? AND status = 'pending'",
                         (_now().isoformat(timespec="seconds"), request_id, payee_id))
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM aa_share WHERE request_id = ? AND status = 'pending'",
                (request_id,)).fetchone()["n"]
            status = "done" if pending == 0 else "active"
            conn.execute("UPDATE aa_request SET status = ? WHERE id = ?", (status, request_id))
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="aa_mark_paid", tool=PAID_TOOL,
                             params_json={"request_id": request_id, "payee_id": payee_id},
                             risk_level="L1", permission_tier="L1", result="success",
                             error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    label = "全部付清" if status == "done" else "还有未付的人"
    return _ok({"request_id": request_id, "status": status},
               {"request_id": request_id, "payee_name": share["payee_name"], "status_cn": label},
               f"已登记 {share['payee_name']} 已付；{label}")


def cancel_aa_request(request_id: str) -> ToolResult:
    """T44 撤销 群收款请求（`active → cancelled`；已登记的付款记录保留，不回滚）。"""
    try:
        row = _owned_request(request_id, tool=CANCEL_TOOL)
        real = _effective(row)
        if real != "active":
            return _fail(ErrorCode.INVALID_STATE, f"这笔 AA 已是{STATUS_CN[real]}状态，不能撤销")
        conn = dao.connection()
        with transaction(conn):
            changed = conn.execute(
                "UPDATE aa_request SET status = 'cancelled' WHERE id = ? AND status = 'active'",
                (request_id,)).rowcount
            if not changed:
                return _fail(ErrorCode.INVALID_STATE, "状态刚刚变了，请刷新后再试")
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="aa_cancel", tool=CANCEL_TOOL,
                             params_json={"request_id": request_id},
                             risk_level="L1", permission_tier="L1", result="success",
                             error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    shares = _shares(request_id)
    paid = [s for s in shares if s["status"] == "paid"]
    return _ok({"request_id": request_id, "status": "cancelled"},
               {"request_id": request_id, "paid_count": len(paid),
                **_money_facts(row["total"], "total")},
               f"群收款请求 {request_id} 已撤销（已登记的 {len(paid)} 人付款记录保留）")


# ---------------- T48 提醒未付（2026-10-24 人类规格「支持提醒未付人员」） ----------------

REMIND_TOOL = "remind_aa_share"


def remind_aa_share(request_id: str, payee_id: str) -> ToolResult:
    """T48 **提醒某人付款**（L1）。`data` = `{request_id, payee_id, reminded}`。

    demo 期没有真实的推送通道：只**写一条审计**（谁被提醒、对应哪笔 AA），
    回执里说清「已提醒（模拟）」——不伪造送达状态。
    """
    try:
        row = _owned_request(request_id, tool=REMIND_TOOL)
        real = _effective(row)
        if real == "expired":
            return _fail(ErrorCode.INVALID_STATE, "这笔 AA 已超过 24 小时，不用再提醒了")
        if real != "active":
            return _fail(ErrorCode.INVALID_STATE, f"这笔 AA 已是{STATUS_CN[real]}状态")
        share = dao.connection().execute(
            "SELECT * FROM aa_share WHERE request_id = ? AND payee_id = ?",
            (request_id, payee_id)).fetchone()
        if share is None:
            return _fail(ErrorCode.NOT_FOUND, "这笔 AA 里没有该收款人")
        if share["status"] == "paid":
            return _fail(ErrorCode.INVALID_STATE, f"{share['payee_name']} 已经付过了，无需提醒")
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="aa_remind", tool=REMIND_TOOL,
                         params_json={"request_id": request_id, "payee_id": payee_id},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    facts = {"request_id": request_id, "payee_name": share["payee_name"],
             "expire_at": str(row["expire_at"]).replace("T", " "),
             **_money_facts(share["amount"], "amount")}
    return _ok({"request_id": request_id, "payee_id": payee_id, "reminded": True}, facts,
               f"已提醒 {share['payee_name']} 支付 {facts['amount_yuan']} 元"
               f"（截止 {facts['expire_at']}；demo 期为模拟提醒，只留痕不真推送）")
