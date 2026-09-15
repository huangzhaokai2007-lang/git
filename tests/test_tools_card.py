"""任务卡 06 单测：T12 卡片管理（六 action 状态机 + L2/L3 双因子 + apply 的 mock 口径）。

seed 的卡：`card_credit_0002`(credit, normal) / `card_savings_0001`(savings, normal) / `card_savings_0003`(savings, lost)。
口径来源：`docs/cards/card-06.md` 第 3~4 条 + 规格 §2 T12 + §5 权限矩阵 + §4 状态机。
"""


from __future__ import annotations

from pathlib import Path

from tools import card, subscription as sub
from tools.schemas import ErrorCode

from tests.conftest import OTP, count, raw, write_sql

CREDIT = "card_credit_0002"       # credit, normal
SAVINGS = "card_savings_0001"    # savings, normal
LOST = "card_savings_0003"       # savings, 已挂失（seed 自带）
L2_ACTIONS = ("apply", "adjust_limit", "set_txn_limit", "lock")
L3_ACTIONS = ("unlock", "report_lost")
L3_FACT_KEYS = ("l3_delay_seconds", "revocable", "l3_window_phase", "to_human")


def _ref(card_id: str) -> str:
    return sub.issue_confirm_ref(card.MANAGE_ACTION, card_id)


def _call(card_id: str, action: str, *, ref: str | None = None, otp: object = OTP, **kw: object):
    kwargs: dict = {"confirm_ref": _ref(card_id) if ref is None else ref, **kw}
    if otp is not None:
        kwargs["otp"] = otp
    return card.manage_card(card_id, action, **kwargs)


def _force_status(path: Path, card_id: str, status: str) -> None:
    write_sql(path, [("UPDATE card SET status = ? WHERE id = ?", (status, card_id))])


def _row(path: Path, card_id: str) -> dict:
    return raw(path, "SELECT * FROM card WHERE id = ?", (card_id,))[0]


# ---------------- 冻结面 / 权限档 ----------------

def test_action_tiers_match_the_spec_matrix() -> None:
    """规格 §5：L2 = 确认卡 + OTP；L3 = 双因子 + 延迟 60s。apply=L2、report_lost=L3 是规格明写的。"""
    assert set(card.ACTION_TIERS) == set(L2_ACTIONS) | set(L3_ACTIONS)
    assert all(card.ACTION_TIERS[action] == "L2" for action in L2_ACTIONS)
    assert all(card.ACTION_TIERS[action] == "L3" for action in L3_ACTIONS)


def test_data_is_the_card_row_snapshot(seeded: Path) -> None:
    result = _call(SAVINGS, "lock")
    assert set(result.data) == {"id", "user_id", "account_id", "card_no_mask", "type", "credit_limit",
                                "single_limit", "daily_limit", "status"}


def test_intent_mapping_covers_every_action() -> None:
    assert set(card.ACTION_INTENTS) == set(card.ACTION_TIERS)
    assert set(card.ACTION_INTENTS.values()) == {
        "card_apply", "card_limit_adjust", "card_lock", "card_unlock", "card_report_lost"}


# ---------------- 正常路径 ----------------

def test_lock_then_unlock_round_trip(seeded: Path) -> None:
    locked = _call(SAVINGS, "lock")
    assert locked.ok and locked.data["status"] == "locked" and locked.facts["status_before"] == "normal"
    assert _row(seeded, SAVINGS)["status"] == "locked"
    unlocked = _call(SAVINGS, "unlock")
    assert unlocked.ok and unlocked.data["status"] == "normal" and unlocked.facts["tier"] == "L3"
    assert _row(seeded, SAVINGS)["status"] == "normal"


def test_report_lost_marks_the_card_lost(seeded: Path) -> None:
    result = _call(SAVINGS, "report_lost")
    assert result.ok and result.data["status"] == "lost"
    assert result.facts["status_before"] == "normal" and result.facts["tier"] == "L3"
    assert _row(seeded, SAVINGS)["status"] == "lost"


