"""任务卡 07 单测：T15 `trade_wealth`（申购/赎回 L2 高危写：确认凭证 + 风险匹配 + 事务 + 审计）。

口径来源：`docs/cards/card-07.md` 第 3 条 + 规格 §2 T15 + §5 权限矩阵。
T13/T14 在 `tests/test_tools_wealth_risk.py`（共享答卷常量从那里 import，避免复制漂移）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data import dao
from tools import subscription as sub, wealth
from tools import _wealth_risk
from tools.schemas import ErrorCode

from tests.conftest import Clock, assert_covered, assert_no_money_floats, count, raw
from tests.test_tools_wealth_risk import AGGRESSIVE, BALANCED, CONSERVATIVE

MMF = "prod_r1_mmf"           # R1, term=0, 起购 100 分；seed 已有持仓 2_000_000 分
BOND90 = "prod_r2_bond90"     # R2, term=90, 起购 10_000 分；seed 持仓 5_000_000 分
BOND180 = "prod_r2_bond180"   # R2, term=180, 起购 100_000 分；无持仓
GROWTH = "prod_r5_growth"     # R5, 起购 10_000_000 分


def _assess(answers: dict = BALANCED) -> str:
    result = wealth.assess_risk(answers)
    assert result.ok, result.message
    return result.data["risk_level"]


def _ref(product_id: str) -> str:
    return sub.issue_confirm_ref(wealth.TRADE_ACTION, product_id)


def _balance(path: Path) -> dict:
    return raw(path, "SELECT balance, available FROM account WHERE type = 'savings'")[0]


def _holdings(path: Path, product_id: str) -> list[dict]:
    return raw(path, "SELECT * FROM holding WHERE product_id = ? ORDER BY id", (product_id,))


def _trade(path: Path, product_id: str, action: str, amount: int, ref: str | None = None):
    return wealth.trade_wealth(product_id, action, amount, _ref(product_id) if ref is None else ref)


# ---------------- 冻结面 / 正常路径 ----------------

def test_t15_data_fields_are_frozen_to_the_spec(seeded: Path) -> None:
    _assess()
    result = _trade(seeded, BOND90, "buy", 100_000)
    assert result.ok, result.message
    assert set(result.data) == {"order_id", "product_name", "amount", "expected_confirm_date"}
    assert result.data["expected_confirm_date"] == "2026-09-13"        # T+1（锚 AS_OF）


def test_t15_buy_moves_money_writes_holding_txn_and_audit(seeded: Path) -> None:
    _assess()
    before = _balance(seeded)["balance"]
    txns_before = count(seeded, "txn")
    audits_before = count(seeded, "audit_log")
    result = _trade(seeded, BOND90, "buy", 100_000)
    assert result.ok and result.data["amount"] == 100_000
    assert _balance(seeded)["balance"] == before - 100_000             # 扣款
    rows = [row for row in _holdings(seeded, BOND90) if row["amount"] == 100_000]
    assert len(rows) == 1 and rows[0]["status"] == "held" and rows[0]["purchase_date"] == "2026-09-12"
    assert count(seeded, "txn") == txns_before + 1
    txn = raw(seeded, "SELECT * FROM txn ORDER BY rowid DESC LIMIT 1")[0]
    assert txn["amount"] == -100_000 and txn["category"] == "理财" and txn["counterparty"] == "稳健纯债 90 天"
    assert count(seeded, "audit_log") == audits_before + 1
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert audit["intent"] == "wealth_buy" and audit["tool"] == "trade_wealth"
    assert audit["permission_tier"] == "L2" and audit["result"] == "success"
    assert "prod_r2_bond90" in audit["params_json"]


def test_t15_min_amount_boundary(seeded: Path) -> None:
    _assess()
    assert _trade(seeded, BOND90, "buy", 10_000).ok                     # 恰好等于起购额（10000 分）
    assert _trade(seeded, BOND90, "buy", 9_999).error_code == ErrorCode.INVALID_ARGUMENT


def test_t15_insufficient_funds(seeded: Path) -> None:
    _assess(AGGRESSIVE)                                                 # R5 用户 → 先过风险匹配
    result = _trade(seeded, GROWTH, "buy", 10_000_000)                   # R5 起购 10 万元 > 余额
    assert result.error_code == ErrorCode.INSUFFICIENT_FUNDS
    assert _balance(seeded)["balance"] == 4_663_400                     # 未动


# ---------------- 风险等级匹配（卡 07 必查点） ----------------

@pytest.mark.parametrize("product_id", [BOND90, BOND180, "prod_r3_mixed365", "prod_r4_quant720", GROWTH])
def test_t15_low_risk_user_cannot_buy_higher_risk(seeded: Path, product_id: str) -> None:
    """R1 用户买 R2/R3/R4/R5 → FORBIDDEN（+ rejected 留痕），且**余额/持仓/流水零变化**。"""
    _assess(CONSERVATIVE)
    before_balance, before_txn = _balance(seeded)["balance"], count(seeded, "txn")
    before_audit = count(seeded, "audit_log")
    result = _trade(seeded, product_id, "buy", 10_000_000)
    assert result.error_code == ErrorCode.FORBIDDEN, result.message
    assert _balance(seeded)["balance"] == before_balance and count(seeded, "txn") == before_txn
    assert count(seeded, "audit_log") == before_audit + 1
    assert raw(seeded, "SELECT result FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]["result"] == "rejected"


def test_t15_same_level_is_allowed(seeded: Path) -> None:
    _assess(CONSERVATIVE)                                               # R1
    assert _trade(seeded, MMF, "buy", 100).ok                           # R1 产品 → 放行


def test_t15_unassessed_user_cannot_buy(seeded: Path) -> None:
    """未测评边界：没有测评结果 → INVALID_STATE（提示先测评），一分钱不动。"""
    before = _balance(seeded)["balance"]
    result = _trade(seeded, MMF, "buy", 100)
    assert result.error_code == ErrorCode.INVALID_STATE and "测评" in result.message
    assert _balance(seeded)["balance"] == before


def test_t15_expired_assessment_blocks_buy(seeded: Path) -> None:
    _assess()
    _wealth_risk._ASSESSMENTS["u_zhangsan_0001"]["valid_until"] = "2026-01-01"
    assert _trade(seeded, BOND90, "buy", 100_000).error_code == ErrorCode.INVALID_STATE


def test_t15_risk_check_uses_the_products_own_level(seeded: Path) -> None:
    """R3 用户可买 R3 及以下、不可买 R4/R5（逐档核对产品自身的 risk_level 列）。"""
    assert _assess() == "R3"
    assert _trade(seeded, "prod_r3_mixed365", "buy", 1_000_000).ok
    assert _trade(seeded, "prod_r4_quant720", "buy", 5_000_000).error_code == ErrorCode.FORBIDDEN


# ---------------- 确认凭证 ----------------

def test_t15_requires_a_confirm_ref(seeded: Path) -> None:
    _assess()
    assert wealth.trade_wealth(BOND90, "buy", 100_000, "cf_fake").error_code == ErrorCode.FORBIDDEN
    assert wealth.trade_wealth(BOND90, "buy", 100_000, "").error_code == ErrorCode.INVALID_ARGUMENT


def test_t15_ref_is_bound_to_the_product(seeded: Path) -> None:
    _assess()
    assert _trade(seeded, BOND90, "buy", 100_000, _ref(MMF)).error_code == ErrorCode.FORBIDDEN


def test_t15_ref_is_bound_to_the_tool(seeded: Path) -> None:
    _assess()
    cancel_ref = sub.issue_confirm_ref(sub.CANCEL_ACTION, BOND90)
    assert _trade(seeded, BOND90, "buy", 100_000, cancel_ref).error_code == ErrorCode.FORBIDDEN


def test_t15_expired_ref_is_token_expired(seeded: Path, clock: Clock) -> None:
    _assess()
    ref = _ref(BOND90)
    clock.tick(seconds=sub.CONFIRM_TTL_SECONDS + 1)
    result = _trade(seeded, BOND90, "buy", 100_000, ref)
    assert result.error_code == ErrorCode.TOKEN_EXPIRED            # 良性超时，不记 rejected
    assert _balance(seeded)["balance"] == 4_663_400


def test_t15_same_ref_replays_once(seeded: Path) -> None:
    """幂等：同 ref 重复调用返回同一结果，只扣一次款、只写一条流水/审计、只建一行持仓。"""
    _assess()
    ref = _ref(BOND90)
    first = _trade(seeded, BOND90, "buy", 100_000, ref)
    assert first.ok
    balance_after, txns_after, audits_after = _balance(seeded)["balance"], count(seeded, "txn"), count(seeded, "audit_log")
    for _ in range(3):
        again = _trade(seeded, BOND90, "buy", 100_000, ref)
        assert again.ok and again.data == first.data and again.facts == first.facts
    assert _balance(seeded)["balance"] == balance_after
    assert count(seeded, "txn") == txns_after and count(seeded, "audit_log") == audits_after
    assert len([row for row in _holdings(seeded, BOND90) if row["amount"] == 100_000]) == 1


def test_t15_new_ref_is_a_new_trade(seeded: Path) -> None:
    """同 ref 重放=幂等；**新 ref = 新的一笔**（口径写进交付说明）。"""
    _assess()
    _trade(seeded, BOND90, "buy", 100_000)
    _trade(seeded, BOND90, "buy", 100_000)
    assert _balance(seeded)["balance"] == 4_663_400 - 200_000
    assert len([row for row in _holdings(seeded, BOND90) if row["amount"] == 100_000]) == 2
