"""渲染层用例（表格/徽章）。"""

from __future__ import annotations




# ---------------- 2026-10-24 回归：持仓渲染曾崩（NameError: RISK_LABELS） ----------------

def test_render_holdings_does_not_crash(seeded) -> None:
    """「我的理财」曾直接崩（拆 `render_tables.py` 时漏带 `RISK_LABELS`，随后又漏 `TemplateError`）。

    这里用**工具层的真实 facts** 渲染 —— 自造 facts 会因缺 `total_count` 被正确炸掉，
    那样测的就不是「持仓能不能渲染」了。
    """
    from agent import render_tables
    from tools import wealth_holdings

    result = wealth_holdings.list_holdings()
    assert result.ok is True
    text = render_tables.render_holdings(result.facts)
    assert "预期年化" in text and result.facts["items"]
