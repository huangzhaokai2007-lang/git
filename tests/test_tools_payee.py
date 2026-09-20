"""卡 20 单测：T17 `add_payee` + `dao.insert_payee`（正常 / 边界 / 非法 + 铁律 8 脱敏红线）。

重点盖三件事：
① **脱敏**：完整号只作为入参，落库/出参/日志里一律只有 `138****0001` 形式；
② **契约**：`data` 严格 3 个键、新行 `is_whitelist=0` 且 `last_used_ts` 为 NULL（→ 之后转账走 L2 + OTP）；
③ **越权 fail-closed**：非本 demo 用户 → `FORBIDDEN` 且**一行都不写**。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from data import dao
from tools import _query_common, payee

from tests.conftest import FOREIGN_USER, count, raw

FULL = "13812345678"
MASKED = "138****5678"
OWNER = "u_zhangsan_0001"                       # data/seed.USER_ID：demo 期唯一用户


# ---------------- ① 正常 ----------------

def test_full_phone_is_masked_before_storing(seeded: Path) -> None:
    result = payee.add_payee("王小明", FULL)
    assert result.ok is True and result.error_code is None
    assert set(result.data) == {"payee_id", "name", "masked_phone"}          # 契约：只这 3 个键
    assert result.data["name"] == "王小明" and result.data["masked_phone"] == MASKED
    row = raw(seeded, "SELECT * FROM payee WHERE id = ?", (result.data["payee_id"],))[0]
    assert row["phone"] == MASKED and row["user_id"] == OWNER
    assert row["is_whitelist"] == 0 and row["last_used_ts"] is None           # → 之后按新收款人走 L2
    assert FULL not in str(dict(row))                                        # 完整号绝不落库
    assert result.facts["masked_phone"] == MASKED                            # 回执里的数字来自事实包


def test_already_masked_phone_is_accepted(seeded: Path) -> None:
    result = payee.add_payee("陈静", MASKED)
    assert result.ok is True and result.data["masked_phone"] == MASKED


def test_masked_phone_helper_branches() -> None:
    assert payee.mask_phone(FULL) == MASKED
    assert payee.mask_phone(f"  {MASKED}  ") == MASKED                        # 两侧空白容忍
    assert payee.mask_phone("1381234567") is None                             # 10 位 → 不猜、不补位
    assert payee.mask_phone("abc") is None and payee.mask_phone("") is None


# ---------------- ② 边界：去重 ----------------

def test_same_name_and_phone_is_not_inserted_twice(seeded: Path) -> None:
    first = payee.add_payee("王小明", FULL)
    before = count(seeded, "payee")
    again = payee.add_payee("王小明", FULL)
    assert again.ok is True and count(seeded, "payee") == before              # 不重复插入
    assert again.data == first.data                                          # 返回既有收款人
    assert "已经" in again.message                                           # 用 message 说明，不给 data 加键


def test_same_phone_different_name_inserts_a_new_row(seeded: Path) -> None:
    first = payee.add_payee("王小明", FULL)
    other = payee.add_payee("王晓明", FULL)
    assert other.ok is True and other.data["payee_id"] != first.data["payee_id"]


def test_another_users_same_name_and_phone_does_not_block_me(seeded: Path) -> None:
    """去重只看**当前 user**：别人有同名同号，我照样能加自己的（这不是越权）。"""
    dao.insert_payee(FOREIGN_USER, "王小明", MASKED)
    result = payee.add_payee("王小明", FULL)
    assert result.ok is True
    rows = raw(seeded, "SELECT user_id FROM payee WHERE name = ? AND phone = ?", ("王小明", MASKED))
    assert sorted(row["user_id"] for row in rows) == [FOREIGN_USER, OWNER]    # 两人各一条


# ---------------- ③ 非法 ----------------

@pytest.mark.parametrize(("phone", "why"), [
    ("1381234567", "10 位"),
    ("138123456789", "12 位"),
    ("1381234567a", "含字母"),
    ("138-1234-5678", "带分隔符"),
    ("*" * 8, "纯星号"),
])
def test_bad_phone_is_invalid_without_echoing_the_value(seeded: Path, caplog: pytest.LogCaptureFixture,
                                                       phone: str, why: str) -> None:
    with caplog.at_level(logging.DEBUG):
        result = payee.add_payee("王小明", phone)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT", why
    assert result.data is None and result.facts == {}
    assert phone not in caplog.text                                          # 铁律 8：取值不进日志
    assert phone not in result.message                                       # 也不回显给用户


def test_empty_name_or_phone_is_invalid(seeded: Path) -> None:
    assert payee.add_payee("", FULL).error_code == "INVALID_ARGUMENT"
    assert payee.add_payee("   ", FULL).error_code == "INVALID_ARGUMENT"
    assert payee.add_payee("王小明", "").error_code == "INVALID_ARGUMENT"


def test_foreign_session_user_is_forbidden_and_writes_nothing(seeded: Path) -> None:
    before = count(seeded, "payee")
    _query_common.set_current_user(FOREIGN_USER)
    try:
        result = payee.add_payee("王小明", FULL)
    finally:
        _query_common.set_current_user(None)
    assert result.ok is False and result.error_code == "FORBIDDEN"
    assert count(seeded, "payee") == before                                  # 越权一行都不写


# ---------------- ④ DAO 原语 ----------------

def test_dao_insert_payee_round_trip(seeded: Path) -> None:
    row = dao.insert_payee(OWNER, "李四", MASKED, bank="测试银行", is_whitelist=0)
    assert row["id"].startswith("payee_") and row["phone"] == MASKED and row["last_used_ts"] is None
    assert dao.get_payee(row["id"])["name"] == "李四"


def test_dao_insert_payee_rejects_bad_args(seeded: Path) -> None:
    with pytest.raises(ValueError):
        dao.insert_payee(OWNER, "李四", MASKED, is_whitelist=2)
    with pytest.raises(ValueError):
        dao.insert_payee(OWNER, "  ", MASKED)
    with pytest.raises(ValueError):
        dao.insert_payee("", "李四", MASKED)
