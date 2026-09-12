"""任务卡 04 单测：T1–T5（`tools/query.py`）与入参模型（`tools/schemas.py`）。

三类用例：正常 / 边界（空结果、跨月、年度账期、规则阈值边界）/ 非法参数。另加四组红线断言：
1. **事实包完整性**（幻觉红线）：回执 message 与报告 markdown 里的每个数字都必须出现在 facts 中；
   日期类 token 另有一条单独断言（必须能在 facts 里找到同形字符串）。
2. **禁浮点**：data / facts 递归不得出现 float（金额一律整数分）。
3. **越权（规格第 6 节 L2）**：他人账户/流水/订阅不得泄漏；越权资源 → FORBIDDEN 且 data/facts 为空。
4. **错误消息不含数字**：参数回显会污染数字校验器（`guard/facts_check.py`，卡 13）。

断言尽量用**独立口径**复核：金额、条数、均值、百分比能对上的地方直接与原生 SQL 或复算比对，
不复用 `tools/query.py` 的算法（避免自证）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from data import dao
from data.db import connect, transaction
from data.seed import CREDIT_ID, SAVINGS_ID, USER_ID, generate
from tools import query, schemas

ACCOUNT = SAVINGS_ID
FOREIGN_USER, FOREIGN_ACCOUNT = "u_mallory_0002", "acc_aaa_foreign"   # id 更小 → DAO 会优先返回它
FOREIGN_TXN, FOREIGN_SUB, FOREIGN_CARD = "txn-x-0001", "sub_x_0001", "card_x_0001"
FOREIGN_AMOUNT = 999_900


# ---------------- fixtures ----------------

@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    path = tmp_path / "bank.db"
    generate(path)
    dao.connect_db(path)
    query.set_current_user(None)
    yield path
    query.set_current_user(None)
    dao.close()


@pytest.fixture()
def blank(tmp_path: Path) -> Path:
    path = tmp_path / "blank.db"
    dao.connect_db(path)
    query.set_current_user(None)
    yield path
    query.set_current_user(None)
    dao.close()


@pytest.fixture()
def foreign(seeded: Path) -> Path:
    """在张三的库上再插入一个用户（mallory）的账户/流水/卡/订阅：账户 id 排在最前，试探越权。"""
    conn = connect(seeded)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO user (id, name, phone, kyc_level) VALUES (?, ?, ?, ?)",
                         (FOREIGN_USER, "马洛里", "137****9002", "L1"))
            conn.execute("INSERT INTO account (id, user_id, type, balance, available, status)"
                         " VALUES (?, ?, 'savings', ?, ?, 'active')",
                         (FOREIGN_ACCOUNT, FOREIGN_USER, FOREIGN_AMOUNT, FOREIGN_AMOUNT))
            conn.execute("INSERT INTO txn (id, account_id, ts, amount, direction, counterparty,"
                         " category, channel, memo, balance_after) VALUES (?, ?, ?, ?, 'out', ?, ?, ?, ?, ?)",
                         (FOREIGN_TXN, FOREIGN_ACCOUNT, "2026-08-10T10:00:00", -FOREIGN_AMOUNT,
                          "他人的商户", "餐饮", "卡", "他人的备注", 0))
            conn.execute("INSERT INTO card (id, user_id, account_id, card_no_mask, type,"
                         " single_limit, daily_limit, status) VALUES (?, ?, ?, ?, 'savings', ?, ?, ?)",
                         (FOREIGN_CARD, FOREIGN_USER, FOREIGN_ACCOUNT, "6222 **** **** 9999", 1, 1, "normal"))
            conn.execute("INSERT INTO subscription (id, user_id, merchant, amount, cycle,"
                         " next_charge_date, status) VALUES (?, ?, ?, ?, 'monthly', ?, 'active')",
                         (FOREIGN_SUB, FOREIGN_USER, "他人的会员", 9_900, "2026-09-30"))
    finally:
        conn.close()
    return seeded


# ---------------- 独立口径的辅助 ----------------

def raw(path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """独立直查（不经过 DAO、不经过工具层）。"""
    conn = connect(path)
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def money(cents: int) -> str:
    """独立复算的"分 → 元"（纯整数），用于核对 facts 的展示串。"""
    whole, frac = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{whole:,}.{frac:02d}"


_DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?")
_NUMBER = re.compile(r"\d[\d,\.]*")


def numbers(text: str) -> set[str]:
    """文本里的数字，归一化为纯数字串（剥掉千分位/小数点/百分号/元；日期时间先摘掉）。"""
    return {re.sub(r"\D", "", token) for token in _NUMBER.findall(_DATEISH.sub(" ", text))}


def facts_numbers(facts: dict) -> set[str]:
    """facts 里所有数字（递归、含 *_yuan 展示串与嵌套结构）的归一化数字串。"""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found.add(str(abs(node)))
        elif isinstance(node, str):
            found.update(numbers(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(facts)
    return found


def assert_covered(text: str, facts: dict) -> None:
    """红线 1：text 里的每个数字都必须在 facts 里找得到。"""
    missing = numbers(text) - facts_numbers(facts)
    assert not missing, f"这些数字不在 facts 里（幻觉红线）：{sorted(missing)}｜文本：{text[:200]}"


def assert_dates_covered(text: str, facts: dict) -> None:
    """日期/时间 token 不计入数字校验，但必须能在 facts 里找到同形字符串。"""
    haystack = json.dumps(facts, ensure_ascii=False)
    for token in set(_DATEISH.findall(text)):
        assert token in haystack, f"文本里的日期 {token} 不在 facts 中"


def assert_no_floats(node: object, where: str = "root") -> None:
    """红线 2：任何层级都不许出现 float / bool 冒充数字。"""
    if isinstance(node, bool):
        pytest.fail(f"{where} 出现 bool（金额必须整数分）")
    if isinstance(node, float):
        pytest.fail(f"{where} 出现浮点：{node!r}")
    if isinstance(node, dict):
        for key, value in node.items():
            assert_no_floats(value, f"{where}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            assert_no_floats(value, f"{where}[{index}]")


def baseline_raw(path: Path, moment: str) -> int:
    """独立复算"该笔往前 90 天（左闭右开、不含本笔）支出均值"的整数地板值。

    口径里的 90 与 3 刻意**写成字面量**（来源：卡 04 第 3 条"近 90 天均值 3 倍"）：
    若复用被测常量，改坏阈值时测试会跟着变，等于自证。
    """
    floor = (datetime.fromisoformat(moment) - timedelta(days=90)).isoformat()
    outs = [-row["amount"] for row in raw(
        path, "SELECT amount FROM txn WHERE amount < 0 AND ts >= ? AND ts < ?", (floor, moment))]
    return sum(outs) // len(outs) if outs else 0


def txns_of(path: Path, where: str, params: tuple = ()) -> int:
    return raw(path, f"SELECT COUNT(*) AS n FROM txn WHERE {where}", params)[0]["n"]


# ---------------- 接口冻结（规格第 2 节） ----------------

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


def test_string_enum_error_codes_serialize_as_plain_strings() -> None:
    assert str(schemas.ErrorCode.NOT_FOUND) == "NOT_FOUND"
    failed = query.get_balance("checking")
    assert failed.error_code == "INVALID_ARGUMENT"
    assert json.dumps({"code": failed.error_code}) == '{"code": "INVALID_ARGUMENT"}'


# ---------------- T1 get_balance ----------------

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


# ---------------- T2 list_txn ----------------

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


# ---------------- T3 analyze_spending ----------------

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
        split = query._split_pct(weights, sum(weights))
        assert sum(split) == 100 and all(isinstance(value, int) and value >= 0 for value in split)
    assert query._split_pct([1, 1], 0) == [0, 0]


def test_money_uses_integer_math_only() -> None:
    assert (query._money(0), query._money(5), query._money(100), query._money(-123_456)) == \
        ("0.00", "0.05", "1.00", "-1,234.56")


def test_previous_period_walks_month_and_year_boundaries() -> None:
    assert query._previous_period("2026-01") == "2025-12"
    assert query._previous_period("2026-09") == "2026-08"
    assert query._previous_period("2026") == "2025"


# ---------------- T4 detect_anomalies ----------------

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
    """阈值边界：恰好 3 倍不算，3 倍多 1 分才算（口径来源：卡 04 第 3 条，测试里写字面量不自证）。"""
    moment = "2026-08-20T12:00:00"
    baseline = baseline_raw(seeded, moment)
    assert baseline > 0
    dao.insert_txn(ACCOUNT, moment, -(baseline * 3), "out", 1,
                   counterparty="边界商户甲", id="txn-bound-at")
    dao.insert_txn(ACCOUNT, moment, -(baseline * 3 + 1), "out", 1,
                   counterparty="边界商户乙", id="txn-bound-over")
    found = {item["txn_id"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert "txn-bound-at" not in found and "txn-bound-over" in found
    assert query.AMOUNT_RATIO_THRESHOLD == 3 and query.BASELINE_DAYS == 90      # 阈值常量与卡 04 一致


def test_night_rule_hour_boundaries(seeded: Path) -> None:
    stamps = ("2026-08-20T22:59:00", "2026-08-20T23:00:00", "2026-08-21T05:59:00", "2026-08-21T06:00:00")
    for index, stamp in enumerate(stamps):
        dao.insert_txn(ACCOUNT, stamp, -100, "out", 1, counterparty=f"时段商户{index}", id=f"txn-n-{index}")
    found = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert "txn-n-0" not in found and "txn-n-3" not in found
    assert found["txn-n-1"] == found["txn-n-2"] == "凌晨时段交易"


def test_velocity_rule_needs_three_payments_to_one_merchant_within_an_hour(seeded: Path) -> None:
    for index, at in enumerate(("10:00:00", "10:30:00", "10:59:00")):
        dao.insert_txn(ACCOUNT, f"2026-08-20T{at}", -100, "out", 1, counterparty="高频商户", id=f"txn-v-{index}")
    for index, at in enumerate(("14:00:00", "14:05:00")):
        dao.insert_txn(ACCOUNT, f"2026-08-20T{at}", -100, "out", 1, counterparty="安静商户", id=f"txn-q-{index}")
    found = {item["txn_id"]: item["reason"] for item in query.detect_anomalies("2026-08").data["items"]}
    assert found["txn-v-0"] == found["txn-v-1"] == found["txn-v-2"] == "同商户短时密集交易"
    assert "txn-q-0" not in found and "txn-q-1" not in found


def test_severity_follows_the_number_of_rules_hit(seeded: Path) -> None:
    items = {item["txn_id"]: item for item in query.detect_anomalies("2026-03").data["items"]}
    night_and_big = raw(seeded, "SELECT id FROM txn WHERE ts = '2026-03-14T02:13:00'")[0]["id"]
    fixed_repayment = raw(seeded, "SELECT id FROM txn WHERE counterparty = '信用卡还款'"
                                 " AND substr(ts, 1, 7) = '2026-03'")[0]["id"]
    assert items[night_and_big]["severity"] == "high" and "凌晨" in items[night_and_big]["reason"]
    assert items[fixed_repayment]["severity"] == "medium"


def test_amount_rule_also_flags_the_fixed_monthly_repayment_known_consequence(seeded: Path) -> None:
    """已知后果（口径取自卡 04 第 3 条，未自行加规则）：每月固定信用卡还款金额远高于日常支出均值，
    会命中"金额偏离"。是否按 category 排除固定周期性交易需人类定口径 —— 交付说明里已登记。"""
    periods = [f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 13)]
    hits = 0
    for period in periods:
        ids = {item["txn_id"] for item in query.detect_anomalies(period).data["items"]}
        repayments = {row["id"] for row in raw(
            seeded, "SELECT id FROM txn WHERE counterparty = '信用卡还款' AND substr(ts, 1, 7) = ?", (period,))}
        hits += len(ids & repayments)
    assert hits == 12


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


# ---------------- T5 generate_bill_report ----------------

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


# ---------------- 红线：越权（规格第 6 节 L2） ----------------

def test_get_balance_refuses_a_foreign_account(foreign: Path) -> None:
    """他人账户 id 更小 → DAO 会优先返回它；工具层必须拒绝而不是把余额当成本人的。"""
    result = query.get_balance("savings")
    assert result.ok is False and result.error_code == "FORBIDDEN"
    assert result.data is None and result.facts == {}            # 越权时不泄漏任何数字
    assert query.get_balance("credit").ok                        # 本人另一类账户照常可用


def test_session_user_drives_the_ownership_decision(foreign: Path) -> None:
    query.set_current_user(FOREIGN_USER)
    result = query.get_balance("savings")
    assert result.ok and result.data["balance"] == FOREIGN_AMOUNT
    query.set_current_user(None)
    assert query.get_balance("savings").error_code == "FORBIDDEN"


def test_require_owned_contract_for_cards_and_subscriptions(seeded: Path) -> None:
    """卡 06/07 的资源查询要用的归属断言：本卡先把契约钉住。"""
    with pytest.raises(query.ToolError) as caught:
        query.require_owned("卡片", FOREIGN_USER, FOREIGN_CARD)
    assert caught.value.message == "卡片不属于当前用户"
    query.require_owned("卡片", USER_ID, "card_savings_0001")     # 自己的资源：不抛


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


# ---------------- 红线：全局（facts 覆盖 / 禁浮点 / 错误消息无数字） ----------------

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
