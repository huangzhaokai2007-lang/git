"""任务卡 01 单测：建库、DDL 一致性、外键、事务、reset。

规格来源：docs/01-接口规格.md 第 1 节（DDL）与第 2 节前的金额约定（一律整数分）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from data.db import (SchemaDriftError, TABLES, connect, init_db, reference_columns, reset_db, schema_drift,
                     schema_sql, statements, transaction)

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "docs" / "01-接口规格.md"

#: 各表字段数量（人工从规格第 1 节 DDL 数出来的独立口径，防止 schema.sql 与测试一起错）
EXPECTED_COLUMN_COUNTS: dict[str, int] = {
    "user": 4,
    "account": 6,
    "txn": 10,
    "card": 9,
    "subscription": 8,
    "wealth_product": 6,
    "holding": 6,
    "payee": 7,
    "audit_log": 12,
    "risk_event": 7,
}

#: 金额类字段：必须是 INTEGER（分），禁止浮点
MONEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "account": ("balance", "available"),
    "txn": ("amount", "balance_after"),
    "card": ("credit_limit", "single_limit", "daily_limit"),
    "subscription": ("amount",),
    "wealth_product": ("min_amount",),
    "holding": ("amount",),
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "bank.db"


@pytest.fixture()
def conn(db_path: Path):
    connection = init_db(db_path)
    yield connection
    connection.close()


def spec_ddl_block() -> list[str]:
    """取回规格第 1 节的 ```sql 代码块（逐行，去掉首尾空白行）。"""
    lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 1."))
    fence = next(i for i in range(start, len(lines)) if lines[i].strip() == "```sql")
    end = next(i for i in range(fence + 1, len(lines)) if lines[i].strip() == "```")
    return [line.rstrip() for line in lines[fence + 1:end]]


def ddl_columns(table: str) -> list[tuple[str, str]]:
    """从 schema.sql 解析出 (字段名, 声明类型) 列表，顺序与 DDL 一致。"""
    sql = re.sub(r"--[^\n]*", "", schema_sql())
    match = re.search(rf"CREATE TABLE\s+\w*\s*{re.escape(table)}\s*\(([^;]*)\)\s*;", sql)
    assert match is not None, f"schema.sql 里找不到表 {table}"
    columns: list[tuple[str, str]] = []
    for part in match.group(1).split(","):
        tokens = part.split()
        assert len(tokens) >= 2, f"{table} 的字段定义不完整：{part!r}"
        columns.append((tokens[0], tokens[1].upper()))
    return columns


def table_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [(row["name"], (row["type"] or "").upper()) for row in rows]


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
        for table in TABLES
    }


def test_all_tables_exist_after_init(conn: sqlite3.Connection) -> None:
    assert table_names(conn) == set(TABLES)
    assert len(TABLES) == 12                    # 卡 14b：+idempotency +rate_limit（规格 §DDL 同步授权）


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMN_COUNTS))
def test_column_count_matches_spec(conn: sqlite3.Connection, table: str) -> None:
    columns = table_columns(conn, table)
    assert len(columns) == EXPECTED_COLUMN_COUNTS[table]
    assert len(columns) == len(ddl_columns(table))


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMN_COUNTS))
def test_columns_match_ddl_names_types_order(conn: sqlite3.Connection, table: str) -> None:
    assert table_columns(conn, table) == ddl_columns(table)


@pytest.mark.parametrize("table", sorted(MONEY_COLUMNS))
def test_money_columns_are_integer_cents(conn: sqlite3.Connection, table: str) -> None:
    types = dict(table_columns(conn, table))
    for column in MONEY_COLUMNS[table]:
        assert types[column] == "INTEGER", f"{table}.{column} 必须是整数分"


def test_schema_sql_is_verbatim_copy_of_spec() -> None:
    written = [line.rstrip() for line in schema_sql().splitlines()]
    assert written == spec_ddl_block()


def test_statements_split_into_twelve_complete_statements() -> None:
    """卡 14b-4（reviewer RISK #4）：表数已是 12（规格 §DDL 加 idempotency / rate_limit），名字同步。"""
    parsed = statements(schema_sql())
    assert len(parsed) == len(TABLES)
    assert all(statement.startswith("CREATE TABLE") for statement in parsed)


def test_foreign_keys_pragma_is_on(conn: sqlite3.Connection, db_path: Path) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    other = connect(db_path)
    try:
        assert other.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        other.close()


def test_foreign_keys_reject_orphan_reference(tmp_path: Path) -> None:
    """外键约束真的生效：自建两张带 FK 的表，插入孤儿行必须报错。"""
    connection = connect(tmp_path / "fk.db")
    try:
        with transaction(connection):
            connection.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE child (id TEXT PRIMARY KEY,"
                " parent_id TEXT NOT NULL REFERENCES parent(id))"
            )
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute("INSERT INTO child VALUES ('c1', 'missing')")
        with transaction(connection):
            connection.execute("INSERT INTO parent VALUES ('p1')")
            connection.execute("INSERT INTO child VALUES ('c1', 'p1')")
        assert connection.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 1
    finally:
        connection.close()


