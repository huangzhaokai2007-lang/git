"""持仓的**累计收益**（2026-10-31 加）：用户最想看的那个数，必须和 K 线同源、符号一致。"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.seed import AS_OF
from tools import wealth


def _items() -> list[dict]:
    result = wealth.list_holdings()
    assert result.ok
    return result.facts["items"]


def test_every_holding_reports_cumulative_profit(seeded: Path) -> None:
    """每只持仓都要有「累计收益（元）」和「累计收益率（%）」，且带正负号。"""
    for item in _items():
        assert item["cum_profit_yuan"].lstrip("+-").replace(",", "").replace(".", "").isdigit()
        assert item["cum_profit_pct"].endswith("%")
        assert item["cum_profit_yuan"][0] in "+-"          # 零也要带号，界面才好上色
        assert item["cum_profit_pct"][0] in "+-"


def test_signs_agree_between_amount_and_percent(seeded: Path) -> None:
    """金额和百分比不能一个说赚一个说亏。"""
    for item in _items():
        amount_up = not item["cum_profit_yuan"].startswith("-")
        pct_up = not item["cum_profit_pct"].startswith("-")
        assert amount_up == pct_up, item["name"]
        assert item["cum_is_profit"] == pct_up


def test_total_equals_sum_of_items(seeded: Path) -> None:
    """合计累计收益 = 各只之和（界面不许自己加，这个数只能来自工具层）。"""
    facts = wealth.list_holdings().facts
    total = float(facts["cum_profit_total_yuan"].replace(",", "").replace("+", ""))
    parts = sum(float(i["cum_profit_yuan"].replace(",", "").replace("+", "")) for i in facts["items"])
    assert abs(total - parts) < 0.01


def test_holding_bought_today_is_flat(seeded: Path) -> None:
    """买入日就是数据锚点 → 累计收益为 0，不能凭空算出收益。"""
    from data import dao

    conn = dao.connection()
    conn.execute("delete from holding")
    conn.execute(
        "insert into holding (id, user_id, product_id, amount, purchase_date, status) "
        "values ('h_today', 'u_zhangsan_0001', 'prod_r1_mmf', 100000, ?, 'held')",
        (AS_OF.isoformat(),))
    conn.commit()          # 不能 close：这是全进程共享的连接

    item = _items()[0]
    assert item["cum_profit_pct"] in ("+0.00%", "-0.00%")
    assert float(item["cum_profit_yuan"].replace(",", "").replace("+", "")) == pytest.approx(0.0)


def test_message_states_the_profit_in_plain_words(seeded: Path) -> None:
    """回执要直白写出累计收益，不能只说「持有多少」。"""
    message = wealth.list_holdings().message
    assert "累计收益" in message
    assert "元" in message
