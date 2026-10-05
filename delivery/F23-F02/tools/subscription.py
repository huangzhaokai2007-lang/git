"""订阅工具（占位）。"""

from __future__ import annotations

from tools.schemas import ToolResult


def list_subscriptions(status: str = "active") -> ToolResult:
    """列出订阅。"""
    from data import dao
    from tools._query_common import current_user_id, _ok

    subs = dao.list_subscriptions(current_user_id(), status)
    items = [
        {
            "id": sub["id"],
            "merchant": sub["merchant"],
            "cycle": sub["cycle"],
            "next_charge_date": sub["next_charge_date"],
            "amount": sub["amount"],
        }
        for sub in subs
    ]
    return _ok(
        {"items": items, "total_count": len(items)},
        {"total_count": len(items), "items": items},
        f"共有 {len(items)} 个订阅服务。",
    )
