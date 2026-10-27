"""F18 补全：逾期订阅在**列表页**也要标出来（与提醒口径一致）。

提醒里说「扣费日已过」，但点进「管订阅」看不出哪条逾期 —— 那是自相矛盾。
本文件钉住：工具层给 facts 打标、两层渲染逐字同源、界面卡片也显示标记。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from data.seed import AS_OF
from agent import render_lists
from tools import subscription

from tests.conftest import write_sql


def _set_charge(path: Path, sub_id: str, days: int, status: str = "active") -> None:
    charge = (AS_OF + timedelta(days=days)).isoformat()
    write_sql(path, [("UPDATE subscription SET next_charge_date = ?, status = ? WHERE id = ?",
                      (charge, status, sub_id))])


def test_active_overdue_is_marked_in_the_list(seeded: Path) -> None:
    _set_charge(seeded, "sub_0001", -4)
    items = subscription.list_subscriptions("active").facts["items"]
    item = next(i for i in items if i["id"] == "sub_0001")
    assert item["days_overdue"] == 4 and item["overdue_cn"] == "已过 4 天"


def test_future_and_cancelled_are_not_marked(seeded: Path) -> None:
    """只有「active 且扣费日已过」才标逾期：未来的不算，已取消的更不算。"""
    _set_charge(seeded, "sub_0001", 3)
    _set_charge(seeded, "sub_0002", -2, status="cancelled")
    items = {i["id"]: i for i in subscription.list_subscriptions("active").facts["items"]}
    assert "days_overdue" not in items["sub_0001"]
    assert all("days_overdue" not in i for i in subscription.list_subscriptions("cancelled").facts["items"])


def test_both_layers_render_the_overdue_marker_identically(seeded: Path) -> None:
    """工具层与编排层的明细行必须逐字一致（既有约定），逾期标记也一样。"""
    _set_charge(seeded, "sub_0001", -4)
    items = subscription.list_subscriptions("active").facts["items"]
    item = next(i for i in items if i["id"] == "sub_0001")
    agent_line = render_lists.subscription_line(item)
    tool_line = subscription._subscription_line(item)
    assert agent_line == tool_line
    assert "已过 4 天" in agent_line and item["next_charge_date"] in agent_line


def test_rendered_list_shows_the_overdue_marker(seeded: Path) -> None:
    _set_charge(seeded, "sub_0001", -4)
    facts = subscription.list_subscriptions("active").facts
    text = render_lists.render_subscriptions(facts)
    assert "已过 4 天" in text
