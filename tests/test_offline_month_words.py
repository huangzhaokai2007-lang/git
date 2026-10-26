"""单测：**中文月份说法**（「五月份」）必须直接就是一整段区间。

为什么补这份：用户实测反馈——说「我要查五月份的流水」，系统反复追问「请补充起始日期、
结束日期」。根因是替身认月份的正则只认阿拉伯数字（`5月`），中文写法匹配不上，于是
一路掉到最后的兜底分支，同时犯下两个错：

- `dates_from_text` 返回 `{}` → F02 缺槽位 → **追问具体日期**（用户看到的症状）；
- `period_from_text` 返回「本月」→ 账单分析**静默答成锚点当月**（比追问更坏：数字来自
  真实事实包、措辞也顺，只有月份是错的，用户根本无从察觉 —— 正是卡 17b 那类缺陷）。

月份本身就是一段闭区间：「五月」= 5 月 1 日 ~ 5 月 31 日，没有第二种解释。要求用户
把话说成「2026-05-01 到 2026-05-31」既不现实，也不该发生。
"""

from __future__ import annotations

import calendar

import pytest

from agent import orchestrator, session_memory
from agent.period import anchor_date
from app import cli

PERIODS = [f"{(anchor_date().year * 12 + anchor_date().month - 1 - 12 + i) // 12:04d}"
           f"-{(anchor_date().year * 12 + anchor_date().month - 1 - 12 + i) % 12 + 1:02d}"
           for i in range(13)]

_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
       7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二"}


def _expected_period(month: int) -> str:
    """裸月份怎么说：**超过锚点当月的按去年**（今天 9 月说「十二月」指去年 12 月）。"""
    today = anchor_date()
    year = today.year if month <= today.month else today.year - 1
    return f"{year:04d}-{month:02d}"


@pytest.mark.parametrize("month", sorted(_CN))
def test_chinese_month_name_is_a_whole_month_not_a_clarify_loop(month: int) -> None:
    """「五月份」= 5 月 1 日 ~ 5 月 31 日：不许再追问具体日期，也不许静默答成当月。"""
    period = _expected_period(month)
    year, last = int(period[:4]), calendar.monthrange(int(period[:4]), month)[1]
    for text in (f"{_CN[month]}月份的流水", f"{_CN[month]}月花了多少", f"查一下{_CN[month]}月",
                 f"{_CN[month]}月的账单", f"{_CN[month]}月份"):
        assert cli.period_from_text(text) == period, text
        assert cli.dates_from_text(text) == {"date_from": f"{period}-01",
                                            "date_to": f"{period}-{last:02d}"}, text


def test_year_plus_chinese_month_is_that_month_not_the_whole_year() -> None:
    """「2026年五月的流水」→ `2026-05`；此前 `_YEAR_ONLY` 抢先把它认成**整年**。"""
    assert cli.period_from_text("2026年五月的流水") == "2026-05"
    assert cli.dates_from_text("2026年五月的流水")["date_from"] == "2026-05-01"
    assert cli.period_from_text("2026年的账单") == "2026"          # 回归：只给年仍是整年


@pytest.mark.parametrize("text, month", [("正月的流水", 1), ("腊月的流水", 12),
                                         ("冬月的流水", 11), ("元月的账单", 1)])
def test_lunar_colloquial_months_are_recognised(text: str, month: int) -> None:
    """农历口语也认：「正月/元月」= 1 月、「冬月」= 11 月、「腊月」= 12 月。

    这些词**自带「月」字**（「正月月」这种写法永远匹配不上），必须与数词+后缀分列两支。
    """
    assert cli.period_from_text(text) == _expected_period(month), text


@pytest.mark.parametrize("text", ["三个月前的流水", "给我看五笔流水", "这笔钱分三期"])
def test_relative_and_quantifier_phrases_are_not_swallowed_as_a_month(text: str) -> None:
    """防误伤：「三个月**前**」是相对偏移不是三月；「五**笔**流水」的量词五不是五月。

    宁可 downstream 追问，也不许把这类说法静默答成某个月（又是一次看不见的错月）。
    """
    assert cli.dates_from_text(text) == {}, text
    assert cli.period_from_text(text) != "2026-03", text


@pytest.mark.parametrize("text, expected", [
    ("五月一日", {"date_from": "2026-05-01", "date_to": "2026-05-01"}),
    ("五月一日到五月三日", {"date_from": "2026-05-01", "date_to": "2026-05-03"}),
    ("5月1日到5月31日", {"date_from": "2026-05-01", "date_to": "2026-05-31"}),
])
def test_chinese_month_day_is_a_day_not_the_whole_month(text: str, expected: dict) -> None:
    """说了具体日子就得照日子查，不能被整月规则盖住。"""
    assert cli.dates_from_text(text) == expected, text


def test_user_utterance_reaches_the_tool_without_asking_for_dates(seeded) -> None:  # noqa: ANN001
    """端到端：用户原话**直接出结果**，不再卡在「请补充起始日期、结束日期」的追问里。"""
    context = cli.offline_llm()                               # 必须存变量，否则 GeneratorExit 撤销 patch
    context.__enter__()
    try:
        session_memory.reset()
        resp = orchestrator.handle("五月份的流水", session_id="cn-month-flow")
        assert resp.tool_calls and "list_txn" in resp.tool_calls
        assert resp.facts["date_from"] == "2026-05-01"
        assert resp.facts["date_to"] == "2026-05-31"
        assert "起始日期" not in (resp.reply or ""), "还在追问具体日期"
    finally:
        context.__exit__(None, None, None)


def test_user_utterance_without_the_word_flowstill_works(seeded) -> None:  # noqa: ANN001
    """「查一下五月份」连关键词都不带：靠日期槽位兜底，也要问出流水而不是答"没太理解"。"""
    context = cli.offline_llm()
    context.__enter__()
    try:
        session_memory.reset()
        resp = orchestrator.handle("查一下五月份", session_id="cn-month-bare")
        assert resp.tool_calls and "list_txn" in resp.tool_calls
        assert resp.facts["date_from"] == "2026-05-01"
    finally:
        context.__exit__(None, None, None)


def test_recent_months_span_is_unchanged_by_the_cn_month_rules() -> None:
    """回归：加中文月份不能碰坏既有的跨月/相对说法（「最近三个月」仍给足 3 个月）。"""
    assert cli.dates_from_text("最近三个月的流水") == {"date_from": "2026-07-01",
                                                  "date_to": anchor_date().isoformat()}
    assert cli.dates_from_text("上个月") == {"date_from": "2026-08-01", "date_to": "2026-08-31"}
    assert cli.dates_from_text("最近半年的流水")["date_from"] == "2026-04-01"
    assert cli.dates_from_text("最近半个月")["date_from"] != "2026-04-01"
