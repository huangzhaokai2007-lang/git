"""编排层：收款人的银行卡（界面只准调 agent 层，不能直连 tools）。

- `cards_of(session_id, payee_id)` → `[{card_id, bank, card_no_mask, card_type, is_default}]`
- `pick_card(session_id, card_id)` → 把「用户选的卡」落进在途确认卡的槽位（改卡走 `write_flow.edit`）

卡号只有掩码，完整号既不入库也不返回（铁律 8）。
"""

from __future__ import annotations

from tools.payee import list_payee_cards


def cards_of(payee_id: str) -> list[dict]:
    """某位收款人的全部卡（默认卡在前）；取不到就返回空列表（界面显示「没有卡」）。"""
    if not payee_id:
        return []
    result = list_payee_cards(payee_id)
    if not result.ok:
        return []
    return list((result.data or {}).get("items") or [])


def primary_label(cards: list[dict]) -> str:
    """默认卡的一句话描述（没有卡 → 空串）。"""
    default = next((card for card in cards if card.get("is_default")), None)
    if default is None:
        default = cards[0] if cards else None
    if default is None:
        return ""
    return f"{default['bank']} {default['card_no_mask']}"
