"""单测：离线替身的**时间槽位抽取**（`app/cli.period_from_text` / `dates_from_text`）。

为什么补这份：数据集扩到 13 个账期月后，逐月查询暴露出三个"静默答错"，全是**槽位被悄悄改写**
而不是"查不到" —— 回执数字都来自真实事实包，看着完全像对的，只能靠断言钉住：

- **日期上下文污染**：编排层会在话尾附「（当前日期：2026-09-12）」，`dates_from_text` 把它当成
  "用户说了一个完整日期" → 问「2025-09 的流水」查成锚点当天（3 笔），**13 个月全答成同一个月**；
- **答错月份**：只按相对词取 `period`，显式月份 `2025-12` 认不出 → 静默回落当月；
- **跨月说法缩水**：「最近三个月」被"最近"兜底分支吞成近 30 天 → 问三个月答一个月。

沿用仓库做法：断言跑在 `seeded` 合成库上，期望的首末日由本文件**独立复算**（不复用被测实现）。
"""

from __future__ import annotations

import calendar
from datetime import date

import pytest

from agent import orchestrator
from app import cli
from data.seed import AS_OF

#: 数据集的 13 个账期月：首月 = `AS_OF` 往前推 12 个月，逐月递增
PERIODS = [f"{(AS_OF.year * 12 + AS_OF.month - 1 - 12 + i) // 12:04d}"
           f"-{(AS_OF.year * 12 + AS_OF.month - 1 - 12 + i) % 12 + 1:02d}"
           for i in range(13)]


def _last_day(period: str) -> str:
    """独立复算该月的最后一天（不调 `calendar` 之外的被测实现）。"""
    year, month = int(period[:4]), int(period[5:])
    return f"{calendar.monthrange(year, month)[1]:02d}"


@pytest.mark.parametrize("period", PERIODS)
def test_explicit_month_wins_over_the_anchor_period(period: str) -> None:
    """显式月份优先：**问 2025-12 就得答 2025-12**，绝不静默回落锚点当月。"""
    year, month = period[:4], period[5:]
    for text in (f"{period} 花了多少", f"{year}年{int(month)}月的账单"):
        assert cli.period_from_text(text) == period, text


@pytest.mark.parametrize("period", PERIODS)
def test_explicit_month_spreads_into_a_full_month_range(period: str) -> None:
    """整月说法 → 摊成该月首末日（流水查询能跨月，故这里给足整整一个月）。"""
    assert cli.dates_from_text(f"{period} 的流水") == {"date_from": f"{period}-01",
                                                       "date_to": f"{period}-{_last_day(period)}"}


def test_date_context_appended_by_the_orchestrator_never_hijacks_the_range() -> None:
    """编排层附的「（当前日期：2026-09-12）」不许被当成"用户说的单个日期"。

    这条就是"13 个月全答成同一个月"的根因：尾巴里的 `2026-09-12` 命中了「单个日期 → 查当天」分支。
    """
    text = f"2025-09 的流水\n（当前日期：{AS_OF.isoformat()}）"
    assert cli.dates_from_text(text)["date_from"] == "2025-09-01"


def test_bare_month_falls_back_a_year_when_it_points_to_the_future() -> None:
    """「11月」在锚点 9 月指的是**去年** 11 月（不是还没到的今年 11 月）。"""
    year = AS_OF.year - 1 if AS_OF.month < 11 else AS_OF.year
    assert cli.period_from_text("11月的账单") == f"{year}-11"


def test_span_of_months_is_not_swallowed_by_the_recent_fallback(seeded) -> None:  # noqa: ANN001
    """「最近三个月的流水」要给足 3 个月，不能被"最近"兜底分支缩成近 30 天。"""
    expected_from = f"{PERIODS[-3]}-01"                       # 含锚点当月在内的前 3 个账期月
    context = cli.offline_llm()                               # ⚠️ 必须存变量，否则 GeneratorExit 撤销 patch
    context.__enter__()
    try:
        resp = orchestrator.handle("最近三个月的流水", session_id="span-months")
        assert resp.tool_calls and "list_txn" in resp.tool_calls
        assert resp.facts["date_from"] == expected_from
    finally:
        context.__exit__(None, None, None)


def test_recent_days_still_resolves_to_a_day_window() -> None:
    """回归：「最近 7 天」仍是按**天**算（上面那条按月改坏了这里就会挂）。"""
    assert cli.dates_from_text("最近7天的流水") == {
        "date_from": date.fromordinal(AS_OF.toordinal() - 6).isoformat(),
        "date_to": AS_OF.isoformat(),
    }


def test_year_only_resolves_to_the_year_period() -> None:
    """「2026年的账单」→ 年度账期 `YYYY`（工具层原生认），不缩成某一个月。"""
    assert cli.period_from_text("2026年的账单") == "2026"
    assert cli.dates_from_text("2025 年的流水") == {"date_from": "2025-01-01",
                                                     "date_to": "2025-12-31"}


def test_half_year_is_six_months_but_half_a_month_is_not() -> None:
    """「最近半年」= 6 个月；「最近半个月」是 15 天，**绝不能**按 6 个月算。"""
    assert cli.dates_from_text("最近半年的流水")["date_from"] == f"{PERIODS[-6]}-01"
    assert cli.dates_from_text("最近半个月的流水")["date_from"] != f"{PERIODS[-6]}-01"


def test_span_utterance_never_silently_collapses_to_one_month(seeded) -> None:  # noqa: ANN001
    """「最近三个月花了多少」：工具层只吃单账期 → **宁可追问，也不许答成一个月**。

    静默缩水比追问坏得多：数字是真的、措辞是顺的，只有月份是错的，用户无从察觉。
    """
    context = cli.offline_llm()
    context.__enter__()
    try:
        resp = orchestrator.handle("最近三个月花了多少", session_id="span-analysis")
        assert resp.intent == "bill_analysis"
        assert resp.missing_slots == ["period"]               # 认不出 → 追问，不猜
        assert not resp.tool_calls
    finally:
        context.__exit__(None, None, None)
