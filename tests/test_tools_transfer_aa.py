"""任务卡 05 单测（拆分后）：T9 AA 收款 + 红线不变量（事实包 / 禁浮点 / 错误消息无数字 / 越权）。

共享脚手架在 conftest.py（卡 04b 拆分）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from data.seed import SAVINGS_ID
from tools import query, transfer

from tests.conftest import (FOREIGN_PAYEE, PAYEE, PAYEE_NEW, Clock, raw, count, balance, assert_covered, assert_no_money_floats, preview_ok)


def test_aa_split_puts_the_remainder_on_the_initiator(seeded: Path) -> None:
    result = transfer.create_aa_request([PAYEE, PAYEE_NEW, "payee_0003"], 10_000)
    assert result.ok and result.data["per_person_amount"] == 3_333
    assert result.facts["initiator_share"] == 1 and result.facts["total_check"] == 10_000
    assert 3_333 * 3 + 1 == 10_000
    assert result.data["request_id"].startswith("aa_")
    assert_covered(result.message, result.facts)


def test_aa_exact_division_has_no_remainder(seeded: Path) -> None:
    ids = ["payee_0001", "payee_0002", "payee_0003", "payee_0004", "payee_0005"]
    result = transfer.create_aa_request(ids, 10_000)
    assert result.data["per_person_amount"] == 2_000 and result.facts["initiator_share"] == 0
    assert result.facts["total_check"] == 10_000


def test_aa_single_payee_takes_the_whole_amount(seeded: Path) -> None:
    result = transfer.create_aa_request([PAYEE], 9_999)
    assert result.data["per_person_amount"] == 9_999 and result.facts["initiator_share"] == 0


def test_aa_amount_smaller_than_people_is_allowed_and_documented(seeded: Path) -> None:
    """规格未设"金额 ≥ 人数"下限，本卡不自行加限制：均摊得 0 分、余数全给发起人，合计仍精确相等。"""
    result = transfer.create_aa_request([PAYEE, PAYEE_NEW, "payee_0003"], 2)
    assert result.data["per_person_amount"] == 0 and result.facts["initiator_share"] == 2
    assert result.facts["total_check"] == 2


def test_aa_writes_only_an_audit_row(seeded: Path) -> None:
    before = (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded))
    result = transfer.create_aa_request([PAYEE], 1_000)
    assert result.ok
    assert (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded)) == \
        (before[0], before[1] + 1, before[2])
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert (audit["tool"], audit["intent"], audit["result"]) == ("create_aa_request", "aa_collect", "success")


def test_aa_rejects_duplicates_and_unknown_or_foreign_payees(seeded: Path, foreign_payee: Path) -> None:
    assert transfer.create_aa_request([PAYEE, PAYEE], 1_000).error_code == "INVALID_ARGUMENT"
    assert transfer.create_aa_request([PAYEE, "payee_nope"], 1_000).error_code == "NOT_FOUND"
    refused = transfer.create_aa_request([PAYEE, FOREIGN_PAYEE], 1_000)
    assert refused.error_code == "FORBIDDEN" and refused.facts == {}


@pytest.mark.parametrize("payee_ids,amount", [
    ([], 1_000), ([""], 1_000), ([PAYEE], 0), ([PAYEE], -1), ([PAYEE], 1.5), ([PAYEE], True),
    (PAYEE, 1_000), (None, 1_000), ([PAYEE], "1000"),
])
def test_aa_rejects_illegal_arguments(seeded: Path, payee_ids: object, amount: object) -> None:
    result = transfer.create_aa_request(payee_ids, amount)      # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


def happy_calls(seeded: Path, clock: Clock) -> list[tuple[str, transfer.ToolResult]]:
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    return [("resolve_payee", transfer.resolve_payee("李四")),
            ("preview_transfer", preview_ok(PAYEE, 10_000)),
            ("execute_transfer", transfer.execute_transfer(token)),
            ("create_aa_request", transfer.create_aa_request([PAYEE, PAYEE_NEW], 10_000))]


def test_reply_messages_numbers_are_all_in_facts(seeded: Path, clock: Clock) -> None:
    for name, result in happy_calls(seeded, clock):
        assert result.ok, name
        assert_covered(result.message, result.facts)


def test_no_floats_anywhere_in_data_or_facts(seeded: Path, clock: Clock) -> None:
    for name, result in happy_calls(seeded, clock):
        assert_no_money_floats(result.data, f"{name}.data")
        assert_no_money_floats(result.facts, f"{name}.facts")
        json.dumps({"data": result.data, "facts": result.facts}, ensure_ascii=False)


def test_money_and_ownership_helpers_agree_with_query_module(seeded: Path) -> None:
    """两处同口径实现（重复实现，待 04b/05b 合并）必须给出相同结果，否则就是漂移。"""
    for cents in (0, 5, 100, -123_456, 9_999_999):
        assert transfer._money(cents) == query._money(cents)
    assert transfer._owned_account_ids() == query._owned_account_ids() == {SAVINGS_ID, "acc_credit_0001"}


ILLEGAL_CALLS = [
    ("resolve_payee", ("",), {}),
    ("preview_transfer", ("", 100), {}),
    ("preview_transfer", (PAYEE, 0), {}),
    ("execute_transfer", ("",), {}),
    ("create_aa_request", ([], 100), {}),
]


@pytest.mark.parametrize("name,args,kwargs", ILLEGAL_CALLS, ids=[f"{c[0]}-{i}" for i, c in enumerate(ILLEGAL_CALLS)])
def test_rejected_calls_return_a_clean_digit_free_result(seeded: Path, name: str, args: tuple,
                                                         kwargs: dict) -> None:
    result = getattr(transfer, name)(*args, **kwargs)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {}
    assert result.message and not re.search(r"\d", result.message)


def test_foreign_account_does_not_pay_for_our_transfers(foreign_payee: Path) -> None:
    """他人账户 id 更小 → 本人储蓄账户取不到 → fail-closed：拒绝转账，绝不从他人账户扣款。"""
    refused = transfer.preview_transfer(PAYEE, 1_000)
    assert refused.error_code == "FORBIDDEN" and refused.facts == {}
    foreign_balance = raw(foreign_payee, "SELECT balance, available FROM account WHERE id = 'acc_aaa_foreign'")[0]
    assert (foreign_balance["balance"], foreign_balance["available"]) == (999_900, 999_900)   # 分毫未动
