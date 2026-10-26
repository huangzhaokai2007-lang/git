"""预约的**用户操作**（T32 立即执行 / T33 改时间）—— F16 追加需求，从 `_transfer_schedule.py`
拆出（原文件 263 行，塞不下且不破红线）。

**为什么不改 T21/T22 的签名**（铁律 4：契约冻结）：
- T22 `execute_scheduled` 是**系统触发**的到点结算器（「不对应任何用户意图」）；
- T21 是「落预约」。
用户说「现在就执行那笔预约」是新的一次用户意图 → 新工具 **T32**，它**复用 T22 的同一份结算
实现**（抢锁 / 重检硬约束 / 扣款 + 流水 + 审计一行不重写），只多写一条「用户提前执行」审计；
「把预约改到别的时间」→ **T33**，只改 `pending` 那一笔的执行时间。

| 工具 | 触发 | 做什么 | 扣款吗 |
|---|---|---|---|
| **T32** `run_scheduled_now(schedule_id)` | 用户 | 校验 pending → 走 T22 结算 | **扣** |
| **T33** `reschedule_scheduled(schedule_id, execute_at)` | 用户 | 仅 `pending` 可改时间（不动金额/收款人/档位） | 不扣 |
"""

from __future__ import annotations

import calendar
import datetime
import re
import uuid

from data import dao
from data.db import transaction
from tools._query_common import (
    ToolError, _fail, _money_facts, _ok, current_session_id, current_user_id,
)
from tools._scheduled_common import STATUS_PENDING, _owned
from tools._transfer_schedule import execute_scheduled
from tools.schemas import ErrorCode, ToolResult

#: 工具名（审计归属）
RUN_NOW_TOOL, MODIFY_TOOL = "run_scheduled_now", "reschedule_scheduled"
REPEAT_TOOL, NEXT_TOOL = "set_schedule_repeat", "schedule_next_occurrence"
#: 循环周期（人类口径：每月 = monthly；每周 = weekly）
REPEAT_CYCLES = ("monthly", "weekly")

#: 预约时间接受 `YYYY-MM-DDTHH:MM` / `YYYY-MM-DD HH:MM` / `YYYY-MM-DD`
_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?$")


def _normalize_time(value: object) -> str | None:
    """把预约时间归一成 ISO8601（缺时间按 09:00）；认不出 → None（不猜）。"""
    text = str(value or "").strip()
    match = _TIME_RE.match(text)
    if match is None:
        return None
    date_part, clock = match.group(1), match.group(2) or "09:00"
    try:
        moment = datetime.datetime.fromisoformat(f"{date_part}T{clock}")
    except ValueError:
        return None
    return moment.isoformat(timespec="minutes")


def run_scheduled_now(schedule_id: str) -> ToolResult:
    """T32 立即执行一条**待执行**预约（用户主动提前）。`data` = `{schedule_id, status}`。

    结算**复用 T22 `execute_scheduled`**（同一份实现，不重写）：T22 只要求 `status='pending'`、
    不校验时间，故提前执行天然可用。成功后额外写一条「用户提前执行」审计，便于与系统到点结算区分。
    """
    try:
        row = _owned(schedule_id, tool=RUN_NOW_TOOL)
    except Exception as exc:                                    # noqa: BLE001 —— ToolError 已带码
        return _fail(getattr(exc, "code", ErrorCode.NOT_FOUND), getattr(exc, "message", str(exc)))
    if row["status"] != STATUS_PENDING:
        return _fail(ErrorCode.INVALID_STATE,
                     f"这笔预约已是 {row['status']} 状态，不能再立即执行")
    result = execute_scheduled(schedule_id)                     # T22：抢锁 → 重检 → 扣款 + 流水 + 审计
    if not result.ok:
        return result
    dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                     intent="scheduled_run", tool=RUN_NOW_TOOL,
                     params_json={"schedule_id": schedule_id},
                     risk_level=row["tier"], permission_tier=row["tier"],
                     result="success", error_code=None)
    return _ok({"schedule_id": schedule_id, "status": "success"},
               {**(result.facts or {}), "schedule_id": schedule_id},
               f"已按您的要求**立即执行**这笔预约，{result.message}")


