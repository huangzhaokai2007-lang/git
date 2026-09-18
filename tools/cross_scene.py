"""工具层 T16（任务卡 07）：送礼计划（跨场景联动 —— 锁定资金 + mock 预订清单）。

依据 `docs/01-接口规格.md` §2 T16、§5 权限矩阵、`CLAUDE.md` 铁律 + 卡 07 第 4 条：
`plan_gift(contact, date, budget)` → 返回 `plan_id` / `lock_id` / `items[]` / `total`，其中：
- **锁定资金**：从储蓄账户的 `available` 扣减预算（**不动 `balance`** —— "锁定"不是"支出"），返回 `lock_id`；
- **mock 预订**：鲜花 / 蛋糕清单（`GIFT_CATALOG`），组合优先、预算不够则退化单件、单件也不够则拒；
- 金额整数分、单一事务、写 `audit_log`（intent=`gift_plan`，tier=L2）。

口径（规格未定义处，逐条进交付说明的「需要人类决定」）：
- **DDL 没有锁资金表**（也没有放款/解冻工具，规格未定）→ 本层用「`available` 扣减 + `audit_log` 记 `lock_id` +
  `TODO(07b)`」顶住；真正的锁表 + 解冻（超期自动释放）需补 DDL 与 DAO 原语。
- **价目表**：规格与 seed 都没给礼物价目 → `GIFT_CATALOG` 是 demo 常量（卡 07 第 4 条要求 mock 清单，
  价目本身无来源，已记待人类确认）。
- L2 需确认卡 + OTP，但规格 T16 签名（`contact,date,budget`）**没有** `confirm_ref` 参数 →
  确认与 OTP 落在编排层（同 T11/T15 的分工），本层在 facts 给 `requires_otp=True` / `tier=L2`。
- 时间锚 = `data.seed.AS_OF`；预订日期必须 ≥ AS_OF（不能给过去日期下单）→ 否则 `INVALID_ARGUMENT`。
"""

from __future__ import annotations

import datetime
import logging
import uuid

from pydantic import BaseModel, ConfigDict, Field

from data import dao
from data.db import transaction
from data.seed import AS_OF
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money_facts, _ok, current_session_id, current_user_id,
)
from tools.schemas import ErrorCode, ToolResult
from tools.subscription import reject_audit

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
GIFT_TIER = "L2"            # 来源：规格 §2 T16 权限档列 = L2（跨场景写操作）
GIFT_ACTION = "plan_gift"   # 审计 tool 名（与工具名一致）
#: 【mock 价目】来源：卡 07 第 4 条要求 mock 预订清单（鲜花/蛋糕）；**价目规格与 seed 均未提供**，
#: 故以下为 demo 常量，非规格数字 → 已记「需要人类决定」（金额整数分：199.00 元 / 299.00 元）。
GIFT_CATALOG = (
    {"item_id": "gift_flowers", "name": "鲜花花束", "price": 19_900},
    {"item_id": "gift_cake", "name": "生日蛋糕", "price": 29_900},
)

STRICT = ConfigDict(strict=True, extra="forbid")


# ---------------- 出入参模型（卡 07 范围不扩 schemas.py → 模型放本文件，07b 归位） ----------------

class PlanGiftReq(BaseModel):
    model_config = STRICT

    contact: str = Field(min_length=1)
    date: str = Field(min_length=10)          # ISO8601 日期（YYYY-MM-DD），格式在函数内校验
    budget: int = Field(gt=0)


class GiftItem(BaseModel):
    item_id: str
    name: str
    price: int


class PlanGiftData(BaseModel):
    plan_id: str
    lock_id: str
    items: list[GiftItem]
    total: int


# ---------------- 内部辅助 ----------------

def _pick_items(budget: int) -> list[dict]:
    """按预算挑礼物：组合（全部）优先；不够则退化为单价最高的一件；一件也买不起 → 拒。"""
    if budget >= sum(item["price"] for item in GIFT_CATALOG):
        return list(GIFT_CATALOG)
    affordable = [item for item in GIFT_CATALOG if item["price"] <= budget]
    if not affordable:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "预算不足以预订任何礼物")
    return [max(affordable, key=lambda item: item["price"])]


