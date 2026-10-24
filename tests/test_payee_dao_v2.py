"""`data/payee_dao.py`（收款人 v2 主数据）的回归守卫：标签 / 专属额度 / 黑名单互斥。

**为什么要单独钉**：v2 让「白/普通/黑」成为**一个收款人的状态**（而不是三个功能），
风控关卡整条链都读它。这里的每一条都对应一个会直接影响钱的动作：

| 验收点 | 用例 |
|---|---|
| **黑白互斥**（同一人不能既白又黑） | `test_blacklist_and_whitelist_are_mutually_exclusive` |
| 额度必须是**正数分** | `test_quota_must_be_positive_cents` |
| 「已用额度」是**正数**（`txn.amount` 出账为负） | `test_used_quota_is_positive_even_though_out_txn_amount_is_negative` |
| 筛选认错取值要报错，不能静默返回全部 | `test_unknown_filter_is_rejected` |
| 只能改白名单内的列（防注入） | `test_update_rejects_unknown_columns` |
"""

from __future__ import annotations

import pytest

from data import payee_dao
from tests.conftest import raw


@pytest.fixture()
def wired(seeded):
    return seeded


def _id(path, name: str) -> str:
    return raw(path, "SELECT id FROM payee WHERE name = ?", (name,))[0]["id"]


def test_blacklist_and_whitelist_are_mutually_exclusive(wired) -> None:
    """加入黑名单 → `is_whitelist` 必须被清 0；反之亦然。这是风控关卡的前提。"""
    pid = _id(wired, "李四")                                  # 种子里李四(1001) 是白名单
    row = payee_dao.update_payee(pid, "u_zhangsan_0001", blacklist=1, blacklist_reason="诈骗")
    assert row["blacklist"] == 1 and row["is_whitelist"] == 0

    row = payee_dao.update_payee(pid, "u_zhangsan_0001", is_whitelist=1, whitelist_quota=100000)
    assert row["is_whitelist"] == 1 and row["blacklist"] == 0


def test_quota_must_be_positive_cents(wired) -> None:
    """额度是**整数分且为正**：0 / 负数 / 浮点 / bool 一律拒（防「额度 0 元也算白名单」）。"""
    pid = _id(wired, "王五")
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            payee_dao.update_payee(pid, "u_zhangsan_0001", whitelist_quota=bad)


def test_update_rejects_unknown_columns(wired) -> None:
    """列名白名单：不许改表外列（拼 SQL 的注入面）。"""
    pid = _id(wired, "王五")
    for bad in ("id", "phone; DROP TABLE payee", "created_at"):
        with pytest.raises(ValueError):
            payee_dao.update_payee(pid, "u_zhangsan_0001", **{bad: "x"})


def test_unknown_filter_is_rejected(wired) -> None:
    """`kind` 认不出 → 报错。静默当 `all` 会让「查黑名单」返回全部收款人、骗用户。"""
    with pytest.raises(ValueError):
        payee_dao.list_payees("u_zhangsan_0001", kind="blacklst")


def test_used_quota_is_positive_even_though_out_txn_amount_is_negative(wired) -> None:
    """**踩过的坑**：`txn.amount` 出账是**负数**，直接 SUM 会得到负的「已用」
    → 「剩余额度」反而变大（实测 50000 额度算出剩余 104800）。
    这里钉死：已用必须是**正数**，且剩余不会超过总额度。
    """
    pid = _id(wired, "张小美")
    payee_dao.update_payee(pid, "u_zhangsan_0001", is_whitelist=1, whitelist_quota=50000)
    row = payee_dao.get_payee(pid)
    assert row is not None
    # 种子账期是 2026-08（data.seed.AS_OF 之前一年），用它算
    used = payee_dao.used_whitelist_cents(pid, "2026-08", name="张小美")
    assert used >= 0, f"已用额度不该是负数：{used}"
    remain = payee_dao.remaining_quota_cents(row, "2026-08")
    assert remain is not None and 0 <= remain <= 50000


def test_no_quota_means_none_not_zero(wired) -> None:
    """没设额度 → `None`（= 走普通风控要验证码），不是 0（0 会被误读成「额度已用尽」）。"""
    pid = _id(wired, "赵六")
    row = payee_dao.get_payee(pid)
    assert row is not None and payee_dao.remaining_quota_cents(row, "2026-08") is None
