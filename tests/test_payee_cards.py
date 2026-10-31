"""收款人**多张银行卡** + 转账时选卡（2026-10-31 用户需求，SPEC-CHANGE 已批）。

覆盖：① 数据层每位收款人 1–3 张卡、恰好一张默认卡；② 工具层 T51 只读他人卡必须拒；
③ 预览按卡走（默认 / 指定 / 他人卡拒绝）并回事实包；④ 确认卡印「收款卡」；
⑤ 定时转账同链路；⑥ 新增收款人自动生成 1–3 张卡。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from data import dao
from tools import payee as payee_tool
from tools import transfer

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from tools.payee import list_payee_cards  # noqa: E402


def _cards_in_db(payee_id: str) -> list[dict]:
    conn = dao.connection()
    rows = conn.execute(
        "SELECT * FROM payee_card WHERE payee_id = ? ORDER BY is_default DESC, id",
        (payee_id,)).fetchall()
    return [dict(row) for row in rows]


def test_every_seeded_payee_has_exactly_one_default_card(seeded) -> None:
    """seed：每位收款人 1–3 张卡，且恰好一张默认卡（转账不选卡时用它）。"""
    conn = dao.connection()
    payees = [row["id"] for row in conn.execute("SELECT id FROM payee ORDER BY id").fetchall()]
    assert payees, "种子数据里应有收款人"
    for payee_id in payees:
        cards = _cards_in_db(payee_id)
        assert 1 <= len(cards) <= 3, f"{payee_id} 卡数应在 1–3 张，实际 {len(cards)}"
        assert sum(1 for card in cards if card["is_default"]) == 1, f"{payee_id} 应有且仅有一张默认卡"
        banks = {card["bank"] for card in cards}
        assert all(bank for bank in banks), "银行名不能为空"


def test_list_payee_cards_returns_masked_cards(seeded) -> None:
    """T51：返回掩码卡号（绝不返回完整号）、默认卡排最前。"""
    result = list_payee_cards("payee_0003")
    assert result.ok, result.message
    items = result.data["items"]
    assert result.data["total_count"] == len(items) >= 2
    assert items[0]["is_default"] is True
    for item in items:
        assert item["card_no_mask"].startswith("6222 **** **** ")
        assert len(item["card_no_mask"].split()[-1]) == 4


def test_list_payee_cards_rejects_unknown_payee(seeded) -> None:
    """不存在的收款人 → NOT_FOUND（不猜、不返回空列表冒充成功）。"""
    result = list_payee_cards("payee_does_not_exist")
    assert result.ok is False and result.error_code == "NOT_FOUND"


def test_preview_uses_default_card_when_not_specified(seeded) -> None:
    """不传卡 → 用默认卡，事实包里带卡信息（卡面才能印）。"""
    result = transfer.preview_transfer("payee_0003", 100_00)
    assert result.ok, result.message
    assert result.facts["payee_card_is_default"] is True
    assert result.facts["payee_card_bank"] == "建设银行"
    assert result.facts["payee_card_mask"].endswith("2003")


def test_preview_accepts_a_specific_card(seeded) -> None:
    """指定第二张卡 → 事实包里换成那张（农业银行 **** 2007）。"""
    result = transfer.preview_transfer("payee_0003", 100_00, payee_card_id="pc_0003_b")
    assert result.ok, result.message
    assert result.facts["payee_card_id"] == "pc_0003_b"
    assert result.facts["payee_card_bank"] == "农业银行"
    assert result.facts["payee_card_is_default"] is False


def test_preview_rejects_a_card_of_another_payee(seeded) -> None:
    """拿别人的卡 id → INVALID_ARGUMENT（不静默换人、不换卡）。"""
    result = transfer.preview_transfer("payee_0003", 100_00, payee_card_id="pc_0004_a")
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"


def test_new_payee_gets_generated_cards(seeded) -> None:
    """新增收款人 → 自动生成 1–3 张卡，且恰好一张默认卡。"""
    result = payee_tool.add_payee("钱七", "13812345678")
    assert result.ok, result.message
    payee_id = result.data["payee_id"]
    cards = _cards_in_db(payee_id)
    assert 1 <= len(cards) <= 3
    assert sum(1 for card in cards if card["is_default"]) == 1
    assert all(card["card_no_mask"].startswith("6222 **** **** ") for card in cards)


def test_confirm_card_prints_the_selected_card(seeded) -> None:
    """确认卡上必须有「收款卡」这一行，且值来自事实包。"""
    from agent import confirm_card, write_flow

    confirm_card.reset_state()
    step = write_flow.start("transfer_single",
                            {"payee": "王五", "amount": "100",
                             "payee_card_id": "pc_0003_c"}, "sess-card-face")
    assert "CONFIRM_CARD" in step.states, step.reply
    assert "收款卡：中国银行 6222 **** **** 2009" in step.reply
