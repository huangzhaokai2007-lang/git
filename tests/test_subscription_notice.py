"""F18 提醒条的数据来源：`summary()` 只读概览，不去重、不写状态。

提醒的窗口口径（7 天闭区间、只查 active、逾期单列）在
`tests/test_subscription_remind.py` 里钉着；本文件只测「概览怎么摘」。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from agent import subscription_notice
from data.seed import AS_OF

from tests.conftest import write_sql

SESSION = "sess-f18-summary"


def _set_charge(path: Path, sub_id: str, days: int, status: str = "active") -> None:
    charge = (AS_OF + timedelta(days=days)).isoformat()
    write_sql(path, [("UPDATE subscription SET next_charge_date = ?, status = ? WHERE id = ?",
                      (charge, status, sub_id))])


def _only_one_due(path: Path) -> None:
    _set_charge(path, "sub_0001", 2)
    for sub_id in ("sub_0002", "sub_0003", "sub_0004"):
        _set_charge(path, sub_id, 999)


def test_summary_reports_the_due_window(seeded: Path) -> None:
    _only_one_due(seeded)
    got = subscription_notice.summary(SESSION)
    assert got["due"] == 1 and got["overdue"] == 0
    assert got["within_days"] == 7 and got["total_amount_yuan"] == "15.00"


def test_summary_is_read_only_and_repeatable(seeded: Path) -> None:
    """界面每帧都会问一次 —— 必须可重复调用，且**不消耗**任何状态。"""
    _only_one_due(seeded)
    first = subscription_notice.summary(SESSION)
    assert subscription_notice.summary(SESSION) == first
    assert subscription_notice.summary(SESSION) == first


def test_summary_is_empty_when_nothing_is_due(seeded: Path) -> None:
    for sub_id in ("sub_0001", "sub_0002", "sub_0003", "sub_0004"):
        _set_charge(seeded, sub_id, 999)
    assert subscription_notice.summary(SESSION) == {}


def test_summary_reports_overdue_separately(seeded: Path) -> None:
    _set_charge(seeded, "sub_0001", -3)
    for sub_id in ("sub_0002", "sub_0003", "sub_0004"):
        _set_charge(seeded, sub_id, 999)
    got = subscription_notice.summary(SESSION)
    assert got["due"] == 0 and got["overdue"] == 1
    assert got["overdue_total_amount_yuan"] == "15.00"


def test_summary_skips_the_sidebar_session(seeded: Path) -> None:
    """侧边栏余额借用了 handle，但提醒条不该为它计算。"""
    _only_one_due(seeded)
    assert subscription_notice.summary(f"{SESSION}:sidebar-balance") == {}


def test_summary_never_raises_when_the_tool_fails(seeded: Path, monkeypatch) -> None:
    monkeypatch.setattr(subscription_notice.subscription_remind, "list_due_subscriptions",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert subscription_notice.summary(SESSION) == {}


@pytest.mark.parametrize("session", ["", "web-abcd1234"])
def test_summary_works_for_ordinary_sessions(seeded: Path, session: str) -> None:
    _only_one_due(seeded)
    assert subscription_notice.summary(session)["due"] == 1
