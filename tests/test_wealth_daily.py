"""T14b `product_daily`：按需拉取的日 K（L0 只读、公共目录、无归属断言）。

为什么单独测：日 K 不随推荐下发（14 只全塞进去要多 150 KB），是点开某只才取的，
所以它的窗口、体积、确定性、参数校验都得单独钉住。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import wealth_daily
from tools import wealth
from tools.schemas import ErrorCode


def test_daily_window_is_120_trading_days(seeded: Path) -> None:
    result = wealth.product_daily("prod_r5_growth")
    assert result.ok and result.facts["day_count"] == 120
    candles = result.facts["candles"]
    assert len(candles) == 120
    # 末个交易日 = 数据锚点（2026-09-12 是周六，取前一个周五）
    assert candles[-1][0] == "2026-09-11"
    # 只跳周末：不能出现周六/周日
    import datetime as dt
    for row in candles:
        assert dt.date.fromisoformat(row[0]).weekday() < 5


def test_daily_candles_are_well_formed_and_deterministic(seeded: Path) -> None:
    first = wealth.product_daily("prod_r5_growth").facts["candles"]
    second = wealth.product_daily("prod_r5_growth").facts["candles"]
    assert first == second                      # 同一产品永远同一组日线
    for _date, open_, high, low, close in first:
        assert low <= min(open_, close) <= max(open_, close) <= high
        assert all(isinstance(value, int) for value in (open_, high, low, close))


def test_daily_payload_stays_small(seeded: Path) -> None:
    """紧凑数组：一只产品约 5 KB。用对象形状会到 9 KB，14 只就是 130 KB。"""
    facts = wealth.product_daily("prod_r5_growth").facts
    assert len(json.dumps(facts)) / 1024 < 8


def test_daily_rejects_bad_window_and_unknown_product(seeded: Path) -> None:
    assert wealth.product_daily("prod_r5_growth", 5).error_code == ErrorCode.INVALID_ARGUMENT
    assert wealth.product_daily("prod_r5_growth", 999).error_code == ErrorCode.INVALID_ARGUMENT
    assert wealth.product_daily("nope").error_code == ErrorCode.NOT_FOUND


def test_agent_seam_writes_an_audit_row(seeded: Path) -> None:
    """界面不许直接 import tools，必须走 `agent.wealth_daily.load`（顺带留痕）。"""
    from tests.conftest import count

    before = count(seeded, "audit_log")
    payload = wealth_daily.load("sess-daily", "prod_r1_mmf")
    assert payload["ok"] and payload["day_count"] == 120
    assert payload["trace_id"].startswith("trace-")
    assert count(seeded, "audit_log") == before + 1


def test_card_button_loads_daily_on_demand() -> None:
    """点「日 K」才调 loader；再点「收起」不重复取数。"""
    calls: list[str] = []

    def loader(product_id: str) -> dict:
        calls.append(product_id)
        candles = [[f"2026-09-{day:02d}", 10000, 10020, 9990, 10010] for day in range(1, 21)]
        return {"ok": True, "candles": candles, "name": "测试产品"}

    item = {"product_id": "p1", "name": "测试产品", "risk_label": "高风险",
            "yield": "8.50%", "term": 1095, "min_amount": 10_000_000,
            "min_amount_yuan": "100,000.00", "max_gain_pct": "+38.50",
            "max_loss_pct": "-30.00", "affordable": True,
            "candles": [{"period": "2026-09", "open": 10000, "high": 10100,
                         "low": 9900, "close": 10050}]}
    turn = {"trace_id": "daily", "intent": "wealth_recommend", "error_code": None,
            "facts": {"items": [item]}, "states": ["REPLY"], "tier": "L0"}

    def page(turn, loader, calls):
        from interfaces.web import components, wealth_ui
        components.transcript([{"role": "assistant", "text": "推荐", "turn": turn}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=lambda _: None, key_prefix=key,
                                  daily_loader=loader))

    at = AppTest.from_function(page, args=(turn, loader, calls), default_timeout=30).run()
    assert not at.exception
    next(b for b in at.button if "日 K" in b.label).click().run()
    assert calls == ["p1"]                       # 点一次取一次
    assert any(b.label == "收起日 K" for b in at.button)
    next(b for b in at.button if b.label == "收起日 K").click().run()
    assert calls == ["p1"]                       # 收起不重新取数
