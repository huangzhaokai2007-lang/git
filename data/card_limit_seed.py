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
DEMO_PIN = "000000"
MAX_CREDIT = 10_000_000
MAX_DAILY = 10_000_000


def provision(conn: sqlite3.Connection, hash_password: Callable[[str], tuple[str, str]]) -> None:
    if not conn.execute("SELECT 1 FROM user WHERE id=?", (USER_ID,)).fetchone():
        return
    conn.execute("CREATE TABLE IF NOT EXISTS agent_card_limit_policy "
                 "(card_id TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
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
        if not conn.execute("SELECT 1 FROM agent_card_secret WHERE card_id=?", (card_id,)).fetchone():
            salt, digest = hash_password(DEMO_PIN)
            conn.execute("INSERT INTO agent_card_secret VALUES (?,?,?,?)", (card_id, number, salt, digest))
