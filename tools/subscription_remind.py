"""F18 订阅到期提醒（T53，L0 只读）—— 用户需求「在会员下次扣费前提醒我」。

**形态是查询展示，不是主动推送**：本项目没有后台推送通道（无 cron / MQ / 长连接），
每轮 `handle` 惰性注入会变成噪音。故本工具只在用户问「有什么要扣费」时被调用。
不新建表、不做已提醒去重 —— 没有推送就没有去重需求。

口径（与 `docs/features/F18-subscription-reminder.md` 逐条一致）：
- **窗口是闭区间** `[AS_OF, AS_OF + within_days]`：今天要扣的（`days_left=0`）必须算进去；
  **早于 AS_OF 的不算**（否则过期的同一笔会天天被提醒）
- 只查 `status='active'`（已取消 / 已暂停不提醒）
- 归属 fail-closed：只查当前 user
- `within_days` 严格 1–30 整数（字符串 `"3"` 直接拒）
- 空结果 `ok=True`，不是错误
- 日期锚点用 `data.seed.AS_OF`，**不用 `datetime.now()`**（否则演示当天会算出负数天）

**为什么单独一个文件**：`tools/subscription.py` 已 337 行（红线 300），装不下。
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from data import dao
from data.seed import AS_OF
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money_facts, _ok, current_user_id,
)
from tools.schemas import ErrorCode, ToolResult

TOOL = "list_due_subscriptions"
INTENT = "subscription_remind"
TIER = "L0"

DEFAULT_WITHIN_DAYS = 7                    # 来源：F18 口径决定「默认 7 天」——太早记不住、太晚来不及取消
MIN_DAYS, MAX_DAYS = 1, 30          # 来源：T53 契约「within_days: 1–30」


class ListDueSubscriptionsReq(BaseModel):
    """入参校验：`strict=True` 让字符串 `"3"` 直接失败（T53 要求严格整数）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    within_days: int = DEFAULT_WITHIN_DAYS


def days_cn(days: int) -> str:
    """把「距今天数」说成人话。编排层不算天数（铁律 1），这里算好塞进 facts。"""
    if days == 0:
        return "今天"
    if days == 1:
        return "明天"
    return f"{days} 天后"


def list_due_subscriptions(within_days: int = DEFAULT_WITHIN_DAYS) -> ToolResult:
    """T53：未来 `within_days` 天内要扣费的订阅（闭区间，含今天）。

    `data` = `items[{subscription_id,merchant,cycle,next_charge_date,days_left,amount}]`
    + `due_count` / `within_days` / `total_amount`（金额单位为分，与 T10 一致）。
    """
    if (bad := _invalid(ListDueSubscriptionsReq, within_days=within_days)) is not None:
        return bad
    if not MIN_DAYS <= within_days <= MAX_DAYS:
        return _fail(ErrorCode.INVALID_ARGUMENT,
                     f"within_days 必须是 {MIN_DAYS}–{MAX_DAYS} 的整数")
    try:
        subs = dao.list_subscriptions(current_user_id(), "active")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)

    end = AS_OF + timedelta(days=within_days)
    items = []
    for sub in subs:
        day = date.fromisoformat(sub["next_charge_date"])
        if not (AS_OF <= day <= end):                 # 闭区间；早于 AS_OF 的过期项不算
            continue
        items.append({"subscription_id": sub["id"], "merchant": sub["merchant"],
                      "cycle": sub["cycle"], "next_charge_date": sub["next_charge_date"],
                      "days_left": (day - AS_OF).days, "amount": sub["amount"]})
    items.sort(key=lambda item: (item["days_left"], item["merchant"]))

    total = sum(item["amount"] for item in items)
    facts = {"items": [{**item, "days_cn": days_cn(item["days_left"]),
                        **_money_facts(item["amount"], "amount")} for item in items],
             "due_count": len(items), "within_days": within_days,
             **_money_facts(total, "total_amount")}
    data = {"items": items, "due_count": len(items), "within_days": within_days,
            "total_amount": total}
    if not items:
        return _ok(data, facts, f"未来 {within_days} 天内没有要扣费的订阅。")
    return _ok(data, facts, f"未来 {within_days} 天内有 {len(items)} 个订阅要扣费，"
                            f"合计 {facts['total_amount_yuan']} 元。")
