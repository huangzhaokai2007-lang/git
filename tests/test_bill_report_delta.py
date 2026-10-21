"""账单总结的「较上一账期多/少多少」（2026-10-21 按用户要求补的两行）。

既有 `test_query_report.py` 盯的是"写进 markdown 的数字都在 facts 里"；这里额外盯
**写得对不对**：差额算得对不对、方向词（多/少/持平）跟符号一不一致、有没有被负号污染
成"少 -4,304.00 元"这种双重否定。

为什么值得单开一个文件：百分比看得出趋势、看不出 magnitude，用户要的是**差额本身**；
差额是本期与上期两次查询的差，算错一位就全错，必须有独立重算盯着。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import query

from tests.conftest import assert_covered, assert_dates_covered, raw
from tests.test_query_common import assert_facts_verbatim

PREV_SQL = ("SELECT SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END) AS out_sum,"
            " SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS in_sum"
            " FROM txn WHERE substr(ts, 1, 7) = ?")


def _word(diff: int) -> str:
    """方向词（纯展示，不是数字）：与 `_delta_line` 的口径同源，测试自己再算一遍。"""
    return "多" if diff > 0 else ("少" if diff < 0 else "持平")


def test_delta_amounts_match_raw_sql_of_the_previous_period(seeded: Path) -> None:
    """差额必须等于"本期 − 上期"，且上期数字要对得上原始 SQL（不是从百分比倒推）。"""
    facts = query.generate_bill_report("2026-08").facts
    prev = raw(seeded, PREV_SQL, ("2026-07",))[0]

    assert facts["prev_total"] == prev["out_sum"] and facts["prev_in_sum"] == prev["in_sum"]
    assert facts["vs_prev_out_diff"] == facts["out_sum"] - facts["prev_total"]
    assert facts["vs_prev_in_diff"] == facts["in_sum"] - facts["prev_in_sum"]
    assert facts["vs_prev_out_diff_abs"] == abs(facts["vs_prev_out_diff"])
    assert facts["vs_prev_in_diff_abs"] == abs(facts["vs_prev_in_diff"])


def test_markdown_states_direction_and_absolute_amount(seeded: Path) -> None:
    """明细行的措辞由符号决定、金额用绝对值 —— 不许露出「少 -4,304.00 元」。"""
    result = query.generate_bill_report("2026-08")
    facts, markdown = result.facts, result.data["markdown"]
    out_line = next(line for line in markdown.splitlines() if line.startswith("- 较上一账期支出"))
    in_line = next(line for line in markdown.splitlines() if line.startswith("- 较上一账期收入"))

    for line, diff, abs_yuan in ((out_line, facts["vs_prev_out_diff"], facts["vs_prev_out_diff_abs_yuan"]),
                                 (in_line, facts["vs_prev_in_diff"], facts["vs_prev_in_diff_abs_yuan"])):
        assert line.split("：", 1)[1].startswith(_word(diff)), line
        assert abs_yuan in line                       # 差额逐字来自 facts
        if diff < 0:
            assert f"-{abs_yuan}" not in line         # 双重否定：负号不许出现在差额金额上

    assert_covered(markdown, facts) and assert_dates_covered(markdown, facts)
    assert_facts_verbatim(markdown, facts)


@pytest.mark.parametrize("period", ["2026-01", "2026-03", "2026-05", "2026-07", "2026-08", "2026-09"])
def test_direction_word_tracks_the_sign_in_every_period(seeded: Path, period: str) -> None:
    """逐账期核对：方向词跟着符号走，不是把 8 月那一条写死后到处复用。"""
    result = query.generate_bill_report(period)
    facts = result.facts
    if facts["vs_prev_pct"] is None:
        assert "较上一账期" not in result.data["markdown"]
        return
    lines = [line for line in result.data["markdown"].splitlines() if line.startswith("- 较上一账期")]
    pairs = [(facts["vs_prev_out_diff"], "支出"), (facts["vs_prev_in_diff"], "收入")]
    matched = [(line, diff) for line in lines for diff, label in pairs if f"较上一账期{label}" in line]
    assert matched and all(line.split("：", 1)[1].startswith(_word(diff)) for line, diff in matched)


def test_first_period_without_a_previous_one_has_no_delta_line(seeded: Path) -> None:
    """数据集首月没有上期 → 不写"较上一账期"（宁可不写，也不写 0 元这种假数字）。"""
    result = query.generate_bill_report("2025-09")
    assert result.facts["vs_prev_pct"] is None and result.facts["vs_prev_in_pct"] is None
    assert "较上一账期" not in result.data["markdown"]
    assert_facts_verbatim(result.data["markdown"], result.facts)


def test_yearly_report_shows_the_delta_too(seeded: Path) -> None:
    """年度账期同样要有差额 —— 用户问的是"比上一期"，不是"比上个月"。"""
    result = query.generate_bill_report("2026", kind="yearly")
    facts, markdown = result.facts, result.data["markdown"]
    assert result.ok and "较上一账期支出" in markdown
    assert_covered(markdown, facts) and assert_facts_verbatim(markdown, facts)
