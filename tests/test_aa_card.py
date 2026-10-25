"""F22 追加：**AA 卡面**（`agent/aa_flow` + `interfaces/web/aa_card_ui`）与**状态徽章**。

| 验收点 | 用例 |
|---|---|
| `aa_flow.active()` 只返回进行中 | `test_active_only_lists_active` |
| 徽章：状态码 → emoji；未知状态原样返回 | `test_badge_covers_state_machines` |
| 表格里带上徽章（AA / 订单 / 预约） | `test_tables_carry_badges` |
"""

from __future__ import annotations

import pytest

from agent import aa_flow, render_lists
from tests.conftest import raw


@pytest.fixture()
def wired(seeded):
    return seeded


def _open(wired, title: str, ids: list[str]) -> str:
    from tools import _aa_request as aa
    return aa.open_aa_request(ids, [10_000] * len(ids), 10_000 * len(ids), "equal",
                              title).data["request_id"]


def test_active_only_lists_active(wired) -> None:
    """只有 `active` 的进卡面；撤销后不再出现。"""
    from tools import _aa_request as aa
    ids = [raw(wired, "SELECT id FROM payee WHERE name = ?", ("王五",))[0]["id"]]
    rid = _open(wired, "周末聚餐", ids)
    assert [item["request_id"] for item in aa_flow.active()] == [rid]
    assert aa.cancel_aa_request(rid).ok is True
    assert aa_flow.active() == []
    assert len(aa_flow.requests()) == 1                       # 全部（含已撤销）仍查得到


def test_badge_covers_state_machines() -> None:
    """徽章覆盖三套状态机；未知状态不猜（原样返回）。"""
    assert render_lists.badge("active", "进行中") == "⏳ 进行中"
    assert render_lists.badge("done", "已完成") == "✅ 已完成"
    assert render_lists.badge("expired", "已失效") == "⌛ 已失效"
    assert render_lists.badge("cancelled", "已撤销") == "⛔ 已撤销"
    assert render_lists.badge("created", "已下单") == "🧾 已下单"
    assert render_lists.badge("pending", "待执行") == "⏳ 待执行"
    assert render_lists.badge("success", "已执行") == "✅ 已执行"
    assert render_lists.badge("failed", "执行失败") == "⚠️ 执行失败"
    assert render_lists.badge("weird", "奇怪") == "奇怪"


def test_tables_carry_badges() -> None:
    """三张表格的状态列都带徽章。"""
    aa_text = render_lists.render_aa_requests({"total_count": 1, "items": [
        {"title": "周末聚餐", "total_yuan": "300.00", "paid_count": 1, "share_count": 2,
         "status": "active", "status_cn": "进行中", "expire_at": "2026-10-25 00:52"}]})
    assert "⏳ 进行中" in aa_text
    order_text = render_lists.render_gift_orders({"total_count": 1, "items": [
        {"order_id": "ord_1", "contact": "王五", "gift_date": "2026-10-30",
         "amount_yuan": "498.00", "status": "paid", "status_cn": "已支付"}]})
    assert "✅ 已支付" in order_text
    scheduled = render_lists.render_schedules({"total_count": 1, "items": [
        {"schedule_id": "sch_1", "payee_name": "王五", "execute_at": "2026-10-30T09:00",
         "status": "pending", "amount": 10000, "amount_yuan": "100.00"}]})
    assert "⏳ 待执行" in scheduled