def test_lost_card_cannot_be_unlocked(seeded: Path) -> None:
    """卡 06 第 4 条：已挂失的卡不能解挂，只能补卡。"""
    assert _call(LOST, "unlock").error_code == ErrorCode.INVALID_STATE
    assert _row(seeded, LOST)["status"] == "lost"


def test_adjust_limit_happy_path(seeded: Path) -> None:
    before = _row(seeded, CREDIT)
    result = _call(CREDIT, "adjust_limit", credit_limit=888_800)
    assert result.ok and result.data["credit_limit"] == 888_800
    assert result.facts["credit_limit"] == 888_800 and result.facts["credit_limit_yuan"] == "8,888.00"
    after = _row(seeded, CREDIT)
    assert after["credit_limit"] == 888_800
    for untouched in ("single_limit", "daily_limit", "status", "card_no_mask", "account_id"):
        assert after[untouched] == before[untouched]          # 只动白名单字段


def test_set_txn_limit_happy_path(seeded: Path) -> None:
    result = _call(SAVINGS, "set_txn_limit", single_limit=188_800, daily_limit=1_888_800)
    assert result.ok
    assert result.facts["single_limit_yuan"] == "1,888.00"
    assert result.facts["daily_limit_yuan"] == "18,888.00"
    row = _row(seeded, SAVINGS)
    assert (row["single_limit"], row["daily_limit"]) == (188_800, 1_888_800)


def test_set_txn_limit_accepts_a_single_side(seeded: Path) -> None:
    before = _row(seeded, SAVINGS)
    result = _call(SAVINGS, "set_txn_limit", single_limit=99_900)
    assert result.ok and result.data["single_limit"] == 99_900
    assert result.data["daily_limit"] == before["daily_limit"]       # 另一档保持不动


def test_lock_writes_one_audit_row_with_the_spec_intent(seeded: Path) -> None:
    before = count(seeded, "audit_log")
    _call(SAVINGS, "lock")
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["intent"] == "card_lock" and row["tool"] == "manage_card"
    assert row["permission_tier"] == "L2" and row["risk_level"] == "L2" and row["result"] == "success"
    assert row["session_id"] == "session-test" and row["trace_id"]
    assert SAVINGS in row["params_json"] and "normal" in row["params_json"]


# ---------------- apply：不落库的申请单 ----------------

def test_apply_never_creates_a_card_row(seeded: Path) -> None:
    """规格没有申请表、DAO 没有建卡原语 → apply 走 mock：**不建行**，只出申请快照 + 审计。"""
    before = count(seeded, "card")
    result = _call(SAVINGS, "apply", card_type="credit")
    assert result.ok and count(seeded, "card") == before
    assert result.data["id"] is None and result.data["card_no_mask"] is None
    assert result.data["ref_card_id"] == SAVINGS
    assert result.data["status"] == "pending_review"
    assert result.data["request_id"].startswith("cardreq_")
    assert result.facts["pending"] is True and result.facts["mocked"] is True
    assert _row(seeded, SAVINGS)["status"] == "normal"        # 参考卡本身不被改动


def test_apply_audit_is_marked_pending(seeded: Path) -> None:
    _call(SAVINGS, "apply", card_type="credit")
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["intent"] == "card_apply" and row["result"] == "pending_confirm"
    assert '"mocked": true' in row["params_json"] and '"pending": true' in row["params_json"]


def test_apply_defaults_to_the_reference_card_type(seeded: Path) -> None:
    assert _call(CREDIT, "apply").data["type"] == "credit"     # card_credit_0002 → credit
    assert _call(SAVINGS, "apply").data["type"] == "savings"


def test_apply_on_a_lost_card_is_the_replacement_path(seeded: Path) -> None:
    """挂失后补卡：apply 允许以挂失卡为参考（卡 06 第 4 条"只能补卡"）。"""
    result = _call(LOST, "apply", card_type="savings")
    assert result.ok and result.data["ref_card_id"] == LOST
    assert _row(seeded, LOST)["status"] == "lost"