def reschedule_scheduled(schedule_id: str, execute_at: str) -> ToolResult:
    """T33 改预约的执行时间（**只改时间**：金额/收款人/档位一个字不动）。

    `data` = `{schedule_id, execute_at}`；只允许 `pending`；新时间必须是 ISO 且**晚于现在**
    （改到过去等于"立即执行"，请用 T32 —— 两条意图不混）。
    """
    when = _normalize_time(execute_at)
    if when is None:
        return _fail(ErrorCode.INVALID_ARGUMENT, "预约时间看不懂，请用「2026-11-01 09:00」这样的格式")
    if when <= datetime.datetime.now().isoformat(timespec="minutes"):
        return _fail(ErrorCode.INVALID_ARGUMENT, "新时间必须晚于现在；想马上转请说「立即执行这笔预约」")
    try:
        row = _owned(schedule_id, tool=MODIFY_TOOL)
    except Exception as exc:                                    # noqa: BLE001
        return _fail(getattr(exc, "code", ErrorCode.NOT_FOUND), getattr(exc, "message", str(exc)))
    if row["status"] != STATUS_PENDING:
        return _fail(ErrorCode.INVALID_STATE, f"这笔预约已是 {row['status']} 状态，不能改时间")
    old_time = str(row["execute_at"])
    conn = dao.connection()
    with transaction(conn):
        changed = conn.execute("UPDATE scheduled_transfer SET execute_at = ?"
                               " WHERE id = ? AND status = ?",
                               (when, schedule_id, STATUS_PENDING)).rowcount
        if not changed:                                         # 并发：已被抢先结算/取消
            return _fail(ErrorCode.INVALID_STATE, "预约状态刚刚变了，请刷新后再试")
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="scheduled_modify", tool=MODIFY_TOOL,
                         params_json={"schedule_id": schedule_id,
                                      "from": old_time, "to": when},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    facts = {"schedule_id": schedule_id,
             "payee_name": (dao.get_payee(row["payee_id"]) or {}).get("name") or "",
             "execute_at": when, "old_execute_at": old_time,
             **_money_facts(row["amount"], "amount")}
    return _ok({"schedule_id": schedule_id, "execute_at": when}, facts,
               f"已把预约时间从 {old_time.replace('T', ' ')} 改到 {when.replace('T', ' ')}")


# ---------------- T34/T35：循环预约（设周期 / 续下一期） ----------------

def _next_occurrence(execute_at: str, cycle: str) -> str:
    """下一期的时间：weekly = +7 天；monthly = 下月同日（当月没有则取月末）。

    **月末对齐**（2026-10-26 修）：源日期若正好是它所在月的**最后一天**，下一期也取
    「下个月的最后一天」—— 否则会漂移：10-31 → 11-30 → **12-30**（应为 12-31），
    用户「每个月末」的语义就丢了。
    """
    moment = datetime.datetime.fromisoformat(execute_at)
    if cycle == "weekly":
        return (moment + datetime.timedelta(days=7)).isoformat(timespec="minutes")
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    last_day = calendar.monthrange(year, month)[1]
    is_month_end = moment.day == calendar.monthrange(moment.year, moment.month)[1]
    day = last_day if is_month_end else min(moment.day, last_day)
    return moment.replace(year=year, month=month, day=day).isoformat(timespec="minutes")


def set_schedule_repeat(schedule_id: str, cycle: str, until: str = "") -> ToolResult:
    """T34 给**待执行**预约记循环周期（`monthly` / `weekly`；空串 = 取消循环）。L1，不扣款。

    只改周期字段；`execute_at` / 金额 / 收款人 / 档位一个字不动（第一次执行前的时间就是首期）。
    """
    value = str(cycle or "").strip()
    end = str(until or "").strip()[:10]                      # 结束日（可空 = 一直循环）
    if end:
        try:
            end = datetime.date.fromisoformat(end).isoformat()
        except ValueError:
            return _fail(ErrorCode.INVALID_ARGUMENT, "循环结束日看不懂，请用 2026-12-31 这样的日期")
    if value and value not in REPEAT_CYCLES:
        return _fail(ErrorCode.INVALID_ARGUMENT, "循环周期只支持「每个月」或「每周」")
    try:
        row = _owned(schedule_id, tool=REPEAT_TOOL)
    except Exception as exc:                                    # noqa: BLE001
        return _fail(getattr(exc, "code", ErrorCode.NOT_FOUND), getattr(exc, "message", str(exc)))
    if row["status"] != STATUS_PENDING:
        return _fail(ErrorCode.INVALID_STATE, f"这笔预约已是 {row['status']} 状态，不能改循环设置")
    conn = dao.connection()
    with transaction(conn):
        conn.execute("UPDATE scheduled_transfer SET repeat_cycle = ?, repeat_until = ? WHERE id = ?",
                     (value or None, end or None, schedule_id))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="transfer_scheduled", tool=REPEAT_TOOL,
                         params_json={"schedule_id": schedule_id, "cycle": value,
                                      "until": end or None},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    label = {"monthly": "每月", "weekly": "每周"}.get(value, "不循环")
    suffix = f"（循环至 {end}）" if end else ""
    return _ok({"schedule_id": schedule_id, "repeat_cycle": value, "repeat_until": end or None},
               {"schedule_id": schedule_id, "repeat_label": label},
               f"已设为{label}循环{suffix}")


