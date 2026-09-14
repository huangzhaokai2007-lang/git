"""任务卡 04 单测（拆分后）：T5 账单报告 + 越权（规格第 6 节 L2）+ 红线不变量。

红线：事实包完整性、禁浮点、错误消息无数字；共享脚手架在 conftest.py（卡 04b 拆分）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from data.seed import CREDIT_ID, USER_ID
from tools import query

from tests.conftest import (FOREIGN_TXN, FOREIGN_AMOUNT, raw, facts_numbers, assert_covered, assert_dates_covered, assert_no_floats, txns_of)


def test_bill_report_markdown_numbers_all_come_from_facts(seeded: Path) -> None:
    result = query.generate_bill_report("2026-03")
    assert result.ok and result.data["markdown"].startswith("# 月度账单报告 · 2026-03")
    assert_covered(result.data["markdown"], result.facts)
    assert_dates_covered(result.data["markdown"], result.facts)
    assert_covered(result.message, result.facts)
    assert "| 项目 | 金额（元） | 占比 |" in result.data["markdown"]


def test_bill_report_summary_numbers_are_facts_ints(seeded: Path) -> None:
    result = query.generate_bill_report("2026-03")
    summary = result.data["summary_numbers"]
    assert summary and all(result.facts.get(key) == value for key, value in summary.items())
    assert all(isinstance(value, int) and not isinstance(value, bool) for value in summary.values())
    assert summary["out_sum"] == result.facts["out_sum"] > 0
    assert [key for key in ("out_count", "in_count", "net", "prev_total") if key not in summary] == []


def test_bill_report_totals_match_raw_sql(seeded: Path) -> None:
    result = query.generate_bill_report("2026-03")
    row = raw(seeded, "SELECT SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END) AS out_sum,"
                      " SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS in_sum"
                      " FROM txn WHERE substr(ts, 1, 7) = '2026-03'")[0]
    assert result.facts["out_sum"] == row["out_sum"] and result.facts["in_sum"] == row["in_sum"]
    assert result.facts["net"] == row["in_sum"] - row["out_sum"]


def test_bill_report_yearly_kind_and_mismatch(seeded: Path) -> None:
    yearly = query.generate_bill_report("2026", kind="yearly")
    assert yearly.ok and yearly.data["markdown"].startswith("# 年度账单报告 · 2026")
    assert_covered(yearly.data["markdown"], yearly.facts)
    mismatch = query.generate_bill_report("2026-03", kind="yearly")
    assert mismatch.ok is False and mismatch.error_code == "INVALID_ARGUMENT"
    assert mismatch.data is None and mismatch.facts == {} and not re.search(r"\d", mismatch.message)


def test_bill_report_counts_anomalies_consistently_with_detect_anomalies(seeded: Path) -> None:
    report = query.generate_bill_report("2026-06")
    detected = query.detect_anomalies("2026-06")
    assert report.facts["anomaly_count"] == detected.facts["anomaly_count"] == len(detected.data["items"])


def test_bill_report_lists_only_the_users_active_subscriptions(seeded: Path, foreign: Path) -> None:
    result = query.generate_bill_report("2026-08")
    merchants = {sub["merchant"] for sub in result.facts["subscriptions"]}
    assert merchants == {row["merchant"] for row in raw(
        seeded, "SELECT merchant FROM subscription WHERE user_id = ? AND status = 'active'", (USER_ID,))}
    assert "健悦健身" not in merchants and "轻食工坊" not in merchants     # 已取消的订阅不出现
    assert foreign is not None                                            # 越权订阅见下面的用例


def test_bill_report_on_empty_period_is_still_consistent(seeded: Path) -> None:
    result = query.generate_bill_report("2027-01")
    assert result.ok and result.facts["out_sum"] == 0 and result.facts["vs_prev_pct"] is None
    assert_covered(result.data["markdown"], result.facts)
    assert_dates_covered(result.data["markdown"], result.facts)


@pytest.mark.parametrize("period,kind", [
    ("2026-13", "monthly"), ("去年", "monthly"), ("", "monthly"), ("2026", "monthly"),
    ("2026-03", "weekly"), ("2026-03", "MONTHLY"), (None, "monthly"),
])
def test_bill_report_rejects_illegal_arguments(seeded: Path, period: object, kind: object) -> None:
    result = query.generate_bill_report(period, kind)            # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


def test_get_balance_refuses_a_foreign_account(foreign: Path) -> None:
    """他人账户 id 更小 → DAO 会优先返回它；工具层必须拒绝而不是把余额当成本人的。"""
    result = query.get_balance("savings")
    assert result.ok is False and result.error_code == "FORBIDDEN"
    assert result.data is None and result.facts == {}            # 越权时不泄漏任何数字
    assert query.get_balance("credit").ok                        # 本人另一类账户照常可用


def test_foreign_txns_never_leak_into_any_tool(foreign: Path) -> None:
    """他人账户 id 更小 → DAO 不再返回本人的 savings；此时必须 fail-closed：
    只暴露"能确认归属"的信用账户数据（宁少勿漏），他人的账户/流水/订阅一律不出现。"""
    owned = {row["id"] for row in raw(foreign, "SELECT id FROM account WHERE user_id = ?", (USER_ID,))}
    listed = query.list_txn("2026-08-01", "2026-08-31", limit=500)
    assert {item["id"] for item in listed.data["items"]} & {FOREIGN_TXN} == set()
    assert listed.data["total_count"] == txns_of(
        foreign, "substr(ts, 1, 7) = '2026-08' AND account_id = ?", (CREDIT_ID,))
    assert {item["account_id"] for item in listed.data["items"]} <= owned
    spending = query.analyze_spending("2026-08")
    assert spending.data["total"] == raw(
        foreign, "SELECT SUM(-amount) AS total FROM txn WHERE amount < 0 AND substr(ts, 1, 7) = '2026-08'"
                 " AND account_id = ?", (CREDIT_ID,))[0]["total"]
    assert FOREIGN_TXN not in {item["txn_id"] for item in query.detect_anomalies("2026-08").data["items"]}
    report = query.generate_bill_report("2026-08")
    assert "他人的会员" not in report.data["markdown"] and FOREIGN_AMOUNT not in facts_numbers(
        report.facts)


def test_foreign_account_does_not_rescue_the_balance_query(foreign: Path) -> None:
    """fail-closed：他人的账户让本人的 savings 查不出来时，宁可 FORBIDDEN，也不放行他人的数字。"""
    refused = query.get_balance("savings")
    assert refused.ok is False and refused.error_code == "FORBIDDEN" and refused.facts == {}
    listing = query.list_txn("2025-09-01", "2026-08-31", limit=500)
    assert {item["account_id"] for item in listing.data["items"]} == {CREDIT_ID}


CALLS = [
    ("get_balance", ("savings",), {}),
    ("list_txn", ("2026-08-01", "2026-08-31"), {"limit": 50}),
    ("analyze_spending", ("2026-08",), {"group_by": "channel"}),
    ("detect_anomalies", ("2026-06",), {}),
    ("generate_bill_report", ("2026-08",), {}),
]


@pytest.mark.parametrize("name,args,kwargs", CALLS, ids=[call[0] for call in CALLS])
def test_no_floats_anywhere_in_data_or_facts(seeded: Path, name: str, args: tuple, kwargs: dict) -> None:
    result = getattr(query, name)(*args, **kwargs)
    assert result.ok
    assert_no_floats(result.data, "data")
    assert_no_floats(result.facts, "facts")
    json.dumps({"data": result.data, "facts": result.facts}, ensure_ascii=False)


@pytest.mark.parametrize("name,args,kwargs", CALLS, ids=[call[0] for call in CALLS])
def test_reply_message_numbers_are_all_in_facts(seeded: Path, name: str, args: tuple, kwargs: dict) -> None:
    result = getattr(query, name)(*args, **kwargs)
    assert_covered(result.message, result.facts)


ILLEGAL_CALLS = [
    ("get_balance", ("checking",), {}),
    ("list_txn", ("2026-13-01", "2026-12-31"), {}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"limit": 501}),
    ("analyze_spending", ("2026-13",), {}),
    ("detect_anomalies", ("去年",), {}),
    ("generate_bill_report", ("2026-03", "weekly"), {}),
]


@pytest.mark.parametrize("name,args,kwargs", ILLEGAL_CALLS, ids=[f"{c[0]}-{i}" for i, c in enumerate(ILLEGAL_CALLS)])
def test_rejected_calls_return_a_clean_tool_result(seeded: Path, name: str, args: tuple, kwargs: dict) -> None:
    result = getattr(query, name)(*args, **kwargs)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {}
    assert result.message and not re.search(r"\d", result.message)
