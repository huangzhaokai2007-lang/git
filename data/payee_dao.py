"""收款人主数据 v2 的 DAO 原语（标签 / 白名单专属额度 / 黑名单）。

**为什么单开一个文件**：`data/dao.py` 已**正好 300 行**（`CLAUDE.md` 单文件红线），
v2 要给 `payee` 加 4 个字段并读写它们，塞不下。照仓内先例（`tools/_transfer_token.py`
这类按职责拆）单开；`dao.py` 不动，`find_payee` / `insert_payee` 继续由它提供。

**金额一律整数分**（铁律）。

**不存"已用额度"**：按 `txn` 聚合算（`used_whitelist_cents`）。理由是同一份数据只存一处真相 ——
若另存一列，与流水对不上时无法判断谁对。
"""

from __future__ import annotations

import sqlite3

from data import dao

#: 白名单专属额度上限（分）。防御：>1 亿分（=100 万元）视为录入错误 —— 与单笔上限同数量级。
MAX_QUOTA_CENTS = 100_000_000


def _conn() -> sqlite3.Connection:
    return dao.connection()


def get_payee(payee_id: str) -> dict | None:
    """按 id 取收款人全字段（含 v2 新列）；不存在 → None（**不抛**，调用方决定错误码）。"""
    row = _conn().execute("SELECT * FROM payee WHERE id = ?", (payee_id,)).fetchone()
    return None if row is None else dict(row)


def list_payees(user_id: str, *, kind: str = "all") -> list[dict]:
    """当前 user 的收款人；`kind` 取 `all` / `whitelist` / `blacklist`。

    **归属过滤写在 SQL 里**（fail-closed）；排序固定 `白名单优先, 黑名单, 姓名, id` —— 编排层不重排。
    认不出的 `kind` → `ValueError`（不静默当 `all`，否则「查黑名单」会返回全部、骗用户）。
    """
    where = {"all": "", "whitelist": " AND is_whitelist = 1",
             "blacklist": " AND blacklist = 1"}.get(kind)
    if where is None:
        raise ValueError(f"不支持的收款人筛选：{kind}")
    rows = _conn().execute(
        "SELECT * FROM payee WHERE user_id = ?" + where
        + " ORDER BY is_whitelist DESC, blacklist DESC, name, id", (user_id,)).fetchall()
    return [dict(row) for row in rows]


def update_payee(payee_id: str, user_id: str, **fields: object) -> dict | None:
    """按列更新（**列名白名单**，防注入）；返回更新后的行，找不到 → None。

    白名单、黑名单**互斥**在这里强制：置 `is_whitelist=1` 时清 `blacklist`，反之亦然。
    """
    allowed = {"tags", "whitelist_quota", "whitelist_expire", "is_whitelist",
               "blacklist", "blacklist_reason"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"不允许更新的列：{sorted(bad)}")
    if "is_whitelist" in fields and fields["is_whitelist"]:
        fields.setdefault("blacklist", 0)          # 互斥：加入白名单即移出黑名单
    if "blacklist" in fields and fields["blacklist"]:
        fields.setdefault("is_whitelist", 0)       # 互斥：加入黑名单即移出白名单
    if (quota := fields.get("whitelist_quota")) is not None:
        if not isinstance(quota, int) or isinstance(quota, bool) or quota <= 0 \
                or quota > MAX_QUOTA_CENTS:
            raise ValueError("白名单专属额度必须是正数分，且不超过上限")
    if not fields:
        return get_payee(payee_id)
    keys = sorted(fields)
    sql = "UPDATE payee SET " + ", ".join(f"{k} = ?" for k in keys) \
        + " WHERE id = ? AND user_id = ?"
    _conn().execute(sql, tuple(fields[k] for k in keys) + (payee_id, user_id))
    return get_payee(payee_id)


def used_whitelist_cents(payee_id: str, month_prefix: str, *, name: str = "") -> int:
    """该收款人**本月**已转出金额（分）—— 用于算白名单专属额度的剩余。

    `month_prefix` 形如 `2026-10`（与 `txn.ts` 的 ISO 前缀对齐）。
    只算**出账**（`direction='out'`），退款/收入不计入。

    ⚠️ **口径与限制**：优先按 `txn.payee_id` 聚合（精确）；但种子流水是 v2 之前生成的、
    那一列为 NULL，故对这些行**回落到按 `counterparty` 名字匹配**。
    - 好处：演示库立刻有「已用额度」可算，不用重灌数据；
    - 代价：**同名的两个李四**会把额度算在一起（兜底行无法区分）。
    消除它只需让新转账回填 `payee_id`（`transfer.execute_transfer` 后续补），种子数据重灌亦可。
    """
    row = _conn().execute(
        # ⚠️ `txn.amount` 的口径是「正=入账、负=出账」（见 schema.sql 注释）。
        # 出账行是负数，直接 SUM 会得到**负的「已用额度」**，于是「剩余额度」反而变大 ——
        # 本列必须在 SQL 里取相反数，得到**正数的已用金额**。
        "SELECT COALESCE(SUM(-amount), 0) AS used FROM txn"
        " WHERE direction = 'out' AND ts LIKE ?"
        "   AND (payee_id = ? OR (payee_id IS NULL AND counterparty = ?))",
        (f"{month_prefix}%", payee_id, name)).fetchone()
    return int(row["used"] if row else 0)


def remaining_quota_cents(payee: dict, month_prefix: str) -> int | None:
    """白名单专属额度**剩余**（分）；未设额度 → `None`（= 走普通风控，要验证码）。

    收款人字典两种键都接：DAO 自己用 `id`，而编排层（`agent/payee_resolve.py`）从 T20 拿到的
    是 `payee_id` —— 两边叫法不同，这里统一容错，避免调用方各自 `{**person, "id": ...}` 复制。
    """
    quota = payee.get("whitelist_quota")
    if not quota:
        return None
    payee_id = str(payee.get("id") or payee.get("payee_id") or "")
    used = used_whitelist_cents(payee_id, month_prefix, name=str(payee.get("name") or ""))
    return max(0, int(quota) - used)