def schedule_next_occurrence(schedule_id: str) -> ToolResult:
    """T35 **系统触发**：为已成功结算的循环预约生成**下一期** `pending`（同收款人/金额/档位）。

    非循环 / 上一期未成功（失败或取消）→ **幂等无操作**（不报错，返回说明）。
    幂等：同一 (收款人, 金额, 下一期时间) 已有 pending → 直接返回那一条，不重复插。
    """
    try:
        row = _owned(schedule_id, tool=NEXT_TOOL)
    except Exception as exc:                                    # noqa: BLE001
        return _fail(getattr(exc, "code", ErrorCode.NOT_FOUND), getattr(exc, "message", str(exc)))
    cycle = str(row.get("repeat_cycle") or "")
    if not cycle:
        return _ok({"schedule_id": schedule_id, "next_id": None}, {}, "非循环预约，无需续期")
    if row["status"] != "success":
        return _ok({"schedule_id": schedule_id, "next_id": None}, {},
                   "上一期未成功，按口径不再续期")
    when = _next_occurrence(str(row["execute_at"]), cycle)
    end = str(row.get("repeat_until") or "")
    if end and when[:10] > end:                               # 越过结束日 → 不再续期（人类规格）
        return _ok({"schedule_id": schedule_id, "next_id": None}, {},
                   f"已到循环结束日 {end}，不再续期")
    conn = dao.connection()
    existing = conn.execute(
        "SELECT id FROM scheduled_transfer WHERE user_id = ? AND payee_id = ? AND amount = ?"
        " AND execute_at = ? AND status = 'pending'",
        (row["user_id"], row["payee_id"], row["amount"], when)).fetchone()
    if existing is not None:                                    # 幂等：已续过
        return _ok({"schedule_id": schedule_id, "next_id": existing["id"]}, {},
                   f"下一期已存在（{when.replace('T', ' ')}）")
    next_id = f"sch_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now().isoformat(timespec="minutes")
    with transaction(conn):
        #: 2026-10-26 修：**必须带上 `repeat_until`** —— 之前漏了，续出来的下一期结束日为空，
        #: 用户说的「循环到年底」在第一次执行后就失效（实测：12-31 之后还会继续排）。
        conn.execute(
            "INSERT INTO scheduled_transfer (id, user_id, payee_id, amount, execute_at, status,"
            " tier, token, created_at, repeat_cycle, repeat_until)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (next_id, row["user_id"], row["payee_id"], row["amount"], when,
             row["tier"], None, now, cycle, (end or None)))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="transfer_scheduled", tool=NEXT_TOOL,
                         params_json={"from": schedule_id, "next": next_id, "execute_at": when},
                         risk_level=row["tier"], permission_tier=row["tier"],
                         result="success", error_code=None)
    return _ok({"schedule_id": schedule_id, "next_id": next_id},
               {"schedule_id": next_id, "execute_at": when},
               f"下一期已排到 {when.replace('T', ' ')}")


# ---------------- T51 改金额 / 收款人（2026-10-24 人类规格「预约管理卡：修改内容」） ----------------

UPDATE_TOOL = "update_scheduled"


def update_scheduled(schedule_id: str, amount: int | None = None,
                     payee_id: str | None = None) -> ToolResult:
    """T51 改预约的**金额**和/或**收款人**（只对 `pending`；时间改期走 T33）。

    L1：不动钱，只改预约内容；`tier` **保持预约落库时冻结的值**（Q5：到期不重定档）。
    金额必须为正；收款人必须属于当前用户。`data` = `{schedule_id, amount, payee_id}`。
    """
    if amount is None and payee_id is None:
        return _fail(ErrorCode.INVALID_ARGUMENT, "请说明要改金额还是改收款人")
    if amount is not None and int(amount) <= 0:
        return _fail(ErrorCode.INVALID_ARGUMENT, "预约金额必须大于 0")
    try:
        row = _owned(schedule_id, tool=UPDATE_TOOL)
        if row["status"] != STATUS_PENDING:
            return _fail(ErrorCode.INVALID_STATE, f"这笔预约已是 {row['status']} 状态，不能修改")
        payee_name = None
        if payee_id is not None:
            payee = dao.get_payee(str(payee_id))
            if payee is None or payee["user_id"] != current_user_id():
                return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
            if payee["blacklist"]:
                return _fail(ErrorCode.FORBIDDEN, f"{payee['name']} 已在黑名单，不能作为收款人")
            payee_name = payee["name"]
        conn = dao.connection()
        with transaction(conn):
            if amount is not None:
                conn.execute("UPDATE scheduled_transfer SET amount = ? WHERE id = ?",
                             (int(amount), schedule_id))
            if payee_id is not None:
                conn.execute("UPDATE scheduled_transfer SET payee_id = ? WHERE id = ?",
                             (str(payee_id), schedule_id))
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="scheduled_modify", tool=UPDATE_TOOL,
                             params_json={"schedule_id": schedule_id, "amount": amount,
                                          "payee_id": payee_id},
                             risk_level="L1", permission_tier="L1", result="success",
                             error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _fail(ErrorCode.INTERNAL, str(exc))
    facts = {"schedule_id": schedule_id}
    if amount is not None:
        facts.update(_money_facts(int(amount), "amount"))
    if payee_name:
        facts["payee_name"] = payee_name
    label = []
    if amount is not None:
        label.append(f"金额改成 {int(amount) / 100:.2f} 元")
    if payee_name:
        label.append(f"收款人改成 {payee_name}")
    return _ok({"schedule_id": schedule_id, "amount": amount, "payee_id": payee_id}, facts,
               "已把预约的" + "、".join(label))