def _lock_funds(amount: int) -> dict:
    """锁定资金：`available` 扣减（**不动 balance**），返回锁后的账户行；余额不足 → `INSUFFICIENT_FUNDS`。

    TODO(07b)：DDL 补锁资金表 + DAO 补 `lock_funds/unlock_funds`，替掉这里的直查直改。
    """
    conn = dao.connection()
    row = conn.execute("SELECT * FROM account WHERE user_id = ? AND type = 'savings' ORDER BY id LIMIT 1",
                       (current_user_id(),)).fetchone()
    if row is None or row["user_id"] != current_user_id():
        raise ToolError(ErrorCode.FORBIDDEN, "储蓄账户不属于当前用户")
    if row["available"] < amount:
        raise ToolError(ErrorCode.INSUFFICIENT_FUNDS, "可用余额不足以锁定本次预算")
    conn.execute("UPDATE account SET available = available - ? WHERE id = ?", (amount, row["id"]))
    locked = dict(conn.execute("SELECT * FROM account WHERE id = ?", (row["id"],)).fetchone())
    logger.info("锁定资金：lock 账户=%s 金额=%s 锁后 available=%s", row["id"], amount, locked["available"])
    return locked


# ---------------- T16 plan_gift ----------------

def _wanted_date(date: str) -> None:
    """日期必须是 ISO8601 且不早于基准日；非法 → `ToolError(INVALID_ARGUMENT)`。"""
    try:
        wanted = datetime.date.fromisoformat(date)      # 参数名 `date` 是规格钉的，故用 datetime.date
    except ValueError:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "日期格式必须是 YYYY-MM-DD")
    if wanted < AS_OF:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "预订日期不能早于今天")


def _receipt(contact: str, date: str, budget: int, ids: dict, items: list[dict], total: int,
             locked: dict) -> ToolResult:
    """整理回执：data 只放规格冻结字段，facts 收全部数字（供幻觉校验）。"""
    data = PlanGiftData(plan_id=ids["plan_id"], lock_id=ids["lock_id"],
                        items=[GiftItem(**item) for item in items], total=total)
    facts = {"plan_id": ids["plan_id"], "lock_id": ids["lock_id"], "contact": contact, "date": date,
             "budget": budget, "tier": GIFT_TIER, "requires_otp": True, "item_count": len(items),
             "total": total, "available_after": locked["available"], "balance": locked["balance"],
             "items": list(items), **_money_facts(total, "total"),
             **_money_facts(locked["available"], "available_after"),
             **_money_facts(locked["balance"], "balance")}
    message = (f"已为 {contact} 的 {date} 计划锁定 {facts['total_yuan']} 元（锁 {ids['lock_id']}），"
               f"含 {len(items)} 件礼物；可用余额 {facts['available_after_yuan']} 元。")
    return _ok(data.model_dump(), facts, message)


def plan_gift(contact: str, date: str, budget: int) -> ToolResult:
    """T16 送礼计划（L2 跨场景写）。data: `plan_id` / `lock_id` / `items[]` / `total`。

    顺序：参数 → 日期合法且不早于基准日 → 按预算选礼物 → 单一事务内（锁资金 + 写审计）→ 回执。
    确认卡 / OTP 在编排层（规格 T16 签名无 `confirm_ref`，见文件头口径）。
    """
    if (bad := _invalid(PlanGiftReq, contact=contact, date=date, budget=budget)) is not None:
        return bad
    try:
        _wanted_date(date)
        items = _pick_items(budget)
        total = sum(item["price"] for item in items)
        ids = {"plan_id": f"plan_{uuid.uuid4().hex[:12]}", "lock_id": f"lock_{uuid.uuid4().hex[:12]}"}
        conn = dao.connection()
        with transaction(conn):                       # 单一事务：锁资金 + 写审计（不嵌套 BEGIN）
            locked = _lock_funds(total)
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="gift_plan", tool=GIFT_ACTION,
                             params_json={"plan_id": ids["plan_id"], "lock_id": ids["lock_id"],
                                          "contact": contact, "date": date, "budget": budget,
                                          "total": total, "tier": GIFT_TIER,
                                          "items": [item["item_id"] for item in items]},
                             risk_level=GIFT_TIER, permission_tier=GIFT_TIER, result="success")
    except ToolError as exc:
        if exc.code == ErrorCode.FORBIDDEN:           # 越权 → 留痕（reviewer 口径）
            reject_audit(GIFT_ACTION, "gift_plan", current_user_id(), exc.message, GIFT_TIER)
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    return _receipt(contact, date, budget, ids, items, total, locked)
