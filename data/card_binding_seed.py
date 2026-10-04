"""只补充合成演示卡凭证；不覆盖已有配置，不绑定卡、不改变余额。"""
from __future__ import annotations

import sqlite3
from typing import Callable

# 明确公开的测试夹具，绝不可用于真实银行卡或部署认证。
DEMO_CREDENTIALS = (
    ("card_savings_0001", "6222000000000001", "314159"),
    ("card_credit_0002", "6222000000000002", "271828"),
    ("card_savings_0003", "6222000000000003", "161803"),
)


def provision(conn: sqlite3.Connection, hash_password: Callable[[str], tuple[str, str]]) -> None:
    for card_id, number, password in DEMO_CREDENTIALS:
        if conn.execute("SELECT 1 FROM agent_card_secret WHERE card_id=?", (card_id,)).fetchone():
            continue
        row = conn.execute("SELECT card_no_mask FROM card WHERE id=?", (card_id,)).fetchone()
        if row is None or row["card_no_mask"] != f"{number[:4]} **** **** {number[-4:]}":
            continue
        salt, digest = hash_password(password)
        conn.execute("INSERT INTO agent_card_secret VALUES (?,?,?,?)", (card_id, number, salt, digest))
