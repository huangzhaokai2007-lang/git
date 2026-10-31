"""收款人的**多张银行卡**（2026-10-31 用户需求 · SPEC-CHANGE 已获批准）。

口径与「汤 · 卡片」板块对齐（只对齐字段口径，不碰他的代码）：
- 银行名与 `card` 表同一套中文行名；
- 掩码格式与 `card.card_no_mask` 一致：`6222 **** **** 0001`（完整号绝不入库，铁律 8）；
- 卡种沿用 `card.type` 的两个中文叫法：储蓄卡 / 信用卡。

为什么不复用 `card` 表：那张表是「**我自己的**卡」（有账户、额度、状态机），
收款人的卡是**别人的**，没有账户与额度语义，混在一张表里会让归属与额度口径变糊。
"""

from __future__ import annotations

import uuid

from data import dao
from tools._query_common import current_user_id

#: 银行行名（与 `card` 表同一套；新增收款人随机取一家）
BANKS = ("招商银行", "工商银行", "建设银行", "中国银行", "农业银行", "交通银行", "浦发银行", "中信银行")

#: 卡种
CARD_TYPES = ("储蓄卡", "信用卡")

#: 掩码前缀（与 `card.card_no_mask` 同形；后四位才是可辨位）
_MASK_PREFIX = "6222 **** **** "


def mask_card_no(last4: str) -> str:
    """生成与 `card` 表同形的掩码：`6222 **** **** 0001`。"""
    digits = "".join(ch for ch in str(last4) if ch.isdigit())[-4:].rjust(4, "0")
    return f"{_MASK_PREFIX}{digits}"


def _rows(payee_id: str, user_id: str) -> list[dict]:
    """该收款人的卡（归属 fail-closed：`user_id` 一并进 where）。"""
    conn = dao.connection()
    rows = conn.execute(
        "SELECT * FROM payee_card WHERE payee_id = ? AND user_id = ? "
        "ORDER BY is_default DESC, id", (payee_id, user_id))
    return [dict(row) for row in rows]


def cards_of(payee_id: str, *, user_id: str | None = None) -> list[dict]:
    """收款人的全部银行卡（默认卡在前）。空列表 = 还没登记卡。"""
    return _rows(payee_id, user_id or current_user_id())


def card_payload(card: dict) -> dict:
    """给界面/编排层用的一条卡（**不含**完整卡号，只有掩码）。"""
    return {"card_id": card["id"], "bank": card["bank"],
            "card_no_mask": card["card_no_mask"], "card_type": card["card_type"],
            "is_default": bool(card["is_default"])}


def card_facts(card: dict) -> dict:
    """事实包里的那张卡（卡面上的「收款卡」行只能用这里的值，铁律 2）。"""
    return {"payee_card_id": card["id"], "payee_card_bank": card["bank"],
            "payee_card_mask": card["card_no_mask"], "payee_card_type": card["card_type"],
            "payee_card_is_default": bool(card["is_default"])}


def resolve(payee_id: str, card_id: str | None,
            *, user_id: str | None = None) -> tuple[dict | None, str | None]:
    """把「用户选的卡」解析成一条卡：`(card, 错误码)`。

    - `card_id` 为空 → 取默认卡（没有默认卡则取排序第一张）；
    - 指定的卡不属于这位收款人 / 不属于当前用户 → `INVALID_ARGUMENT`（不猜、不换人）；
    - 该收款人一张卡都没有 → 返回 `(None, None)`，调用方按「无卡」处理（不拦转账）。
    """
    owner = user_id or current_user_id()
    cards = _rows(payee_id, owner)
    if not cards:
        return None, None
    if not card_id:
        default = next((c for c in cards if c["is_default"]), cards[0])
        return default, None
    picked = next((c for c in cards if c["id"] == card_id), None)
    if picked is None:
        return None, "INVALID_ARGUMENT"
    return picked, None


def add_cards(conn, *, user_id: str, payee_id: str, primary_bank: str | None,
              created_at: str, bank_seed: str = "") -> int:
    """给新收款人生成 1–3 张卡（第一张 = 主银行 + 默认卡），返回张数。

    随机源只用于「生成哪几家银行」，**不用于金额/风控**（铁律 1 只约束业务判定，不约束模拟数据）。
    同一 `bank_seed` 下结果稳定，便于测试与复现。
    """
    banks = list(BANKS)
    if primary_bank and primary_bank in banks:
        banks.remove(primary_bank)
    rng = _stable_rng(bank_seed or payee_id)
    count = 1 + rng.randrange(0, 3)                      # 1–3 张
    chosen = [primary_bank or banks[0]] + [banks[rng.randrange(len(banks))]
                                          for _ in range(count - 1)]
    rows = []
    for index, bank in enumerate(chosen):
        last4 = f"{rng.randrange(0, 10000):04d}"
        rows.append((f"pc_{uuid.uuid4().hex[:10]}", payee_id, user_id, bank,
                     mask_card_no(last4), CARD_TYPES[0] if index == 0 else
                     CARD_TYPES[rng.randrange(len(CARD_TYPES))],
                     1 if index == 0 else 0, created_at))
    with _tx(conn):
        conn.executemany(
            "INSERT INTO payee_card (id, payee_id, user_id, bank, card_no_mask, "
            "card_type, is_default, created_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def _tx(conn):
    from data.db import transaction
    return transaction(conn)


def _stable_rng(seed: str):
    """按字符串种子生成稳定随机源（`random.Random(str)` 本身即可）。"""
    import random
    return random.Random(str(seed))
