"""卡片查询工具（卡 23）。"""

from __future__ import annotations

from tools.schemas import ToolResult


def list_cards(status: str | None = None) -> ToolResult:
    """列出卡片。"""
    from data import dao
    from tools._query_common import current_user_id, _ok

    # 简化实现
    cards = [
        {
            "card_id": "card_001",
            "card_no_mask": "6222 **** **** 0001",
            "type": "savings",
            "status": "normal",
            "credit_limit": None,
            "single_limit": 500000,
            "daily_limit": 2000000,
        },
        {
            "card_id": "card_002",
            "card_no_mask": "6222 **** **** 0002",
            "type": "credit",
            "status": "normal",
            "credit_limit": 5000000,
            "single_limit": 1000000,
            "daily_limit": 3000000,
        },
    ]
    if status:
        cards = [c for c in cards if c["status"] == status]

    items = cards
    return _ok(
        {"items": items, "total_count": len(items)},
        {"total_count": len(items)},
        f"共有 {len(items)} 张卡片。",
    )
