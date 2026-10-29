"""群收款「含我」的端到端守卫（2026-10-28 用户要求）。

口径：用户说的总额 = **含我**的总额；份数 = 收款人数 + 1（我占一份）。
- 卡面：总金额（含我）/ 每人 / 我承担 / 待收
- 落库：`aa_request.total` = 含我总额；`aa_share` 每人应缴；**待收 = Σ每人应缴**，我承担 = 差额
"""

from __future__ import annotations

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tools import _aa_request

SESSION = "sess-include-self"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _intent(names: list[str], amount: str):
    from agent.classifier import IntentOut

    return IntentOut(intent="aa_collect", confidence=0.95,
                     slots={"payee_ids": names, "amount": amount}, missing_slots=[])


def _turn(monkeypatch, text: str, names: list[str], amount: str):
    import json

    verdict = _intent(names, amount)

    def chat_json(system, user, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return verdict
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return orchestrator.handle(text, session_id=SESSION)


def test_card_shows_my_share_and_collectible(wired, monkeypatch) -> None:
    """我和王五收 300 → 每人 150、我承担 150、待收 150（正是用户举的例子）。"""
    card = _turn(monkeypatch, "我和王五群收款 300 元", ["王五"], "300")
    assert card.executed is False
    assert "总金额（含我）：300.00 元" in card.reply
    assert "每人：150.00 元" in card.reply
    assert "我承担：150.00 元" in card.reply
    assert "待收：150.00 元" in card.reply


def test_confirm_then_request_stores_group_total_and_my_share(wired, monkeypatch) -> None:
    """确认后落库：`total` = 300（含我），份额 = 150，读出来 待收 150 / 我承担 150。"""
    _turn(monkeypatch, "我和王五群收款 300 元", ["王五"], "300")
    done = _turn(monkeypatch, "确认", ["王五"], "300")
    assert done.executed is True, done.reply

    listed = _aa_request.list_aa_requests("active")
    assert listed.ok is True
    item = (listed.facts or {}).get("items", [])[0]
    #: 2026-10-29（用户要求「把你没写的写了」）：库里 total = **含我总额** 300，
    #: 份额 = 每人应缴 150，「待收 150 / 我承担 150」由差额派生。
    assert item["total"] == 30000                     # 含我总额（分）
    assert item["collect_cents"] == 15000             # 待收 = Σ份额
    assert item["my_share_cents"] == 15000            # 我承担 = total − 待收
    assert item["share_count"] == 1 and item["paid_count"] == 0


def test_amount_above_total_is_rejected(wired) -> None:
    """每人金额之和超过总额 → INVALID_ARGUMENT（含我口径下 total 是最小上界）。"""
    bad = _aa_request.open_aa_request(["payee_0003"], [20000], 15000, "equal", "越界")
    assert bad.ok is False and str(bad.error_code) in ("INVALID_ARGUMENT",
                                                      "ErrorCode.INVALID_ARGUMENT")


def test_custom_split_keeps_zero_my_share(wired) -> None:
    """按人金额（custom）时我承担 0：总额 = Σ每人（老口径不变，避免两套语义打架）。"""
    ok = _aa_request.open_aa_request(["payee_0003", "payee_0005"], [10000, 20000], 30000,
                                     "custom", "按人")
    assert ok.ok is True
    item = (_aa_request.list_aa_requests("active").facts or {}).get("items", [])[0]
    assert item["collect_cents"] == 30000 and item["my_share_cents"] == 0   # 按人金额＝不含我
