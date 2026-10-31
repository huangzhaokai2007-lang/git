"""合成演示卡凭证 + 预绑关系。凭证不覆盖已有配置；绑定不改变余额。"""
from __future__ import annotations

import sqlite3
from typing import Callable

# 明确公开的测试夹具，绝不可用于真实银行卡或部署认证。
DEMO_CREDENTIALS = (
    ("card_savings_0001", "6222000000000001", "314159"),
    ("card_credit_0002", "6222000000000002", "271828"),
    ("card_savings_0003", "6222000000000003", "161803"),
)

#: 演示库预绑的本人名下卡（评委一进来就能在「管卡」看到、转账时能选付款卡）。
#: 绑定（agent_card_binding）与凭证（agent_card_secret）是两件事；这里只落绑定关系。
BOUND_CARD_IDS = (
    "card_savings_0001",    # 招商银行 · 储蓄
    "card_credit_0002",     # 建设银行 · 信用
    "card_savings_0003",    # 工商银行 · 储蓄（已挂失，演示挂失用）
    "card_limit_pending",   # 中国银行 · 信用（额度调整：待审批）
    "card_limit_approved",  # 农业银行 · 信用（额度调整：通过）
    "card_limit_rejected",  # 交通银行 · 信用（额度调整：拒绝）
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


def provision_bindings(conn: sqlite3.Connection, *, user_id: str = "u_zhangsan_0001") -> int:
    """把 6 张预设卡预绑到演示用户（幂等：已绑的不重复）。返回**新增**绑定数。"""
    from data import card_binding as store
    import time as _time
    store.ensure_schema(conn)                 # agent_card_binding 是绑卡流程懒建的表
    at = _time.time()
    added = 0
    for card_id in BOUND_CARD_IDS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO agent_card_binding(user_id, card_id, bound_at) VALUES (?,?,?)",
            (user_id, card_id, at))
        added += cur.rowcount
    return added

