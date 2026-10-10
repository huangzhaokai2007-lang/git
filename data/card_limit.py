"""F06 增量持久化；不修改核心 DDL，也不在 DAO 中判断审批。"""
from __future__ import annotations

import json
import sqlite3

DDL = (
    "CREATE TABLE IF NOT EXISTS agent_card_limit_preview (token TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, session_id TEXT NOT NULL, card_id TEXT NOT NULL, kind TEXT NOT NULL, "
    "target INTEGER NOT NULL, days INTEGER, expires_at TEXT NOT NULL, snapshot TEXT NOT NULL, "
    "application_id TEXT)",
    "CREATE TABLE IF NOT EXISTS agent_card_limit_application (id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, card_id TEXT NOT NULL, card_no_mask TEXT NOT NULL, kind TEXT NOT NULL, "
    "old_limit INTEGER NOT NULL, target INTEGER NOT NULL, approved_limit INTEGER, status TEXT NOT NULL, "
    "reason TEXT NOT NULL, created_at TEXT NOT NULL, effective_at TEXT, expires_at TEXT, trace_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agent_card_limit_temporary (account_id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, card_id TEXT NOT NULL, total_limit INTEGER NOT NULL, "
    "expires_at TEXT NOT NULL, application_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS agent_account_daily_limit (account_id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, daily_limit INTEGER NOT NULL)",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    for ddl in DDL:
        conn.execute(ddl)


def has_schema(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_card_limit_temporary'").fetchone() is not None


def temporary(conn: sqlite3.Connection, account_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_card_limit_temporary WHERE account_id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def expired(conn: sqlite3.Connection, user: str, now: str) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM agent_card_limit_temporary "
                                             "WHERE user_id=? AND expires_at<=?", (user, now))]


def clear_temporary(conn: sqlite3.Connection, account_id: str) -> None:
    conn.execute("DELETE FROM agent_card_limit_temporary WHERE account_id=?", (account_id,))


def set_temporary(conn: sqlite3.Connection, row: dict, total: int, expiry: str, app_id: str) -> None:
    conn.execute("INSERT INTO agent_card_limit_temporary VALUES (?,?,?,?,?,?) ON CONFLICT(account_id) "
                 "DO UPDATE SET total_limit=excluded.total_limit,expires_at=excluded.expires_at,"
                 "application_id=excluded.application_id,card_id=excluded.card_id",
                 (row["account_id"], row["user_id"], row["id"], total, expiry, app_id))


def credit_account(conn: sqlite3.Connection, account_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM account WHERE id=?", (account_id,)).fetchone())


def adjust_available(conn: sqlite3.Connection, account_id: str, delta: int) -> None:
    conn.execute("UPDATE account SET available=available+? WHERE id=?", (delta, account_id))


def set_fixed(conn: sqlite3.Connection, account_id: str, target: int) -> None:
    conn.execute("UPDATE card SET credit_limit=? WHERE account_id=? AND type='credit'", (target, account_id))


def set_daily(conn: sqlite3.Connection, row: dict, target: int) -> None:
    conn.execute("UPDATE card SET daily_limit=? WHERE account_id=?", (target, row["account_id"]))
    conn.execute("INSERT INTO agent_account_daily_limit VALUES (?,?,?) ON CONFLICT(account_id) "
                 "DO UPDATE SET daily_limit=excluded.daily_limit",
                 (row["account_id"], row["user_id"], target))


def daily_cap(conn: sqlite3.Connection, account_id: str, user: str) -> int | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_account_daily_limit'").fetchone():
        return None
    row = conn.execute("SELECT daily_limit FROM agent_account_daily_limit WHERE account_id=? AND user_id=?",
                       (account_id, user)).fetchone()
    return row[0] if row else None


def save_preview(conn: sqlite3.Connection, token: str, user: str, session: str, req: dict,
                 expires: str, snapshot: dict) -> None:
    conn.execute("INSERT INTO agent_card_limit_preview VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                 (token, user, session, req["card_id"], req["kind"], req["target"], req["days"], expires,
                  json.dumps(snapshot, sort_keys=True)))


def preview(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_card_limit_preview WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def finish_preview(conn: sqlite3.Connection, token: str, app_id: str) -> None:
    conn.execute("UPDATE agent_card_limit_preview SET application_id=? WHERE token=?", (app_id, token))


def outcome(conn: sqlite3.Connection, card_id: str) -> str:
    row = conn.execute("SELECT outcome FROM agent_card_limit_policy WHERE card_id=?", (card_id,)).fetchone()
    return row[0] if row else "approved"


def save_application(conn: sqlite3.Connection, item: dict) -> None:
    fields = ("id", "user_id", "card_id", "card_no_mask", "kind", "old_limit", "target", "approved_limit",
              "status", "reason", "created_at", "effective_at", "expires_at", "trace_id")
    conn.execute("INSERT INTO agent_card_limit_application VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 tuple(item[key] for key in fields))


def application(conn: sqlite3.Connection, app_id: str, user: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_card_limit_application WHERE id=? AND user_id=?", (app_id, user)).fetchone()
    return dict(row) if row else None


def applications(conn: sqlite3.Connection, user: str) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM agent_card_limit_application WHERE user_id=? "
                                             "ORDER BY created_at DESC,id DESC", (user,))]
