"""理财卡片的「历年收益与亏损」：工具层确定性生成，界面点开图例展示。

口径：演示数据集没有净值表，规格也未定义历史业绩 → 数字是**模拟**的，
按风险档位给区间，界面必须标明。本文件钉住三件事：
1. 同一产品永远得到同一组数字（跨进程可复现）；
2. facts 里**不许出现 float**（红线 2），百分比用整数基点 + 展示文本；
3. 风险越高，回撤越大；界面把三年数据渲染出来。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from tools import wealth

from tests.test_tools_wealth_risk import AGGRESSIVE


def _items(level: str = "R5") -> list[dict]:
    wealth.assess_risk(AGGRESSIVE)
    result = wealth.recommend_wealth(level)
    assert result.ok
    return result.facts["items"]


def test_history_covers_three_years_with_int_basis_points(seeded: Path) -> None:
    for item in _items():
        history = item["history"]
        assert [row["year"] for row in history] == [2023, 2024, 2025]
        for row in history:
            assert isinstance(row["return"], int) and isinstance(row["drawdown"], int)
            assert row["return_text"].endswith("%") and row["drawdown_text"].startswith("-")


def test_history_is_deterministic_across_calls(seeded: Path) -> None:
    first = {item["product_id"]: item["history"] for item in _items()}
    second = {item["product_id"]: item["history"] for item in _items()}
    assert first == second                       # 同一 id 永远同一组数字


def test_higher_risk_means_deeper_drawdown(seeded: Path) -> None:
    def worst(level: str) -> int:
        return min(row["drawdown"] for item in _items(level) for row in item["history"])

    assert worst("R1") > worst("R3") > worst("R5")


def test_card_expander_renders_the_history_rows() -> None:
    item = {"product_id": "p1", "name": "成长精选三年期", "risk_label": "高风险",
            "yield": "8.50%", "term": 1095, "min_amount": 10_000_000,
            "min_amount_yuan": "100,000.00", "max_gain_pct": "+38.50",
            "max_loss_pct": "-30.00", "affordable": True,
            "history": [{"year": 2023, "return": 1548, "return_text": "+15.48%",
                         "drawdown": -1645, "drawdown_text": "-16.45%"},
                        {"year": 2024, "return": -946, "return_text": "-9.46%",
                         "drawdown": -1771, "drawdown_text": "-17.71%"}]}
    turn = {"trace_id": "hist", "intent": "wealth_recommend", "error_code": None,
            "facts": {"items": [item]}, "states": ["REPLY"], "tier": "L0"}

    def page(turn):
        from interfaces.web import components, wealth_ui
        components.transcript([{"role": "assistant", "text": "推荐", "turn": turn}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=lambda _: None, key_prefix=key))

    at = AppTest.from_function(page, args=(turn,), default_timeout=30).run()
    assert not at.exception
    text = "\n".join(m.value for m in at.markdown)
    assert "+15.48%" in text and "-17.71%" in text and "2024" in text
    assert "模拟数据" in text                     # 不许把模拟数据说成真实业绩
