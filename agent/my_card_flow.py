"""「收款到我哪张卡」的读卡助手（界面只准调 agent 层）。

数据来源两级：
1. 汤的卡片模块里**已绑定**的卡（`agent_card_binding`，只读）；
2. 没有绑定时退回 T18 `list_cards()`（**我名下的卡**）—— 群收款不该因为"还没绑卡"就没得选。
"""

from __future__ import annotations

from data import card_binding, dao
from tools._query_common import current_user_id
from tools.card_query import list_cards

#: 卡种 → 中文（与汤的卡片口径一致）
_TYPE_CN = {"savings": "储蓄卡", "credit": "信用卡"}


def _bound(user_id: str) -> list[dict]:
    conn = dao.connection()
    card_binding.ensure_schema(conn)
    return card_binding.bound_cards(conn, user_id, None)


def cards_of(user_id: str | None = None) -> list[dict]:
    """我名下可用于收款的卡（默认储蓄卡在前）。"""
    owner = user_id or current_user_id()
    rows = [row for row in _bound(owner) if row["status"] == "normal"]
    if rows:
        cards = [{"card_id": row["id"], "bank": row.get("bank") or "本行",
                  "card_no_mask": row["card_no_mask"],
                  "card_type": _TYPE_CN.get(str(row.get("type")), "储蓄卡"),
                  "is_default": row.get("type") == "savings"} for row in rows]
    else:                                     # 退回「我名下的卡」（T18）
        result = list_cards("normal")
        items = (result.data or {}).get("items") if result.ok else None
        cards = [{"card_id": item["card_id"], "bank": "本行",
                  "card_no_mask": item["card_no_mask"],
                  "card_type": _TYPE_CN.get(str(item.get("type")), "储蓄卡"),
                  "is_default": item.get("type") == "savings"} for item in (items or [])]
    cards.sort(key=lambda card: not card["is_default"])       # 默认（储蓄卡）排最前
    return cards


def pick(card_id: str | None) -> dict | None:
    """按 id 取卡；不传 = 默认储蓄卡；取不到就沿用默认卡（界面上不报错）。"""
    cards = cards_of()
    if not cards:
        return None
    if isinstance(card_id, str) and card_id:
        found = next((card for card in cards if card["card_id"] == card_id), None)
        if found is not None:
            return found
    return cards[0]
