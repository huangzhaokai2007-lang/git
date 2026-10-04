"""数据访问对象（DAO）。

模拟实现，用于测试和演示。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MAX_LIMIT = 500

#: 内存数据库连接（单例）
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(":memory:", check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _setup_db(_conn)
    return _conn


def _setup_db(conn: sqlite3.Connection) -> None:
    """初始化数据库表结构。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0,
            available INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS txn (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            amount INTEGER NOT NULL,
            direction TEXT NOT NULL,
            counterparty TEXT,
            category TEXT,
            channel TEXT,
            memo TEXT,
            balance_after INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS subscription (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            merchant TEXT NOT NULL,
            cycle TEXT NOT NULL,
            next_charge_date TEXT,
            amount INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            intent TEXT NOT NULL,
            tool TEXT,
            params_json TEXT,
            risk_level TEXT,
            permission_tier TEXT,
            result TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)


def get_balance(account_type: str) -> dict | None:
    """按账户类型获取余额。"""
    conn = _get_conn()
    cur = conn.execute(
        "SELECT * FROM account WHERE type = ? LIMIT 1",
        (account_type,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def list_account_ids(user_id: str) -> list[str]:
    """列出用户的账户 ID。"""
    conn = _get_conn()
    cur = conn.execute(
        "SELECT id FROM account WHERE user_id = ?",
        (user_id,),
    )
    return [row["id"] for row in cur.fetchall()]


def list_txns_by_accounts(
    account_ids: list[str], date_from: str, date_to: str
) -> list[dict]:
    """按账户列表和日期范围查询流水。"""
    if not account_ids:
        return []

    conn = _get_conn()
    placeholders = ",".join("?" * len(account_ids))
    # date_to 包含当天，所以用 < date_to + 1 day
    cur = conn.execute(
        f"""
        SELECT * FROM txn
        WHERE account_id IN ({placeholders})
          AND ts >= ? AND ts < date(?, '+1 day')
        ORDER BY ts DESC, id DESC
        """,
        (*account_ids, date_from, date_to),
    )
    return [dict(row) for row in cur.fetchall()]


def list_subscriptions(user_id: str, status: str) -> list[dict]:
    """列出用户的订阅。"""
    conn = _get_conn()
    cur = conn.execute(
        "SELECT * FROM subscription WHERE user_id = ? AND status = ?",
        (user_id, status),
    )
    return [dict(row) for row in cur.fetchall()]


def insert_audit(
    trace_id: str,
    session_id: str,
    actor: str,
    intent: str,
    tool: str | None,
    params_json: dict,
    risk_level: str,
    permission_tier: str,
    result: str,
    error_code: str | None = None,
) -> None:
    """写入审计日志。"""
    conn = _get_conn()
    import json

    conn.execute(
        """
        INSERT INTO audit_log
        (trace_id, session_id, actor, intent, tool, params_json,
         risk_level, permission_tier, result, error_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trace_id,
            session_id,
            actor,
            intent,
            tool,
            json.dumps(params_json, ensure_ascii=False, default=str),
            risk_level,
            permission_tier,
            result,
            error_code,
        ),
    )
    conn.commit()


def seed_data(user_id: str = "user_001") -> None:
    """插入测试数据。"""
    conn = _get_conn()

    # 清空旧数据
    conn.executescript("""
        DELETE FROM account;
        DELETE FROM txn;
        DELETE FROM subscription;
        DELETE FROM audit_log;
    """)

    # 插入账户
    conn.execute(
        "INSERT INTO account (id, user_id, type, balance, available) VALUES (?, ?, ?, ?, ?)",
        ("acc_savings_001", user_id, "savings", 4663400, 4663400),
    )
    conn.execute(
        "INSERT INTO account (id, user_id, type, balance, available) VALUES (?, ?, ?, ?, ?)",
        ("acc_credit_001", user_id, "credit", -1234500, 8765500),
    )

    # 插入流水（模拟 600 条）
    import random
    from datetime import datetime, timedelta

    categories = ["餐饮", "交通", "购物", "娱乐", "医疗", "教育", "住房", "其他"]
    channels = ["二维码", "网银", "POS", "APP", "ATM"]
    merchants = ["美团", "滴滴", "京东", "淘宝", "医院", "学校", "房东", "超市"]

    base_date = datetime(2025, 9, 1)
    txns = []
    for i in range(600):
        day_offset = i % 365
        ts = (base_date + timedelta(days=day_offset, hours=i % 24)).isoformat()
        amount = random.choice([-random.randint(100, 50000), random.randint(1000, 20000)])
        category = categories[i % len(categories)]
        channel = channels[i % len(channels)]
        merchant = merchants[i % len(merchants)]
        txns.append(
            (
                f"txn_{i:04d}",
                "acc_savings_001" if i % 10 != 0 else "acc_credit_001",
                ts,
                amount,
                "out" if amount < 0 else "in",
                merchant,
                category,
                channel,
                None,
                0,
            )
        )

    conn.executemany(
        """
        INSERT INTO txn (id, account_id, ts, amount, direction, counterparty, category, channel, memo, balance_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        txns,
    )

    # 插入订阅
    conn.execute(
        "INSERT INTO subscription (id, user_id, merchant, cycle, next_charge_date, amount, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sub_001", user_id, "网易云音乐", "月", "2026-10-15", 1500, "active"),
    )
    conn.execute(
        "INSERT INTO subscription (id, user_id, merchant, cycle, next_charge_date, amount, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sub_002", user_id, "爱奇艺", "月", "2026-10-20", 2500, "active"),
    )

    conn.commit()
