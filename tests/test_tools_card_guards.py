"""任务卡 06 单测（拆分后）：T12 状态机 / 双因子 / 限额校验 / 幂等 / 越权 / 原子性 / 红线守卫。

冻结面、正常路径与 apply 在 `tests/test_tools_card.py`；共享常量与 `_call` / `_row` / `_force_status`
辅助从那里 import（避免复制漂移）。
"""

from __future__ import annotations

import pytest

from tools import card, subscription as sub
from tools.schemas import ErrorCode

from tests.conftest import (
    FOREIGN_CARD, FOREIGN_USER, OTP, assert_covered, assert_no_money_floats, count, raw, write_sql,
)
from tests.test_tools_card import (
    CREDIT, L2_ACTIONS, L3_ACTIONS, L3_FACT_KEYS, LOST, SAVINGS, _call, _force_status, _ref, _row,
)


# ---------------- 状态机（非法流转） ----------------

@pytest.mark.parametrize(("card_id", "status", "action", "kw"), [
    (SAVINGS, "locked", "lock", {}),                               # 已锁不能再锁
    (SAVINGS, "normal", "unlock", {}),                             # 未锁不能解挂
    (SAVINGS, "locked", "adjust_limit", {"credit_limit": 1}),
    (SAVINGS, "locked", "set_txn_limit", {"single_limit": 1}),
    (SAVINGS, "frozen", "lock", {}),
    (SAVINGS, "frozen", "adjust_limit", {"credit_limit": 1}),
    (SAVINGS, "frozen", "set_txn_limit", {"single_limit": 1}),
    (LOST, "lost", "unlock", {}),                                  # 卡 06 第 4 条
    (LOST, "lost", "lock", {}),
    (LOST, "lost", "report_lost", {}),                             # 已挂失再挂失
    (LOST, "lost", "adjust_limit", {"credit_limit": 1}),
    (LOST, "lost", "set_txn_limit", {"single_limit": 1}),
])
def test_illegal_state_transitions_are_rejected(seeded: Path, card_id: str, status: str,
                                                action: str, kw: dict) -> None:
    _force_status(seeded, card_id, status)
    before = count(seeded, "audit_log")
    result = _call(card_id, action, **kw)
    assert result.error_code == ErrorCode.INVALID_STATE, result.message
    assert count(seeded, "audit_log") == before              # 非法操作零副作用
    assert _row(seeded, card_id)["status"] == status          # 状态没被改动


def test_lost_card_unlock_hits_the_dedicated_branch(seeded: Path) -> None:
    """卡 06 第 4 条的专用分支：挂失卡不能解挂 → 提示"走补卡"，**不是**"未锁定"那条通用分支。

    （变异自检发现：只断言 error_code 时，删掉这条分支测试仍绿 —— 相邻 guard 会返回同一错误码。）
    """
    _force_status(seeded, LOST, "lost")
    result = _call(LOST, "unlock")
    assert result.error_code == ErrorCode.INVALID_STATE
    assert "补卡" in result.message


def test_report_lost_accepts_a_locked_card(seeded: Path) -> None:
    _force_status(seeded, SAVINGS, "locked")
    assert _call(SAVINGS, "report_lost").data["status"] == "lost"


def test_frozen_card_cannot_be_unlocked_or_relimited(seeded: Path) -> None:
    _force_status(seeded, SAVINGS, "frozen")
    assert _call(SAVINGS, "unlock").error_code == ErrorCode.INVALID_STATE
    assert _call(SAVINGS, "set_txn_limit", single_limit=1).error_code == ErrorCode.INVALID_STATE


# ---------------- 双因子（confirm_ref + OTP） ----------------

def test_missing_confirm_ref_is_forbidden(seeded: Path) -> None:
    assert card.manage_card(SAVINGS, "lock", otp=OTP).error_code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize("bogus", ["cf_made_up", "card_savings_0001", "yes", "确认"])
def test_bogus_confirm_ref_is_forbidden(seeded: Path, bogus: str) -> None:
    """reviewer 钉死：不存在/自造串 → FORBIDDEN（伪造），并留痕 rejected。"""
    before = count(seeded, "audit_log")
    assert _call(SAVINGS, "lock", ref=bogus).error_code == ErrorCode.FORBIDDEN
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["result"] == "rejected" and row["intent"] == "card_lock"
    assert row["permission_tier"] == "L2" and SAVINGS in row["params_json"]


def test_missing_otp_is_forbidden(seeded: Path) -> None:
    assert _call(SAVINGS, "lock", otp=None).error_code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize("bad_otp", ["000000", "", "12345", "1234567", "123 456"])
def test_wrong_otp_is_forbidden(seeded: Path, bad_otp: str) -> None:
    assert _call(SAVINGS, "lock", otp=bad_otp).error_code == ErrorCode.FORBIDDEN
    assert _row(seeded, SAVINGS)["status"] == "normal"


@pytest.mark.parametrize("wrong_type", [123456, 123456.0, True, b"123456"])
def test_non_string_otp_is_invalid_argument(seeded: Path, wrong_type: object) -> None:
    """禁浮点/禁非字符串：OTP 必须是字符串，别让数值型悄悄过校验。"""
    assert _call(SAVINGS, "lock", otp=wrong_type).error_code == ErrorCode.INVALID_ARGUMENT


