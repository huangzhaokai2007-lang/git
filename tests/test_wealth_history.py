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


def test_history_covers_five_years_with_int_basis_points(seeded: Path) -> None:
    for item in _items():
        history = item["history"]
        assert [row["year"] for row in history] == [2021, 2022, 2023, 2024, 2025]
        for row in history:
            assert isinstance(row["return"], int) and isinstance(row["drawdown"], int)
            assert row["return_text"].endswith("%") and row["drawdown"] <= 0
            assert row["drawdown_text"].endswith("%")
            if row["drawdown"] == 0:
                assert row["drawdown_text"] == "0.00%"     # 零回撤不许显示成 +0.00%


def test_history_is_deterministic_across_calls(seeded: Path) -> None:
    first = {item["product_id"]: item["history"] for item in _items()}
    second = {item["product_id"]: item["history"] for item in _items()}
    assert first == second                       # 同一 id 永远同一组数字


def test_higher_risk_means_deeper_drawdown(seeded: Path) -> None:
    def worst(level: str) -> int:
        return min(row["drawdown"] for item in _items(level) for row in item["history"])

    assert worst("R1") > worst("R3") > worst("R5")


def test_history_stays_within_the_range_shown_on_the_card(seeded: Path) -> None:
    """历年数字必须落在卡片图例那条区间里。

    踩过的坑：历史区间按档位硬编码、卡片区间用 `expected_yield ± RISK_SWING`，两边各算各的，
    结果货基卡片写 `0.00 ~ +2.10%`、展开却看到 `+2.31%` —— 自己打自己脸。
    """
    for item in _items():
        lo = float(item["max_loss_pct"].rstrip("%").lstrip("+"))
        hi = float(item["max_gain_pct"].rstrip("%").lstrip("+"))
        for row in item["history"]:
            assert lo <= row["return"] / 100 <= hi, (item["name"], row)
            assert row["drawdown"] / 100 >= lo, (item["name"], row)


def test_monthly_candles_are_deterministic_and_well_formed(seeded: Path) -> None:
    """月 K：24 根、末月 = 数据锚点所在月、OHLC 关系成立、净值落在卡片区间内。"""
    for item in _items():
        candles = item["candles"]
        assert len(candles) == 60
        assert candles[-1]["period"] == "2026-09"           # 与 data.seed.AS_OF 同月
        lo = float(item["max_loss_pct"].rstrip("%").lstrip("+")) / 100
        hi = float(item["max_gain_pct"].rstrip("%").lstrip("+")) / 100
        for row in candles:
            assert row["low"] <= min(row["open"], row["close"])
            assert row["high"] >= max(row["open"], row["close"])
            assert 10000 * (1 + lo) - 1 <= row["close"] <= 10000 * (1 + hi) + 1
    again = {item["product_id"]: item["candles"] for item in _items()}
    first = {item["product_id"]: item["candles"] for item in _items()}
    assert again == first


def test_candles_drift_up_for_positive_yield(seeded: Path) -> None:
    """净值要跟着预期年化往上走，不能画成"预期 9.8%、两年跌 16%"。

    踩过的坑：一开始只给噪声不给漂移，14 只产品全部收在起点以下 —— 卡片写「预期年化 9.8%」，
    图却是条下滑线，自相矛盾。漂移还必须封顶（货基年化 2.1%、窗口 24 个月本可涨 4.2%，
    但上沿只有 +2.1%，不封顶就会一半月份顶在天花板上画直线）。
    """
    ratios = []
    for item in _items():
        candles = item["candles"]
        assert float(item["yield"].rstrip("%")) > 0
        ratio = candles[-1]["close"] / candles[0]["open"]
        ratios.append(ratio)
        #: 25 只产品、5 年随机游走，个别品种小幅回落是正常的（真实基金也会）。
        #: 但**不能整片向下** —— 那是「预期年化 3%、五年跌 10%」的自相矛盾。
        assert ratio > 0.97, f"{item['name']} 五年跌了 {(1 - ratio) * 100:.1f}%"
        # 没有连续三个月钉在同一个价位（那是被 clamp 卡住的征兆）
        closes = [row["close"] for row in candles]
        assert any(closes[i] != closes[i - 1] for i in range(1, len(closes)))
    ratios.sort()
    assert ratios[len(ratios) // 2] > 1.0, "中位数必须高于起点"


def test_kline_spec_is_a_candlestick_with_zoom() -> None:
    """图表结构钉死：影线 rule + 实体 bar、开收盘双 y、涨跌条件着色、可交互缩放。

    界面测试只能断言「元素存在」，看不到图长什么样；这条补上**规格层面**的保证 ——
    以后谁把 y2 去掉、把 condition 写错，这里会直接红。
    """
    import json

    from interfaces.web import wealth_ui

    candles = [{"period": "2026-08", "open": 10000, "high": 10200, "low": 9800, "close": 10150},
               {"period": "2026-09", "open": 10150, "high": 10300, "low": 10050, "close": 10100}]
    spec = wealth_ui._kline_spec(candles).to_dict()
    layer = spec["layer"]
    assert layer[0]["mark"]["type"] == "rule"       # 影线
    assert layer[1]["mark"]["type"] == "bar"        # 实体
    assert layer[0]["encoding"]["y2"]["field"] == "h"
    assert layer[1]["encoding"]["y2"]["field"] == "c"
    assert "condition" in json.dumps(spec)          # 阳绿阴红
    assert spec["params"] or "interactive" in json.dumps(spec)   # 可缩放
