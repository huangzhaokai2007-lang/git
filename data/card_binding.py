"""F01 增量数据：凭证、绑定、确认令牌和失败计数。保留核心 DDL。"""
from __future__ import annotations

import sqlite3

DDL = (
    "CREATE TABLE IF NOT EXISTS agent_card_secret ("
    "card_id TEXT PRIMARY KEY REFERENCES card(id) ON DELETE CASCADE, "
    "card_no TEXT NOT NULL UNIQUE, salt TEXT NOT NULL, pin_hash TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agent_card_binding ("
    "user_id TEXT NOT NULL, card_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE, "
    "bound_at REAL NOT NULL, PRIMARY KEY(user_id,card_id))",
    "CREATE TABLE IF NOT EXISTS agent_card_request ("
    "token TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL, "
    "card_id TEXT NOT NULL, expires_at REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS agent_card_attempt ("
    "attempt_key TEXT PRIMARY KEY, failures INTEGER NOT NULL, locked_until REAL NOT NULL)",
)

CARD_SELECT = (
    "SELECT c.*, a.balance, a.user_id AS account_owner FROM card c "
    "JOIN account a ON a.id=c.account_id "
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    for statement in DDL:
        conn.execute(statement)


def credential(conn: sqlite3.Connection, number: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_card_secret WHERE card_no=?", (number,)).fetchone()
    return dict(row) if row else None


def secret_for_card(conn: sqlite3.Connection, card_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_card_secret WHERE card_id=?", (card_id,)).fetchone()
    return dict(row) if row else None


def owned_card(conn: sqlite3.Connection, card_id: str, user_id: str) -> dict | None:
    row = conn.execute(CARD_SELECT + "WHERE c.id=? AND c.user_id=? AND a.user_id=?",
                       (card_id, user_id, user_id)).fetchone()
    return dict(row) if row else None


def is_bound(conn: sqlite3.Connection, card_id: str, user_id: str) -> bool:
    return conn.execute("SELECT 1 FROM agent_card_binding WHERE user_id=? AND card_id=?",
                        (user_id, card_id)).fetchone() is not None


def bound_cards(conn: sqlite3.Connection, user_id: str, status: str | None) -> list[dict]:
    rows = conn.execute(CARD_SELECT + "JOIN agent_card_binding b ON b.card_id=c.id "
                        "WHERE b.user_id=? AND c.user_id=? AND a.user_id=? "
                        "AND (? IS NULL OR c.status=?) ORDER BY c.id",
                        (user_id, user_id, user_id, status, status)).fetchall()
    return [dict(row) for row in rows]


def attempt(conn: sqlite3.Connection, key: str) -> dict:
    row = conn.execute("SELECT * FROM agent_card_attempt WHERE attempt_key=?", (key,)).fetchone()
    return dict(row) if row else {"failures": 0, "locked_until": 0}


def record_attempt(conn: sqlite3.Connection, key: str, failures: int, locked_until: float) -> None:
    conn.execute("INSERT INTO agent_card_attempt VALUES (?,?,?) ON CONFLICT(attempt_key) "
                 "DO UPDATE SET failures=excluded.failures,locked_until=excluded.locked_until",
                 (key, failures, locked_until))


def create_request(conn: sqlite3.Connection, token: str, user_id: str, session_id: str,
                   card_id: str, expires_at: float) -> None:
    conn.execute("DELETE FROM agent_card_request WHERE expires_at<?", (expires_at - 300,))
    conn.execute("INSERT INTO agent_card_request(token,user_id,session_id,card_id,expires_at) "
                 "VALUES (?,?,?,?,?)", (token, user_id, session_id, card_id, expires_at))


def get_request(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_card_request WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def confirm_request(conn: sqlite3.Connection, token: str, user_id: str, card_id: str, now: float) -> bool:
    inserted = conn.execute("INSERT OR IGNORE INTO agent_card_binding VALUES (?,?,?)",
                            (user_id, card_id, now)).rowcount == 1
    conn.execute("UPDATE agent_card_request SET confirmed=1 WHERE token=?", (token,))
    return inserted
