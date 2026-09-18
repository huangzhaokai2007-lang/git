"""任务卡 04b/05b 单测：`tools/_query_common.py` 共享 helper 的契约（会话用户 / 归属断言 / 金额展示 / 枚举）。

卡 06b 追加：**facts 逐字断言**工具 —— 补上审核 RISK-1 的漏洞（`numbers()` 会把 `1,234.56` 与
`123,456` 归一成同一串，挡不住量级错）。本文件是所有查询类测试的公共依赖，故 helper 放这里。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from data.seed import CREDIT_ID, USER_ID
from tools import _query_common, query, schemas, transfer

from tests.conftest import (FOREIGN_USER, FOREIGN_CARD, FOREIGN_ACCOUNT, FOREIGN_AMOUNT, balance)


def test_string_enum_error_codes_serialize_as_plain_strings() -> None:
    assert str(schemas.ErrorCode.NOT_FOUND) == "NOT_FOUND"
    failed = query.get_balance("checking")
    assert failed.error_code == "INVALID_ARGUMENT"
    assert json.dumps({"code": failed.error_code}) == '{"code": "INVALID_ARGUMENT"}'


def test_money_uses_integer_math_only() -> None:
    assert (query._money(0), query._money(5), query._money(100), query._money(-123_456)) == \
        ("0.00", "0.05", "1.00", "-1,234.56")


def test_session_user_drives_the_ownership_decision(foreign: Path) -> None:
    query.set_current_user(FOREIGN_USER)
    result = query.get_balance("savings")
    assert result.ok and result.data["balance"] == FOREIGN_AMOUNT
    query.set_current_user(None)
    assert query.get_balance("savings").error_code == "FORBIDDEN"


def test_require_owned_contract_for_cards_and_subscriptions(seeded: Path) -> None:
    """卡 06/07 的资源查询要用的归属断言：本卡先把契约钉住。"""
    with pytest.raises(query.ToolError) as caught:
        query.require_owned("卡片", FOREIGN_USER, FOREIGN_CARD)
    assert caught.value.message == "卡片不属于当前用户"
    query.require_owned("卡片", USER_ID, "card_savings_0001")     # 自己的资源：不抛


# ---------------- 卡 04b：单份实现（不是副本） ----------------


def test_shared_helpers_are_single_sourced() -> None:
    """卡 04b 的核心断言：薄封装只存在**一份对象**，两个模块是引用它而不是各留一份副本。

    若哪天又有人在 query/transfer 里复制一份，`is` 立即失败 —— 这正是「第 4 份副本漂移」的防线。
    """
    both = ("_ok", "_fail", "_invalid", "_dao_reject", "_money", "_money_facts",
            "_owned_account_ids", "ToolError", "require_owned", "current_user_id")
    for name in both:
        shared = getattr(_query_common, name)
        assert getattr(query, name) is shared, f"query.{name} 不是共享对象"
        assert getattr(transfer, name) is shared, f"transfer.{name} 不是共享对象"
    # 这两个只有 query.py 用（会话用户注入 + 百分数基数），transfer.py 不引用它们也是对的
    assert query.set_current_user is _query_common.set_current_user
    assert query.PCT_TOTAL is _query_common.PCT_TOTAL


def test_money_and_pct_have_separate_constants() -> None:
    """`_money` 的 100 是「每元 100 分」，与百分数基数 `PCT_TOTAL` 语义无关：两个常量各自命名。"""
    assert _query_common.CENTS_PER_YUAN == 100 and _query_common.PCT_TOTAL == 100
    assert query.PCT_TOTAL is _query_common.PCT_TOTAL


def test_money_facts_pairs_cents_with_yuan_text() -> None:
    assert _query_common._money_facts(12_345, "amount") == {"amount": 12_345, "amount_yuan": "123.45"}
    assert _query_common._money_facts(-5, "fee") == {"fee": -5, "fee_yuan": "-0.05"}


def test_fail_is_a_clean_empty_result() -> None:
    failed = _query_common._fail(schemas.ErrorCode.FORBIDDEN, "不属于当前用户")
    assert failed.ok is False and failed.data is None and failed.facts == {}
    assert failed.error_code == "FORBIDDEN"


def test_owned_account_ids_is_fail_closed(foreign: Path) -> None:
    """他人账户 id 更小 → DAO 优先返回它 → 本用户该类型账户取不到：宁少勿漏，绝不把他人的算进来。"""
    owned = _query_common._owned_account_ids()
    assert FOREIGN_ACCOUNT not in owned
    assert owned == {CREDIT_ID}


# ---------------- 卡 06b：facts 逐字断言（补审核 RISK-1） ----------------

#: 保留千分位与小数点的数字 token（与 conftest 里归一化的 `_NUMBER` 刻意不同）
_VERBATIM_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_VERBATIM_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?")


def assert_facts_verbatim(text: str, facts: dict) -> None:
    """**逐字**断言：text 里每个数字 token 必须原样出现在 facts 里（不剥千分位/小数点）。

    与 `conftest.assert_covered` 的区别：那边把 `1,234.56` 与 `123,456` 归一成同一串，
    量级错（少/多一个 0、丢了小数点）照样通过；这里逐字比对，量级错必红（卡 04 审核 RISK-1）。
    """
    haystack = json.dumps(facts, ensure_ascii=False)
    tokens = set(_VERBATIM_NUMBER.findall(_VERBATIM_DATE.sub(" ", text)))
    missing = sorted(token for token in tokens if token not in haystack)
    assert not missing, f"这些数字没有逐字出现在 facts 里：{missing}｜文本：{text[:200]}"


def test_verbatim_assertion_catches_a_magnitude_error() -> None:
    """自证这条断言有牙齿：把「1,234.56 元」写成「123,456 元」时，归一化断言会放过、逐字断言必红。"""
    facts = {"amount": 123_456, "amount_yuan": "1,234.56"}
    assert_facts_verbatim("本期支出合计：1,234.56 元。", facts)          # 正确：逐字命中
    with pytest.raises(AssertionError):
        assert_facts_verbatim("本期支出合计：123,456 元。", facts)      # 量级错：逐字不命中
