"""F18 订阅提醒（T53 `list_due_subscriptions`）单测。

时间锚点 = `data.seed.AS_OF`（2026-09-12，固定常量）。窗口是**闭区间**
`[AS_OF, AS_OF + within_days]`，故「今天扣」与「第 7 天扣」都必须命中，「第 8 天」必须不命中。

覆盖验收清单要求的四类：**日期边界 / 重复提醒（幂等只读）/ 取消订阅 / 用户隔离**。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from data.seed import AS_OF
from tools.subscription_remind import DEFAULT_WITHIN_DAYS, list_due_subscriptions
from tools.schemas import ErrorCode

from tests.conftest import FOREIGN_SUB, write_sql


def _set_charge(path: Path, sub_id: str, days: int, status: str = "active") -> None:
    """把某条订阅的下次扣费日改到 `AS_OF + days`（直接写库，构造边界）。"""
    charge = (AS_OF + timedelta(days=days)).isoformat()
    write_sql(path, [("UPDATE subscription SET next_charge_date = ?, status = ? WHERE id = ?",
                      (charge, status, sub_id))])


def test_window_is_closed_on_both_ends(seeded: Path) -> None:
    """边界：今天（0 天）与第 7 天都算命中；第 8 天不算。"""
    _set_charge(seeded, "sub_0001", 0)
    _set_charge(seeded, "sub_0002", DEFAULT_WITHIN_DAYS)
    _set_charge(seeded, "sub_0003", DEFAULT_WITHIN_DAYS + 1)
    _set_charge(seeded, "sub_0004", 999)                 # 挪远，排除干扰
    result = list_due_subscriptions()
    ids = {item["subscription_id"] for item in result.facts["items"]}
    assert ids == {"sub_0001", "sub_0002"}, f"闭区间两端应都命中，实际 {ids}"


def test_past_due_is_not_listed(seeded: Path) -> None:
    """已经过期（扣费日早于 AS_OF）的不算「将要扣费」——否则会天天提醒同一笔。"""
    _set_charge(seeded, "sub_0001", -1)
    _set_charge(seeded, "sub_0002", 999)
    _set_charge(seeded, "sub_0003", 999)
    _set_charge(seeded, "sub_0004", 999)
    result = list_due_subscriptions()
    assert result.facts["due_count"] == 0


def test_only_active_subscriptions_are_reminded(seeded: Path) -> None:
    """已取消的订阅不该被提醒（验收清单：取消订阅）。"""
    _set_charge(seeded, "sub_0001", 2)
    _set_charge(seeded, "sub_0002", 2, status="cancelled")
    _set_charge(seeded, "sub_0003", 999)
    _set_charge(seeded, "sub_0004", 999)
    result = list_due_subscriptions()
    assert {item["subscription_id"] for item in result.facts["items"]} == {"sub_0001"}


def test_foreign_user_subscriptions_are_never_listed(seeded: Path) -> None:
    """用户隔离：把他人订阅的扣费日也设进窗口，结果里不能出现。"""
    write_sql(seeded, [("UPDATE subscription SET next_charge_date = ? WHERE id = ?",
                        (AS_OF.isoformat(), FOREIGN_SUB))])
    result = list_due_subscriptions()
    assert FOREIGN_SUB not in {item["subscription_id"] for item in result.facts["items"]}


def test_within_days_is_validated(seeded: Path) -> None:
    """参数校验：0 / 31 / 字符串都拒绝（严格模式）。"""
    for bad in (0, 31, "7"):
        result = list_due_subscriptions(bad)
        assert not result.ok and result.error_code == ErrorCode.INVALID_ARGUMENT, bad


def test_empty_result_is_not_an_error(seeded: Path) -> None:
    """空结果 → ok=True + due_count=0（不是错误：只是近期没有要扣的）。"""
    for sub_id in ("sub_0001", "sub_0002", "sub_0003", "sub_0004"):
        _set_charge(seeded, sub_id, 999)
    result = list_due_subscriptions()
    assert result.ok and result.facts["due_count"] == 0
    assert "没有要扣费" in result.message


def test_calling_twice_is_idempotent(seeded: Path) -> None:
    """重复提醒：只读工具，连查两次结果完全一致、且不写任何表。"""
    first = list_due_subscriptions()
    second = list_due_subscriptions()
    assert first.facts == second.facts


def test_overdue_active_subscription_is_reported_separately(seeded: Path) -> None:
    """扣费日已过的 active 订阅不能静默丢掉 —— 单独报，且**不并入**「将要扣费」列表。"""
    _set_charge(seeded, "sub_0001", -3)
    for sub_id in ("sub_0002", "sub_0003", "sub_0004"):
        _set_charge(seeded, sub_id, 999)
    result = list_due_subscriptions()
    assert result.facts["due_count"] == 0 and result.data["items"] == []
    assert result.facts["overdue_count"] == 1
    item = result.facts["overdue_items"][0]
    assert item["subscription_id"] == "sub_0001" and item["days_overdue"] == 3
    assert item["overdue_cn"] == "已过 3 天"
    assert "已经过去" in result.message and "扣款失败" in result.message


def test_overdue_and_due_can_coexist(seeded: Path) -> None:
    """同时有「将要扣」和「已逾期」时，两段都要说清，不能互相覆盖。"""
    _set_charge(seeded, "sub_0001", -2)
    _set_charge(seeded, "sub_0002", 3)
    _set_charge(seeded, "sub_0003", 999)
    _set_charge(seeded, "sub_0004", 999)
    result = list_due_subscriptions()
    assert {i["subscription_id"] for i in result.facts["items"]} == {"sub_0002"}
    assert {i["subscription_id"] for i in result.facts["overdue_items"]} == {"sub_0001"}
    assert "1 个订阅要扣费" in result.message and "另有 1 个订阅" in result.message


def test_cancelled_overdue_is_not_reported(seeded: Path) -> None:
    """已取消的订阅即使扣费日过了也不算逾期 —— 它本来就不该再扣。"""
    _set_charge(seeded, "sub_0001", -5, status="cancelled")
    for sub_id in ("sub_0002", "sub_0003", "sub_0004"):
        _set_charge(seeded, sub_id, 999)
    result = list_due_subscriptions()
    assert result.facts["overdue_count"] == 0 and result.facts["overdue_items"] == []
