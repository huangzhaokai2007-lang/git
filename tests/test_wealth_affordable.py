"""理财卡片的「起购额 / 买不买得起」：工具层判定，界面只展示。

背景（演示实测）：产品按收益降序，第一张「成长精选三年期」起购 10 万，
而储蓄余额只有 5.2 万 —— 卡片上又不写起购额，用户点「订购」才知道买不起。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from tools import wealth

from tests.test_tools_wealth_risk import AGGRESSIVE
from tests.test_wealth_recommend_flow import BALANCED


def test_facts_expose_balance_and_affordable_flags(seeded: Path) -> None:
    wealth.assess_risk(AGGRESSIVE)          # 先测出 R5，否则被测评上限压到 R3
    result = wealth.recommend_wealth("R5")
    assert result.ok
    facts = result.facts
    assert facts["available_yuan"] == "52,281.00" and facts["available"] == 5_228_100

    by_id = {item["product_id"]: item for item in facts["items"]}
    assert by_id["prod_r5_growth"]["min_amount_yuan"] == "100,000.00"
    assert by_id["prod_r5_growth"]["affordable"] is False        # 起购 10 万 > 余额 5.2 万
    assert by_id["prod_r4_quant720"]["affordable"] is True        # 起购 5 万 ≤ 余额
    assert by_id["prod_r1_mmf"]["affordable"] is True


def test_affordable_flags_follow_the_risk_ceiling(seeded: Path) -> None:
    """R1 只能看到两只货基（起购 1 元 / 10 元）—— 全部买得起，不会误判。"""
    wealth.assess_risk(BALANCED)
    facts = wealth.recommend_wealth("R1").facts
    assert [item["affordable"] for item in facts["items"]] == [True, True]


def test_balance_read_failure_does_not_break_the_recommendation(seeded: Path, monkeypatch) -> None:
    """读余额只是"锦上添花"：失败时推荐照常给，只是不置灰（申购时还有一次余额校验）。"""
    wealth.assess_risk(BALANCED)

    def boom(*args, **kwargs):
        raise ValueError("db busy")

    monkeypatch.setattr(wealth.dao, "get_balance", boom)
    result = wealth.recommend_wealth("R3")
    assert result.ok and result.error_code is None
    assert "available" not in result.facts and "available_yuan" not in result.facts
    assert all(item["affordable"] is True for item in result.facts["items"])


def _render(items):
    actions: list[str] = []
    turn = {"trace_id": "afford", "intent": "wealth_recommend", "error_code": None,
            "facts": {"items": items}, "states": ["REPLY"], "tier": "L0"}

    def page(turn, actions):
        from interfaces.web import components, wealth_ui
        components.transcript([{"role": "assistant", "text": "推荐", "turn": turn}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=actions.append, key_prefix=key))

    at = AppTest.from_function(page, args=(turn, actions), default_timeout=30).run()
    return at, actions


def _item(**over):
    base = {"product_id": "p1", "name": "成长精选三年期", "risk_label": "高风险",
            "yield": "8.50%", "term": 1095, "min_amount": 10_000_000,
            "min_amount_yuan": "100,000.00", "max_gain_pct": "+38.50",
            "max_loss_pct": "-30.00", "affordable": True}
    return {**base, **over}


def test_card_shows_the_minimum_amount() -> None:
    at, _ = _render([_item()])
    assert not at.exception
    assert any("起购 100,000.00 元" in m.value for m in at.markdown)


def test_unaffordable_card_disables_the_button() -> None:
    at, actions = _render([_item(affordable=False)])
    assert not at.exception
    assert any("起购金额高于储蓄余额" in m.value for m in at.markdown)
    try:
        next(b for b in at.button if b.label == "订购").click().run()
    except Exception:                       # 禁用的按钮点不动（不同版本表现不同）
        pass
    assert actions == []                    # 关键：不会发起申购


def test_affordable_card_still_sends_the_order() -> None:
    at, actions = _render([_item(affordable=True)])
    next(b for b in at.button if b.label == "订购").click().run()
    assert actions and actions[-1].startswith("买 ")