def test_init_db_is_idempotent(conn: sqlite3.Connection, db_path: Path) -> None:
    again = init_db(db_path)
    try:
        assert table_names(again) == set(TABLES)
        assert row_counts(again) == dict.fromkeys(TABLES, 0)
    finally:
        again.close()


def test_reset_db_leaves_all_tables_empty(db_path: Path) -> None:
    connection = init_db(db_path)
    try:
        with transaction(connection):
            connection.execute(
                "INSERT INTO user (id, name, phone, kyc_level) VALUES ('u1','张三','138****0001','L2')"
            )
            connection.execute(
                "INSERT INTO account (id, user_id, type, balance, available, status)"
                " VALUES ('a1','u1','savings',123456,123456,'active')"
            )
            connection.execute(
                "INSERT INTO txn (id, account_id, ts, amount, direction, balance_after)"
                " VALUES ('t1','a1','2026-08-01T12:00:00',-100,'out',123356)"
            )
        assert row_counts(connection)["txn"] == 1
    finally:
        connection.close()

    fresh = reset_db(db_path)
    try:
        assert table_names(fresh) == set(TABLES)
        assert row_counts(fresh) == dict.fromkeys(TABLES, 0)
        assert all(count == 0 for count in row_counts(fresh).values())
    finally:
        fresh.close()


def test_reset_db_drops_tables_unknown_to_schema(db_path: Path) -> None:
    connection = init_db(db_path)
    try:
        with transaction(connection):
            connection.execute("CREATE TABLE scratch (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO scratch VALUES ('x')")
    finally:
        connection.close()
    fresh = reset_db(db_path)
    try:
        assert "scratch" not in table_names(fresh)
    finally:
        fresh.close()


def test_transaction_commits_and_rolls_back(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        for statement in statements(schema_sql()):
            connection.execute(statement)
        with transaction(connection):
            connection.execute(
                "INSERT INTO payee (id, user_id, name, is_whitelist) VALUES ('p1','u1','李四',1)"
            )
        assert connection.execute("SELECT COUNT(*) FROM payee").fetchone()[0] == 1

        with pytest.raises(RuntimeError):
            with transaction(connection):
                connection.execute(
                    "INSERT INTO payee (id, user_id, name, is_whitelist)"
                    " VALUES ('p2','u1','王五',0)"
                )
                raise RuntimeError("写一半炸了")
        assert connection.execute("SELECT COUNT(*) FROM payee").fetchone()[0] == 1
    finally:
        connection.close()


def test_connect_does_not_create_schema(db_path: Path) -> None:
    """connect 只管连，不建表——建表是 init_db 的职责。"""
    connection = connect(db_path)
    try:
        assert table_names(connection) == set()
    finally:
        connection.close()


# ---------------- 卡 16b：旧库结构漂移与自检 ----------------

def test_init_db_repairs_a_partially_stale_database(db_path: Path) -> None:
    """**部分过期**的库（少了卡 14b 的 idempotency / rate_limit）必须被补齐，而不是崩。

    修前的实现只判断"有没有表缺"，缺了就对**全量** DDL 无条件执行 → `table user already exists`。
    这条就是卡 16 现场踩到的那个坑的回归。
    """
    stale = db_path
    connection = connect(stale)
    kept_tables = [name for name in TABLES if name not in ("idempotency", "rate_limit")]
    with transaction(connection):
        for statement in statements(schema_sql()):
            if re.search(r"CREATE TABLE\s+(\w+)", statement).group(1) in kept_tables:
                connection.execute(statement)
    connection.close()

    repaired = init_db(stale)
    try:
        assert table_names(repaired) == set(TABLES)
        assert schema_drift(repaired) == {}
    finally:
        repaired.close()


def test_init_db_reports_schema_drift_with_an_actionable_hint(db_path: Path) -> None:
    """缺列（表在但列不全）无法安全补建 → 报 `SchemaDriftError`，消息里带上重建命令。"""
    connection = init_db(db_path)
    with transaction(connection):
        connection.execute("ALTER TABLE audit_log DROP COLUMN error_code")
    connection.close()

    assert schema_drift(connect(db_path)) == {"audit_log": ["error_code"]}
    with pytest.raises(SchemaDriftError, match="audit_log") as caught:
        init_db(db_path)
    assert "data.seed --reset" in str(caught.value)


def test_schema_drift_is_empty_for_a_fresh_database(conn: sqlite3.Connection) -> None:
    """负向对照：刚建好的库必须零漂移（防"自检永远报警"这种假红）。"""
    assert schema_drift(conn) == {}
    assert set(reference_columns()) == set(TABLES)
