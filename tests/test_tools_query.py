"""任务卡 04 单测（拆分后）：T1–T2 余额与流水（`tools/query.py`）+ `ToolResult` 契约。

三道边界见各用例注释；越权/红线/账单报告的用例分别在 test_query_ownership.py 与
test_query_analysis.py、test_query_report.py（卡 04b 按 300 行上限拆分），共享脚手架在 conftest.py。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from data import dao
from data.db import connect, transaction
from data.seed import CREDIT_ID, SAVINGS_ID
from tools import query, schemas

from tests.conftest import (ACCOUNT, raw, balance, money, assert_covered, txns_of)


def test_tool_result_and_data_keys_are_frozen(seeded: Path) -> None:
    assert set(schemas.ToolResult.model_fields) == {"ok", "data", "error_code", "message", "facts"}
    assert set(query.get_balance("savings").data) == {"balance", "available", "as_of"}
    assert set(query.list_txn("2026-08-01", "2026-08-31", limit=1).data) == {"items", "total_count"}
    assert set(query.analyze_spending("2026-08").data) == {"groups", "total", "vs_prev_pct"}
    assert set(query.detect_anomalies("2026-03").data) == {"items"}
    assert set(query.generate_bill_report("2026-08").data) == {"markdown", "summary_numbers"}
    assert set(query.analyze_spending("2026-08").data["groups"][0]) == {"key", "amount", "pct"}
    assert set(query.detect_anomalies("2026-03").data["items"][0]) == {"txn_id", "reason", "severity"}
    assert schemas.MAX_LIMIT == dao.MAX_LIMIT


def test_get_balance_matches_the_account_row(seeded: Path) -> None:
    result = query.get_balance("savings")
    row = raw(seeded, "SELECT * FROM account WHERE id = ?", (SAVINGS_ID,))[0]
    assert result.ok and result.error_code is None
    assert result.data["balance"] == row["balance"] and result.data["available"] == row["available"]
    assert result.facts["balance"] == row["balance"] and result.facts["balance_yuan"] == money(row["balance"])
    assert result.facts["available_yuan"] == money(row["available"])
    assert datetime.fromisoformat(result.data["as_of"])          # as_of 是合法 ISO 时间戳
    assert_covered(result.message, result.facts)


def test_get_balance_credit_account_can_be_negative(seeded: Path) -> None:
    result = query.get_balance("credit")
    row = raw(seeded, "SELECT * FROM account WHERE id = ?", (CREDIT_ID,))[0]
    assert result.ok and result.data["balance"] == row["balance"] < 0 < result.data["available"]
    assert_covered(result.message, result.facts)


def test_get_balance_on_a_blank_database_is_not_found(blank: Path) -> None:
    result = query.get_balance("savings")
    assert result.ok is False and result.error_code == "NOT_FOUND"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


@pytest.mark.parametrize("bad", ["checking", "SAVINGS", "", None, 0, ["savings"]])
def test_get_balance_rejects_illegal_account_type(seeded: Path, bad: object) -> None:
    result = query.get_balance(bad)                              # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


def test_list_txn_full_range_counts_all_rows_but_limits_the_page(seeded: Path) -> None:
    result = query.list_txn("2025-09-01", "2026-08-31", limit=5)
    assert result.data["total_count"] == txns_of(seeded, "1 = 1") == 600
    assert len(result.data["items"]) == 5
    stamps = [item["ts"] for item in result.data["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert result.facts["total_count"] == 600 and result.facts["returned_count"] == 5
    assert_covered(result.message, result.facts)


def test_list_txn_matches_raw_sql_for_filters(seeded: Path) -> None:
    result = query.list_txn("2026-03-01", "2026-03-31", category="餐饮", min_amount=5_000, limit=500)
    expect = raw(seeded, "SELECT id FROM txn WHERE substr(ts, 1, 7) = '2026-03' AND category = '餐饮'"
                         " AND (amount <= -5000 OR amount >= 5000)")
    assert {item["id"] for item in result.data["items"]} == {row["id"] for row in expect} != set()
    assert result.data["total_count"] == len(expect)
    assert all(item["category"] == "餐饮" and abs(item["amount"]) >= 5_000 for item in result.data["items"])


def test_list_txn_window_includes_the_last_day_and_crosses_months(seeded: Path) -> None:
    last = max(row["ts"][:10] for row in raw(seeded, "SELECT ts FROM txn"))
    single = query.list_txn(last, last, limit=500)
    assert single.data["total_count"] == txns_of(seeded, "substr(ts, 1, 10) = ?", (last,)) > 0
    crossing = query.list_txn("2025-12-28", "2026-01-03", limit=500)
    assert crossing.data["total_count"] == txns_of(seeded, "ts >= '2025-12-28' AND ts < '2026-01-04'") > 0


def test_list_txn_empty_window_is_ok_with_zero_counts(seeded: Path) -> None:
    result = query.list_txn("2020-01-01", "2020-12-31")
    assert result.ok and result.data == {"items": [], "total_count": 0}
    assert result.facts["total_count"] == 0 and not re.search(r"\d", result.message)


def test_list_txn_items_carry_their_numbers_into_facts(seeded: Path) -> None:
    result = query.list_txn("2026-08-01", "2026-08-31", limit=3)
    assert len(result.facts["items"]) == 3
    for item, fact in zip(result.data["items"], result.facts["items"]):
        assert fact["id"] == item["id"] and fact["amount"] == item["amount"]
        assert fact["amount_yuan"] == money(item["amount"])


def test_month_with_more_rows_than_the_page_limit_is_refused(seeded: Path) -> None:
    """单月流水超过 DAO 单次翻页上限（500）时不许悄悄少算：报 TOO_MANY_ROWS，让调用方缩小范围。"""
    floor = query.MAX_LIMIT + 1
    conn = connect(seeded)
    try:
        with transaction(conn):
            conn.executemany(
                "INSERT INTO txn (id, account_id, ts, amount, direction, counterparty, category,"
                " channel, memo, balance_after) VALUES (?, ?, ?, ?, 'out', ?, ?, ?, ?, ?)",
                [(f"txn-bulk-{index:04d}", ACCOUNT, f"2026-09-{index % 28 + 1:02d}T12:00:00", -100,
                  "灌水商户", "餐饮", "二维码", None, 0) for index in range(floor)])
    finally:
        conn.close()
    for name, args in (("list_txn", ("2026-09-01", "2026-09-30")), ("analyze_spending", ("2026-09",))):
        result = getattr(query, name)(*args)
        assert result.ok is False and result.error_code == "TOO_MANY_ROWS"
        assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)
    narrowed = query.list_txn("2026-09-01", "2026-09-15", limit=1)
    assert narrowed.ok and narrowed.data["total_count"] <= query.MAX_LIMIT


@pytest.mark.parametrize("args,kwargs", [
    (("2026-13-01", "2026-12-31"), {}),
    (("2026-03-31", "2026-03-01"), {}),
    (("2026/03/01", "2026-03-31"), {}),
    (("2026-02-30", "2026-03-01"), {}),
    (("2026-03-01", "2026-03-31"), {"category": ""}),
    (("2026-03-01", "2026-03-31"), {"min_amount": -1}),
    (("2026-03-01", "2026-03-31"), {"min_amount": 1.5}),
    (("2026-03-01", "2026-03-31"), {"limit": 0}),
    (("2026-03-01", "2026-03-31"), {"limit": 501}),
    (("2026-03-01", "2026-03-31"), {"limit": True}),
])
def test_list_txn_rejects_illegal_arguments(seeded: Path, args: tuple, kwargs: dict) -> None:
    result = query.list_txn(*args, **kwargs)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)
