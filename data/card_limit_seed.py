"""F06 公开合成演示夹具；只增量补建，不覆盖余额、不自动绑卡。"""
from __future__ import annotations

import sqlite3
from typing import Callable

from data.seed import USER_ID

DEMO_CARDS = (
    ("card_limit_pending", "1111111111111111", "pending"),
    ("card_limit_approved", "2222222222222222", "approved"),
    ("card_limit_rejected", "3333333333333333", "rejected"),
)
INITIAL_CREDIT = 2_000_000
#: 2026-11-01（用户要求）：调额演练卡的密码同样统一 123456（与短信验证码同值）。
DEMO_PINS = {"card_limit_pending": "123456", "card_limit_approved": "123456", "card_limit_rejected": "123456"}
MAX_CREDIT = 10_000_000
MAX_DAILY = 10_000_000


def provision(conn: sqlite3.Connection, hash_password: Callable[[str], tuple[str, str]]) -> None:
    if not conn.execute("SELECT 1 FROM user WHERE id=?", (USER_ID,)).fetchone():
        return
    conn.execute("CREATE TABLE IF NOT EXISTS agent_card_limit_policy "
                 "(card_id TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS agent_card_limit_demo_pin "
                 "(card_id TEXT PRIMARY KEY, version INTEGER NOT NULL)")
    for card_id, number, outcome in DEMO_CARDS:
        account_id = f"acc_z_{card_id}"
        mask = f"{number[:4]} **** **** {number[-4:]}"
        conn.execute("INSERT OR IGNORE INTO account VALUES (?,?, 'credit',0,?,'active')",
                     (account_id, USER_ID, INITIAL_CREDIT))
        conn.execute("INSERT OR IGNORE INTO card VALUES (?,?,?,?, 'credit',?,?,?,'normal')",
                     (card_id, USER_ID, account_id, mask, INITIAL_CREDIT, 500_000, 2_000_000))
        row = conn.execute("SELECT user_id,account_id,card_no_mask FROM card WHERE id=?", (card_id,)).fetchone()
        if tuple(row) != (USER_ID, account_id, mask):
            continue
        conn.execute("INSERT OR IGNORE INTO agent_card_limit_policy VALUES (?,?)", (card_id, outcome))
        secret = conn.execute("SELECT 1 FROM agent_card_secret WHERE card_id=?", (card_id,)).fetchone()
        version = conn.execute("SELECT version FROM agent_card_limit_demo_pin WHERE card_id=?", (card_id,)).fetchone()
        if not secret or not version:
            salt, digest = hash_password(DEMO_PINS[card_id])
            conn.execute("INSERT INTO agent_card_secret VALUES (?,?,?,?) "
                         "ON CONFLICT(card_id) DO UPDATE SET salt=excluded.salt,pin_hash=excluded.pin_hash",
                         (card_id, number, salt, digest))
            conn.execute("INSERT OR REPLACE INTO agent_card_limit_demo_pin VALUES (?,1)", (card_id,))