def test_ref_bound_to_another_card_is_forbidden(seeded: Path) -> None:
    result = _call(SAVINGS, "lock", ref=_ref(CREDIT))
    assert result.error_code == ErrorCode.FORBIDDEN


def test_ref_bound_to_another_action_is_forbidden(seeded: Path) -> None:
    ref = sub.issue_confirm_ref(sub.CANCEL_ACTION, SAVINGS)
    assert _call(SAVINGS, "lock", ref=ref).error_code == ErrorCode.FORBIDDEN


def test_expired_ref_is_token_expired(seeded: Path, clock) -> None:
    ref = _ref(SAVINGS)
    clock.tick(seconds=sub.CONFIRM_TTL_SECONDS + 1)
    assert _call(SAVINGS, "lock", ref=ref).error_code == ErrorCode.TOKEN_EXPIRED


def test_confirm_ref_is_checked_before_otp(seeded: Path) -> None:
    """顺序：凭证 → OTP。凭证无效时报的是"凭证"那条，而不是把 OTP 对错暴露出去。"""
    bogus = _call(SAVINGS, "lock", ref="cf_made_up", otp="000000")
    wrong_otp = _call(SAVINGS, "lock", ref=_ref(SAVINGS), otp="000000")
    assert bogus.error_code == wrong_otp.error_code == ErrorCode.FORBIDDEN
    assert "凭证" in bogus.message and "验证码" not in bogus.message
    assert "验证码" in wrong_otp.message


# ---------------- L2 / L3 事实包口径 ----------------

@pytest.mark.parametrize("action", L2_ACTIONS)
def test_l2_facts_never_carry_l3_keys(seeded: Path, action: str) -> None:
    kw = {"credit_limit": 1_000} if action == "adjust_limit" else {}
    kw = {"single_limit": 1_000} if action == "set_txn_limit" else kw
    facts = _call(CREDIT, action, **kw).facts
    for key in L3_FACT_KEYS:
        assert key not in facts


@pytest.mark.parametrize("action", L3_ACTIONS)
def test_l3_facts_carry_the_delay_window(seeded: Path, action: str, clock) -> None:
    """reviewer 钉死的键名与口径：`l3_delay_seconds=60` / `revocable=True`（档位口径，非事后承诺）。"""
    card_id = SAVINGS if action == "unlock" else CREDIT
    if action == "unlock":
        _force_status(seeded, SAVINGS, "locked")
    facts = _call(card_id, action).facts
    assert facts["l3_delay_seconds"] == 60 and facts["revocable"] is True
    assert facts["l3_window_phase"] == "pre_execution" and facts["to_human"] is True
    assert facts["tier"] == "L3"


def test_l3_message_numbers_come_from_facts(seeded: Path) -> None:
    result = _call(SAVINGS, "report_lost")
    assert_covered(result.message, result.facts)
    assert "60" not in result.message                       # 窗口是编排层的事，别写进已执行的回执


def test_no_money_floats_in_receipts(seeded: Path) -> None:
    for result in (_call(CREDIT, "adjust_limit", credit_limit=888_800), _call(SAVINGS, "report_lost")):
        assert_no_money_floats(result.data, "data")
        assert_no_money_floats(result.facts, "facts")


# ---------------- 限额参数校验 ----------------

@pytest.mark.parametrize(("action", "kw"), [
    ("adjust_limit", {}),
    ("adjust_limit", {"credit_limit": -1}),
    ("adjust_limit", {"credit_limit": 8_888.0}),
    ("adjust_limit", {"credit_limit": "8888"}),
    ("set_txn_limit", {}),
    ("set_txn_limit", {"single_limit": -1}),
    ("set_txn_limit", {"daily_limit": 1.5}),
    ("set_txn_limit", {"single_limit": True}),
])
def test_limit_arguments_are_validated(seeded: Path, action: str, kw: dict) -> None:
    assert _call(CREDIT, action, **kw).error_code == ErrorCode.INVALID_ARGUMENT


def test_unknown_extra_argument_is_rejected(seeded: Path) -> None:
    """kw 白名单：多给字段（例如 cvv）直接拒，不许静默忽略。"""
    assert _call(SAVINGS, "lock", cvv="123").error_code == ErrorCode.INVALID_ARGUMENT


@pytest.mark.parametrize(("action", "kw"), [
    ("lock", {"card_type": "credit"}),
    ("lock", {"credit_limit": 1_000}),
    ("unlock", {"single_limit": 1_000}),
    ("report_lost", {"card_type": "savings"}),
    ("adjust_limit", {"single_limit": 1_000}),
    ("set_txn_limit", {"credit_limit": 1_000}),
    ("set_txn_limit", {"card_type": "credit"}),
])
def test_kw_not_belonging_to_the_action_is_rejected(seeded: Path, action: str, kw: dict) -> None:
    """参数与动作不匹配必须报错：静默忽略等于"用户以为改了、其实没改"。"""
    assert _call(SAVINGS if action != "unlock" else SAVINGS, action, **kw).error_code == (
        ErrorCode.INVALID_ARGUMENT)


def test_unknown_action_is_rejected(seeded: Path) -> None:
    for bogus in ("contactless_pay", "", "LOCK", None):
        assert card.manage_card(SAVINGS, bogus, confirm_ref=_ref(SAVINGS), otp=OTP).error_code == (
            ErrorCode.INVALID_ARGUMENT)
