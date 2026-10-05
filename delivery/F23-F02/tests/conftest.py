"""测试共享脚手架。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data import dao
from data.seed import SAVINGS_ID, CREDIT_ID
from tools._query_common import set_current_user

#: 测试用户
USER = "user_001"
ACCOUNT = "acc_savings_001"


def raw(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """执行原始 SQL 查询（使用内存数据库）。"""
    conn = dao._get_conn()
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def balance(db_path: Path, account_id: str) -> int:
    """获取账户余额。"""
    rows = raw(db_path, "SELECT balance FROM account WHERE id = ?", (account_id,))
    return rows[0]["balance"] if rows else 0


def money(cents: int) -> str:
    """整数分 → 展示字符串。"""
    negative = cents < 0
    cents = abs(cents)
    yuan = cents // 100
    fen = cents % 100
    s = f"{yuan:,}.{fen:02d}"
    return f"-{s}" if negative else s


def assert_covered(message: str, facts: dict) -> None:
    """断言消息中的数字都能在 facts 中找到。"""
    import re
    numbers = re.findall(r'\d[\d,]*\.\d{2}', message)
    for num in numbers:
        found = False
        for v in facts.values():
            if isinstance(v, str) and num in v:
                found = True
                break
            if isinstance(v, (int, float)) and money(int(v)) == num:
                found = True
                break
        assert found, f"消息中的数字 {num} 不在 facts 中"


def txns_of(db_path: Path, where: str, params: tuple = ()) -> int:
    """统计流水数量。"""
    rows = raw(db_path, f"SELECT COUNT(*) as c FROM txn WHERE {where}", params)
    return rows[0]["c"] if rows else 0


def count(db_path: Path, table: str) -> int:
    """统计表行数。"""
    rows = raw(db_path, f"SELECT COUNT(*) as c FROM {table}")
    return rows[0]["c"] if rows else 0


@pytest.fixture
def seeded() -> Path:
    """已播种数据库的 fixture。"""
    dao._conn = None  # 重置连接
    dao.seed_data(USER)
    set_current_user(USER)
    # 返回一个虚拟路径（实际使用内存数据库）
    return Path(":memory:")


@pytest.fixture
def blank() -> Path:
    """空数据库 fixture。"""
    # 重新初始化空数据库
    dao._conn = None
    conn = dao._get_conn()
    conn.executescript("""
        DELETE FROM account;
        DELETE FROM txn;
        DELETE FROM subscription;
        DELETE FROM audit_log;
    """)
    set_current_user(USER)
    return Path(":memory:")
