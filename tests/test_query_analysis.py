"""任务卡 04 单测（拆分后）：T3–T4 账单分析与异常检测（`tools/query.py` + `tools/_query_analysis.py`）。

异常规则的口径常量与独立复算见用例注释；共享脚手架在 conftest.py（卡 04b 拆分）。
卡 05b 把分析内核拆到 `tools/_query_analysis.py` 后，内核用例（账期换算 / 占比 / 环比）直接打内核。"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from data import dao
from tools import _query_analysis, query

from tests.conftest import (ACCOUNT, raw, money, assert_covered, baseline_raw)


def test_analyze_spending_groups_match_raw_sql_and_pct_sums_to_100(seeded: Path) -> None:
    result = query.analyze_spending("2026-03")
    expect = {row["category"]: -row["total"] for row in raw(
        seeded, "SELECT category, SUM(amount) AS total FROM txn WHERE substr(ts, 1, 7) = '2026-03'"
                " AND amount < 0 GROUP BY category")}
    assert {group["key"]: group["amount"] for group in result.data["groups"]} == expect
    assert result.data["total"] == sum(expect.values()) == result.facts["total"]
    assert sum(group["pct"] for group in result.data["groups"]) == 100
    assert [group["amount"] for group in result.data["groups"]] == sorted(expect.values(), reverse=True)
    assert_covered(result.message, result.facts)


def test_analyze_spending_channel_split_keeps_the_same_total(seeded: Path) -> None:
    by_category = query.analyze_spending("2026-08")
    by_channel = query.analyze_spending("2026-08", group_by="channel")
    assert by_channel.data["total"] == by_category.data["total"]
    assert {group["key"] for group in by_channel.data["groups"]} == {
        row["channel"] or "未分类" for row in raw(
            seeded, "SELECT DISTINCT channel FROM txn WHERE substr(ts, 1, 7) = '2026-08' AND amount < 0")}
    assert sum(group["pct"] for group in by_channel.data["groups"]) == 100


def test_analyze_spending_year_period_covers_only_that_year(seeded: Path) -> None:
    result = query.analyze_spending("2026", group_by="channel")
    expect = raw(seeded, "SELECT SUM(-amount) AS total, COUNT(*) AS n FROM txn"
                         " WHERE amount < 0 AND ts >= '2026-01-01' AND ts < '2027-01-01'")[0]
    assert result.data["total"] == expect["total"] > 0
    assert result.facts["group_count"] == len(result.data["groups"]) == 4      # 卡/转账/二维码/代扣


def test_analyze_spending_vs_prev_pct_is_recomputed_independently(seeded: Path) -> None:
    def total_of(prefix: str) -> int:
        return raw(seeded, "SELECT SUM(-amount) AS total FROM txn WHERE amount < 0 AND substr(ts, 1, 7) = ?",
                   (prefix,))[0]["total"]

    current, previous = total_of("2026-03"), total_of("2026-02")
    expected = round((current - previous) * 100 / previous)
    result = query.analyze_spending("2026-03")
    assert result.data["vs_prev_pct"] == expected
    assert result.facts["prev_total"] == previous


def test_analyze_spending_has_no_baseline_for_the_first_data_month(seeded: Path) -> None:
    """2025-09 是数据集首月，上期（2025-08）无支出 → vs_prev_pct 必须是 None（不是 0）。"""
    result = query.analyze_spending("2025-09")
    assert result.data["total"] > 0 and result.data["vs_prev_pct"] is None
    assert result.facts["prev_total"] == 0
    assert_covered(result.message, result.facts)


@pytest.mark.parametrize("period", ["2027-01", "2030", "1999-12"])
def test_analyze_spending_empty_period_is_ok_with_zero(seeded: Path, period: str) -> None:
    result = query.analyze_spending(period)
    assert result.ok and result.data == {"groups": [], "total": 0, "vs_prev_pct": None}
    assert result.facts["total"] == 0 and not re.search(r"\d", result.message)


@pytest.mark.parametrize("period,group_by", [
    ("2026-13", "category"), ("2026-3", "category"), ("去年", "category"), ("", "category"),
    (None, "category"), ("2026-03", "merchant"), ("2026-03", "CATEGORY"),
])
def test_analyze_spending_rejects_illegal_arguments(seeded: Path, period: object, group_by: object) -> None:
    result = query.analyze_spending(period, group_by)             # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


def test_pct_split_always_sums_to_100_even_for_awkward_weights() -> None:
    cases = [[1, 1, 1], [1, 0, 0], [5, 3, 2, 1], [100000, 1, 1, 1, 1, 1, 1], [7] * 13]
    for weights in cases:
        split = _query_analysis._split_pct(weights, sum(weights))
        assert sum(split) == _query_analysis.PCT_TOTAL
        assert all(isinstance(value, int) and value >= 0 for value in split)
    assert _query_analysis._split_pct([1, 1], 0) == [0, 0]


def test_previous_period_walks_month_and_year_boundaries() -> None:
    assert _query_analysis._previous_period("2026-01") == "2025-12"
    assert _query_analysis._previous_period("2026-09") == "2026-08"
    assert _query_analysis._previous_period("2026") == "2025"


def test_period_bounds_reject_illegal_periods() -> None:
    """账期换算在分析内核里：非法账期 → `ToolError(INVALID_ARGUMENT)`。"""
    for bad in ("2026-13", "2026-00", "去年", ""):
        with pytest.raises(_query_analysis.ToolError):
            _query_analysis._period_bounds(bad)
    assert _query_analysis._period_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))


def test_weekly_window_splits_across_months() -> None:
    """`_month_windows` 按月切窗（DAO 单次翻页 500 行上限，靠分窗绕开截断）。"""
    windows = _query_analysis._month_windows(date(2025, 12, 20), date(2026, 2, 3))
    assert windows == [("2025-12-20", "2025-12-31"), ("2026-01-01", "2026-01-31"),
                       ("2026-02-01", "2026-02-03")]


def test_all_six_planted_anomalies_are_detected(seeded: Path) -> None:
    """数据层植入的 6 条异常（seed.ANOMALIES）必须全部被检出，且规则归类正确。"""
    planted = {row["id"]: row for row in raw(
        seeded, "SELECT id, ts, counterparty FROM txn WHERE counterparty IN"
                " ('星域数码专营店', '深圳汇通数码专营店', '快闪便利店')")}
    assert len(planted) == 6
    found: dict[str, str] = {}
    for period in ("2026-03", "2026-05", "2026-06", "2026-07"):
        for item in query.detect_anomalies(period).data["items"]:
            found[item["txn_id"]] = item["reason"]
    assert set(planted) <= set(found)
    for txn_id, row in planted.items():
        if row["counterparty"] == "快闪便利店":
            assert "同商户短时密集交易" in found[txn_id]
        if row["counterparty"] == "星域数码专营店":
            assert "凌晨时段交易" in found[txn_id]


def test_verdict_is_independent_of_the_period_look(seeded: Path) -> None:
    """"每笔各自的近 90 天"口径与账期无关：按月看与按年看，同一批交易的判定必须一致。"""
    month = {item["txn_id"] for item in query.detect_anomalies("2026-03").data["items"]}
    year = {item["txn_id"] for item in query.detect_anomalies("2026").data["items"]}
    march = {row["id"] for row in raw(seeded, "SELECT id FROM txn WHERE substr(ts, 1, 7) = '2026-03'")}
    assert month == year & march != set()


def test_amount_rule_is_strictly_greater_than_three_times_the_baseline(seeded: Path) -> None:
    """阈值边界：恰好 3 倍不算，3 倍多 1 分才算（口径来源：卡 04 第 3 条，测试里写字面量不自证）。

    注：卡 06b 起 T4 多了「陌生商户」规则，这两个商户名是新的 → 必定命中那条；
    故这里只钉**金额规则**本身在不在 reason 里（`in` 而非 `==`），别的规则照旧放过。
    """
    moment = "2026-08-20T12:00:00"
    baseline = baseline_raw(seeded, moment)
    assert baseline > 0
    dao.insert_txn(ACCOUNT, moment, -(baseline * 3), "out", 1,
                   counterparty="边界商户甲", id="txn-bound-at")
    dao.insert_txn(ACCOUNT, moment, -(baseline * 3 + 1), "out", 1,
                   counterparty="边界商户乙", id="txn-bound-over")
    reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert "金额显著高于近期均值" not in reasons["txn-bound-at"]                  # 恰好 3 倍不算
    assert "金额显著高于近期均值" in reasons["txn-bound-over"]                   # 3 倍多 1 分才命中
    assert query.AMOUNT_RATIO_THRESHOLD == 3 and query.BASELINE_DAYS == 90      # 阈值常量与卡 04 一致


def test_night_rule_hour_boundaries(seeded: Path) -> None:
    stamps = ("2026-08-20T22:59:00", "2026-08-20T23:00:00", "2026-08-21T05:59:00", "2026-08-21T06:00:00")
    for index, stamp in enumerate(stamps):
        dao.insert_txn(ACCOUNT, stamp, -100, "out", 1, counterparty=f"时段商户{index}", id=f"txn-n-{index}")
    reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert "凌晨时段交易" not in reasons.get("txn-n-0", "") and "凌晨时段交易" not in reasons.get("txn-n-3", "")
    assert "凌晨时段交易" in reasons["txn-n-1"] and "凌晨时段交易" in reasons["txn-n-2"]


def test_velocity_rule_needs_three_payments_to_one_merchant_within_an_hour(seeded: Path) -> None:
    for index, at in enumerate(("10:00:00", "10:30:00", "10:59:00")):
        dao.insert_txn(ACCOUNT, f"2026-08-20T{at}", -100, "out", 1, counterparty="高频商户", id=f"txn-v-{index}")
    for index, at in enumerate(("14:00:00", "14:05:00")):
        dao.insert_txn(ACCOUNT, f"2026-08-20T{at}", -100, "out", 1, counterparty="安静商户", id=f"txn-q-{index}")
    reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert all("同商户短时密集交易" in reasons[f"txn-v-{index}"] for index in range(3))
    assert "同商户短时密集交易" not in reasons.get("txn-q-0", "")
    assert "同商户短时密集交易" not in reasons.get("txn-q-1", "")


def test_severity_follows_the_number_of_rules_hit(seeded: Path) -> None:
    items = {item["txn_id"]: item for item in query.detect_anomalies("2026-03").data["items"]}
    night_and_big = raw(seeded, "SELECT id FROM txn WHERE ts = '2026-03-14T02:13:00'")[0]["id"]
    fixed_repayment = raw(seeded, "SELECT id FROM txn WHERE counterparty = '信用卡还款'"
                                 " AND substr(ts, 1, 7) = '2026-03'")[0]["id"]
    assert items[night_and_big]["severity"] == "high" and "凌晨" in items[night_and_big]["reason"]
    assert items[fixed_repayment]["severity"] == "medium"


def test_amount_rule_also_flags_the_fixed_monthly_repayment_known_consequence(seeded: Path) -> None:
    """已知后果（口径取自卡 04 第 3 条，未自行加规则）：每月固定信用卡还款金额远高于日常支出均值，
    会命中"金额偏离"。是否按 category 排除固定周期性交易需人类定口径 —— 交付说明里已登记。

    注：卡 06b 起只统计**金额规则**命中数（此前用「命中集合」计数，会把 06b 新增的陌生商户命中算进来）。
    """
    periods = [f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 13)]
    hits = 0
    for period in periods:
        reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies(period).data["items"]}
        repayments = {row["id"] for row in raw(
            seeded, "SELECT id FROM txn WHERE counterparty = '信用卡还款' AND substr(ts, 1, 7) = ?", (period,))}
        hits += sum(1 for txn_id in repayments if "金额显著高于近期均值" in reasons.get(txn_id, ""))
    assert hits == 12


# ---------------- T4 第 4 条：陌生商户（规格 §2 T4 备注，SPEC-CHANGE b84ac38） ----------------

def test_new_merchant_rule_fires_for_a_first_time_counterparty(seeded: Path) -> None:
    """过去 90 天该 counterparty 无交易 → 命中「陌生商户」（口径里的 90 天写字面量，不复用被测常量）。"""
    dao.insert_txn(ACCOUNT, "2026-08-20T12:00:00", -100, "out", 1,
                   counterparty="从未见过的商户", id="txn-new-1")
    result = query.detect_anomalies("2026-08")
    reasons = {item["txn_id"]: item["reason"] for item in result.data["items"]}
    assert "陌生商户交易" in reasons["txn-new-1"]
    assert result.facts["new_merchant_days"] == 90


def test_new_merchant_rule_does_not_fire_for_a_repeat_counterparty(seeded: Path) -> None:
    """同一商户 15 天内已交易过 → 第二笔不算陌生商户（正反两面都钉住，防空实现）。"""
    dao.insert_txn(ACCOUNT, "2026-08-05T12:00:00", -100, "out", 1, counterparty="常客商户", id="txn-repeat-1")
    dao.insert_txn(ACCOUNT, "2026-08-20T12:00:00", -100, "out", 1, counterparty="常客商户", id="txn-repeat-2")
    reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert "陌生商户交易" in reasons["txn-repeat-1"]                     # 第一笔确实是陌生
    assert "陌生商户交易" not in reasons.get("txn-repeat-2", "")         # 第二笔不是（未命中任何规则就不在 items 里）


def test_new_merchant_rule_skips_rows_without_a_counterparty(seeded: Path) -> None:
    """没有对手方名的流水（工资/内部划转）不参与陌生商户判定。"""
    dao.insert_txn(ACCOUNT, "2026-08-21T12:00:00", -100, "out", 1, id="txn-no-counterparty")
    reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert "陌生商户交易" not in reasons.get("txn-no-counterparty", "")


def test_new_merchant_rule_uses_the_baseline_pool_not_the_period(seeded: Path) -> None:
    """基准池覆盖「分析期起点往前 90 天」：4 月 1 日的交易，会让分析期 2026-06 的交易不再算陌生。"""
    dao.insert_txn(ACCOUNT, "2026-04-01T12:00:00", -100, "out", 1, counterparty="老客户商户", id="txn-pool-1")
    dao.insert_txn(ACCOUNT, "2026-06-20T12:00:00", -100, "out", 1, counterparty="老客户商户", id="txn-pool-2")
    reasons = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-06").data["items"]}
    assert "陌生商户交易" not in reasons.get("txn-pool-2", "")           # 80 天前交易过 → 不是陌生


def test_anomaly_reasons_never_contain_digits(seeded: Path) -> None:
    for period in ("2026-03", "2026-06", "2026", "2025-09"):
        result = query.detect_anomalies(period)
        for item in result.data["items"]:
            assert not re.search(r"\d", item["reason"]), item
        assert_covered(result.message, result.facts)
        assert result.facts["anomaly_count"] == len(result.data["items"])


def test_detect_anomalies_on_empty_period_and_blank_database(seeded: Path, blank: Path) -> None:
    empty = query.detect_anomalies("2027-05")
    assert empty.ok and empty.data == {"items": []} and empty.facts["scanned_count"] == 0
    assert not re.search(r"\d", empty.message)
    fresh = query.detect_anomalies("2026-03")
    assert fresh.ok and fresh.data == {"items": []} and fresh.facts["anomaly_count"] == 0


@pytest.mark.parametrize("period", ["2026-13", "2026-00", "去年", "", None, 2026])
def test_detect_anomalies_rejects_illegal_period(seeded: Path, period: object) -> None:
    result = query.detect_anomalies(period)                      # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


def test_every_anomaly_carries_its_own_baseline_in_facts(seeded: Path) -> None:
    result = query.detect_anomalies("2026-06")
    assert result.data["items"]
    for item, fact in zip(result.data["items"], result.facts["items"]):
        assert fact["txn_id"] == item["txn_id"] and isinstance(fact["baseline_mean"], int)
        assert fact["baseline_mean_yuan"] == money(fact["baseline_mean"])
    assert result.facts["amount_ratio_threshold"] == 3 and result.facts["baseline_days"] == 90
