"""任务卡 02 单测：合成数据的可复现性、账实一致、植入异常与红线。

口径来源：`docs/01-接口规格.md` 第 1 节（DDL 与字段含义）、第 5 节（金额一律整数分）。
测试用**独立复算**校验生成器：余额由流水累加得出、异常由规则几何复核，不复用生成器的计算结果。
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from data.db import TABLES, connect
from data.seed import (
    ANOMALIES,
    AS_OF,
    CREDIT_LIMIT,
    DATA_END,
    DATA_START,
    MONTHLY_BATCHES,
    MONTHS,
    OPENING_BALANCES,
    SAVINGS_ID,
    SUBSCRIPTIONS,
    TRANSFER_PAYEES,
    USER_ID,
    generate,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {"餐饮", "交通", "电商", "订阅", "转账", "工资", "生活缴费"}
CHANNELS = {"卡", "二维码", "转账", "代扣"}
#: 正常出现过的对手方（测试侧独立复算，不复用生成器的同名常量）
NORMAL = frozenset({m for b in MONTHLY_BATCHES for m in b.merchants} | {s.merchant for s in SUBSCRIPTIONS}
                   | {name for _, name in TRANSFER_PAYEES} | {"示例科技有限公司", "信用卡还款"})
PHONE_MASK = re.compile(r"^\d{3}\*{4}\d{4}$")
LONG_DIGITS = re.compile(r"\d{12,}")
TXN_COUNT = 600                                    # 卡要求"约 600 条"，生成器按 12×49 + 12 精确落 600


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("seed") / "bank.db"
    generate(path)
    return path


@pytest.fixture(scope="module")
def conn(db_path: Path):
    connection = connect(db_path)
    yield connection
    connection.close()


def rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]


def dump(path: Path) -> dict[str, list[tuple]]:
    connection = connect(path)
    try:
        return {t: [tuple(r) for r in connection.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in TABLES}
    finally:
        connection.close()


def minutes(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# ---------- 可复现 / CLI ----------

def test_two_runs_are_row_by_row_identical(tmp_path: Path) -> None:
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    generate(first)
    generate(second)
    assert dump(first) == dump(second)
    assert len(dump(first)["txn"]) == TXN_COUNT


def run_cli(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DB_PATH": str(db)}
    return subprocess.run([sys.executable, "-m", "data.seed", *args], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True)


def test_cli_is_reproducible_across_processes(tmp_path: Path) -> None:
    first, second = tmp_path / "cli_a.db", tmp_path / "cli_b.db"
    for path in (first, second):
        result = run_cli(path, "--reset")
        assert result.returncode == 0, result.stderr
        assert "600" in result.stderr                       # 日志里回报条数
    assert dump(first) == dump(second)


def test_cli_uses_default_db_path_from_env(tmp_path: Path) -> None:
    target = tmp_path / "from_env.db"
    assert run_cli(target, "--reset").returncode == 0
    assert target.exists() and len(dump(target)["txn"]) == TXN_COUNT


def test_generate_refuses_non_empty_db_without_reset(db_path: Path) -> None:
    with pytest.raises(ValueError, match="请先清库"):
        generate(db_path)
    assert len(rows(connect(db_path), "txn")) == TXN_COUNT     # 没有被写坏


def test_generate_reset_overwrites_and_main_returns_two_on_conflict(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "bank.db"            # 边界：父目录自动创建
    assert generate(path)["txn"] == TXN_COUNT
    assert generate(path, reset=True)["txn"] == TXN_COUNT
    assert main(["--db", str(path)]) == 2                      # 非法：非空且没给 --reset


# ---------- 主数据规模 ----------

@pytest.mark.parametrize("table,expected", [("user", 1), ("account", 2), ("txn", TXN_COUNT), ("card", 3),
                                            ("subscription", 6), ("wealth_product", 6), ("holding", 2),
                                            ("payee", 5), ("audit_log", 1), ("risk_event", 0)])
def test_row_counts(conn: sqlite3.Connection, table: str, expected: int) -> None:
    assert len(rows(conn, table)) == expected


def test_user_account_and_card_fixtures(conn: sqlite3.Connection) -> None:
    user = rows(conn, "user")[0]
    assert (user["id"], user["name"], user["phone"], user["kyc_level"]) == (USER_ID, "张三", "138****0001", "L2")
    assert {a["type"] for a in rows(conn, "account")} == {"savings", "credit"}
    cards = rows(conn, "card")
    assert [c["status"] for c in cards].count("lost") == 1
    assert all(re.fullmatch(r"6222( \*{4}){2} \d{4}", c["card_no_mask"]) for c in cards)


def test_payees_have_ambiguous_lisi(conn: sqlite3.Connection) -> None:
    payees = rows(conn, "payee")
    lisi = [p for p in payees if p["name"] == "李四"]
    assert len(lisi) == 2 and lisi[0]["phone"] != lisi[1]["phone"]
    assert all(PHONE_MASK.fullmatch(p["phone"]) for p in payees)
    assert [p["last_used_ts"] is None for p in payees].count(True) == 1     # 只有赵六从未使用


def test_subscriptions_match_card_requirements(conn: sqlite3.Connection) -> None:
    subs = rows(conn, "subscription")
    assert (len(subs), [s["cycle"] for s in subs].count("monthly"), [s["cycle"] for s in subs].count("yearly")) == (6, 5, 1)
    active = [s for s in subs if s["status"] == "active"]
    cancelled = [s for s in subs if s["status"] == "cancelled"]
    assert len(active) == 4 and len(cancelled) == 2
    assert len([s for s in active if s["cycle"] == "monthly"]) == 3
    charges = {s["id"]: s["next_charge_date"] for s in active}
    assert all(AS_OF <= date.fromisoformat(v) <= AS_OF + timedelta(days=30) for v in charges.values())
    assert max(charges.values()) != min(charges.values())                  # 分散在未来 30 天，不是同一天
    assert all(date.fromisoformat(s["next_charge_date"]) < AS_OF for s in cancelled)


def test_subscription_source_txn_points_at_a_real_charge(conn: sqlite3.Connection) -> None:
    txns = {t["id"]: t for t in rows(conn, "txn")}
    for sub in rows(conn, "subscription"):
        source = txns[sub["source_txn_id"]]
        assert source["category"] == "订阅" and source["counterparty"] == sub["merchant"]
        assert source["amount"] == -sub["amount"]


def test_wealth_products_and_holdings(conn: sqlite3.Connection) -> None:
    products = {p["id"]: p for p in rows(conn, "wealth_product")}
    assert len({p["risk_level"] for p in products.values()}) == 5          # R1–R5 全覆盖
    assert all(isinstance(p["min_amount"], int) and p["min_amount"] > 0 for p in products.values())
    holdings = rows(conn, "holding")
    assert len(holdings) == 2 and all(h["product_id"] in products for h in holdings)
    assert all(isinstance(h["amount"], int) and h["user_id"] == USER_ID for h in holdings)


# ---------- 流水：口径与账实一致 ----------

def test_txn_count_months_and_categories(conn: sqlite3.Connection) -> None:
    txns = rows(conn, "txn")
    assert len(txns) == TXN_COUNT
    months = {t["ts"][:7] for t in txns}
    assert months == {f"{y}-{m:02d}" for y, m in ((2025, m) for m in range(9, 13))} | {f"2026-{m:02d}" for m in range(1, 9)}
    assert all(CATEGORIES >= {t["category"]} and CHANNELS >= {t["channel"]} for t in txns)
    assert {t["category"] for t in txns} == CATEGORIES                     # 7 类都出现过
    assert {t["channel"] for t in txns} == CHANNELS
    assert all(t["memo"] for t in txns)


def test_txn_amounts_dates_and_directions_are_legal(conn: sqlite3.Connection) -> None:
    for t in rows(conn, "txn"):
        assert isinstance(t["amount"], int) and t["amount"] != 0            # 整数分，且没有 0 金额
        assert t["direction"] == ("in" if t["amount"] > 0 else "out")
        ts = datetime.fromisoformat(t["ts"])
        assert DATA_START <= ts.date() <= DATA_END


@pytest.mark.parametrize("account_id", sorted(OPENING_BALANCES))
def test_balance_chain_equals_opening_plus_flows(conn: sqlite3.Connection, account_id: str) -> None:
    """卡单测：流水总额 = 账户余额变动合计（期末 - 期初）。"""
    account = next(a for a in rows(conn, "account") if a["id"] == account_id)
    txns = sorted((t for t in rows(conn, "txn") if t["account_id"] == account_id), key=lambda t: (t["ts"], t["id"]))
    running = OPENING_BALANCES[account_id]
    for txn in txns:
        running += txn["amount"]
        assert txn["balance_after"] == running                              # 逐笔账实一致
    assert sum(t["amount"] for t in txns) == account["balance"] - OPENING_BALANCES[account_id]
    assert account["balance"] == running == txns[-1]["balance_after"]
    expected_available = running if account_id == SAVINGS_ID else CREDIT_LIMIT + running
    assert account["available"] == expected_available
    if account_id == SAVINGS_ID:
        assert account["balance"] > 0                                       # 储蓄账户不透支
    else:
        assert account["balance"] < 0 < abs(account["balance"]) < CREDIT_LIMIT   # 信用：余额=未还欠款，且在额度内
    assert account["available"] > 0


# ---------- 植入异常 ----------

def test_six_planted_anomalies_exist_and_are_findable(conn: sqlite3.Connection) -> None:
    txns = rows(conn, "txn")
    by_ts = {t["ts"]: t for t in txns}
    assert {tag for a in ANOMALIES for tag in a.tags} == {"night_large", "stranger_merchant", "velocity", "amount_jump"}
    for anomaly in ANOMALIES:                                              # 每条植入异常都能被某条规则命中
        row = by_ts[anomaly.ts]
        assert (row["amount"], row["category"], row["channel"]) == (anomaly.amount, anomaly.category, anomaly.channel)
        hour = datetime.fromisoformat(row["ts"]).hour
        if "night_large" in anomaly.tags:
            assert hour in {23, 0, 1, 2, 3, 4, 5} and abs(row["amount"]) >= 100_000
        if "stranger_merchant" in anomaly.tags:
            assert row["counterparty"] not in NORMAL
        if "amount_jump" in anomaly.tags:
            trailing = [t for t in txns if t["category"] == row["category"] and t["amount"] < 0
                        and row["ts"] > t["ts"] >= (minutes(row["ts"]) - timedelta(days=90)).isoformat()]
            mean = sum(-t["amount"] for t in trailing) / len(trailing)
            assert abs(row["amount"]) >= 3 * mean
        if "velocity" in anomaly.tags:
            burst = [t for t in txns if t["counterparty"] == row["counterparty"] and t["amount"] < 0
                     and abs((minutes(t["ts"]) - minutes(row["ts"])).total_seconds()) < 3600]
            assert len(burst) >= 3                                     # 同一商户 1 小时内 ≥3 笔


def test_normal_rows_hit_no_night_or_velocity_rule(conn: sqlite3.Connection) -> None:
    """除 6 条植入异常外，正常数据不得踩中"凌晨时段"或"同商户 1 小时 ≥3 笔"。"""
    planted = {a.ts for a in ANOMALIES}
    txns = [t for t in rows(conn, "txn") if t["ts"] not in planted]
    assert not [t for t in txns if datetime.fromisoformat(t["ts"]).hour in {23, 0, 1, 2, 3, 4, 5}]
    grouped: dict[str, list[str]] = defaultdict(list)
    for txn in txns:
        grouped[txn["counterparty"]].append(txn["ts"])
    for counterparty, stamps in grouped.items():
        ordered = sorted(datetime.fromisoformat(s) for s in stamps)
        for i, start in enumerate(ordered):
            assert len([s for s in ordered[i:] if s - start < timedelta(hours=1)]) < 3, counterparty


# ---------- 红线：全合成、无 PII ----------

def test_no_real_pii_or_full_card_numbers(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        for row in rows(conn, table):
            for column, value in row.items():
                if isinstance(value, str):
                    assert not LONG_DIGITS.search(value), f"{table}.{column}={value}"
    assert PHONE_MASK.fullmatch(rows(conn, "user")[0]["phone"])


def test_seed_run_is_audited(conn: sqlite3.Connection) -> None:
    audit = rows(conn, "audit_log")[0]
    assert (audit["actor"], audit["tool"], audit["result"], audit["permission_tier"]) == \
        ("system", "data.seed", "success", "L0")
