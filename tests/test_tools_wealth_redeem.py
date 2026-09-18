"""任务卡 07 单测（拆分后）：T15 赎回 + 越权/原子性/参数/红线守卫。

买入路径（含风险等级匹配、确认凭证、幂等）在 `tests/test_tools_wealth_trade.py`；
共享辅助（`_assess`/`_ref`/`_balance`/`_holdings`/`_trade` 与产品常量）从那里 import，避免复制漂移。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data import dao
from tools import subscription as sub, wealth
from tools.schemas import ErrorCode

from tests.conftest import assert_covered, assert_no_money_floats, count, raw
from tests.test_tools_wealth_risk import BALANCED
from tests.test_tools_wealth_trade import (
    BOND90, BOND180, GROWTH, MMF, _assess, _balance, _holdings, _ref, _trade,
)


# ---------------- 赎回 ----------------

def test_t15_redeem_happy_path(seeded: Path) -> None:
    before = _balance(seeded)["balance"]
    result = _trade(seeded, MMF, "redeem", 500_000)
    assert result.ok and result.data["amount"] == 500_000
    assert _balance(seeded)["balance"] == before + 500_000
    holding = _holdings(seeded, MMF)[0]
    assert holding["amount"] == 2_000_000 - 500_000 and holding["status"] == "held"
    txn = raw(seeded, "SELECT * FROM txn ORDER BY rowid DESC LIMIT 1")[0]
    assert txn["amount"] == 500_000 and txn["direction"] == "in" and txn["category"] == "理财"
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert audit["intent"] == "wealth_redeem" and audit["result"] == "success"


def test_t15_redeem_all_marks_the_holding_redeemed(seeded: Path) -> None:
    assert _trade(seeded, MMF, "redeem", 2_000_000).ok
    assert _holdings(seeded, MMF)[0]["status"] == "redeemed"


def test_t15_redeem_without_a_holding_is_not_found(seeded: Path) -> None:
    assert _trade(seeded, BOND180, "redeem", 100).error_code == ErrorCode.NOT_FOUND


def test_t15_redeem_over_holding_is_rejected(seeded: Path) -> None:
    assert _trade(seeded, MMF, "redeem", 2_000_001).error_code == ErrorCode.INVALID_ARGUMENT


def test_t15_redeem_inside_the_closed_period_is_invalid_state(seeded: Path) -> None:
    """赎回规则：持有天数 ≥ term_days。刚买入的 365 天产品立刻赎回 → INVALID_STATE。"""
    _assess()
    assert _trade(seeded, "prod_r3_mixed365", "buy", 1_000_000).ok
    result = _trade(seeded, "prod_r3_mixed365", "redeem", 100_000)
    assert result.error_code == ErrorCode.INVALID_STATE and "封闭期" in result.message


def test_t15_redeem_after_the_term_is_allowed(seeded: Path) -> None:
    """seed 的 90 天债 2026-03-20 买入 → 到 2026-09-12 已持有 176 天 ≥ 90 → 可赎回。"""
    assert _trade(seeded, BOND90, "redeem", 100_000).ok


def test_t15_no_otp_parameter_so_orchestrator_confirms(seeded: Path) -> None:
    """规格 T15 签名无 `otp` → 确认卡 + OTP 在编排层；本层在 facts 声明 `requires_otp`。"""
    _assess()
    result = _trade(seeded, BOND90, "buy", 100_000)
    assert result.facts["requires_otp"] is True and result.facts["tier"] == "L2"


# ---------------- 越权 / 原子性 / 参数 ----------------

def test_t15_foreign_savings_account_is_forbidden(foreign: Path) -> None:
    """越权：DAO 取到的最低 id 账户属于他人 → fail-closed（FORBIDDEN + rejected 留痕）。"""
    _assess()
    before = count(foreign, "audit_log")
    result = _trade(foreign, BOND90, "buy", 100_000)
    assert result.error_code == ErrorCode.FORBIDDEN
    assert count(foreign, "audit_log") == before + 1
    assert raw(foreign, "SELECT result FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]["result"] == "rejected"


def test_t15_audit_failure_rolls_back_money_and_holding(seeded: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """原子性：审计写不进去 → 扣款与持仓必须一起回滚（不留"钱动了没留痕"的半成品）。"""
    _assess()
    before_balance, before_holding = _balance(seeded)["balance"], len(_holdings(seeded, BOND90))

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(dao, "insert_audit", boom)
    with pytest.raises(RuntimeError):
        _trade(seeded, BOND90, "buy", 100_000)
    assert _balance(seeded)["balance"] == before_balance
    assert len(_holdings(seeded, BOND90)) == before_holding


@pytest.mark.parametrize(("product_id", "action", "amount"), [
    (BOND90, "sell", 100_000), (BOND90, "BUY", 100_000), ("", "buy", 100_000),
    (BOND90, "buy", 0), (BOND90, "buy", -1), (BOND90, "buy", 100_000.0), (BOND90, "buy", True),
])
def test_t15_bad_arguments_are_rejected(seeded: Path, product_id: str, action: str,
                                        amount: object) -> None:
    _assess()
    assert wealth.trade_wealth(product_id, action, amount, _ref(BOND90)).error_code == (
        ErrorCode.INVALID_ARGUMENT)


def test_t15_unknown_product_is_not_found(seeded: Path) -> None:
    _assess()
    assert _trade(seeded, "prod_nope", "buy", 100).error_code == ErrorCode.NOT_FOUND


def test_t15_lock_is_released_after_every_failure_mode(seeded: Path) -> None:
    _assess()
    _trade(seeded, BOND90, "buy", 100_000, "cf_fake")
    _trade(seeded, GROWTH, "buy", 10_000_000)
    _trade(seeded, BOND90, "buy", 1)
    assert _trade(seeded, BOND90, "buy", 100_000).ok                # 临界区没被锁死


# ---------------- 红线 / 口径守卫 ----------------

def test_t15_no_floats_in_receipt(seeded: Path) -> None:
    _assess()
    result = _trade(seeded, BOND90, "buy", 100_000)
    assert_no_money_floats(result.data, "data")
    assert_no_money_floats(result.facts, "facts")
    assert isinstance(result.data["amount"], int) and isinstance(result.facts["amount"], int)


def test_t15_message_numbers_come_from_facts(seeded: Path) -> None:
    _assess()
    result = _trade(seeded, BOND90, "buy", 100_000)
    assert_covered(result.message, result.facts)
    assert result.facts["amount_yuan"] == "1,000.00"


def test_t15_module_never_calls_an_llm() -> None:
    source = Path(wealth.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)


def test_t15_write_path_opens_exactly_one_transaction() -> None:
    """台账 R1：不自己发 BEGIN/COMMIT，写路径只开一次 `transaction()`。"""
    source = Path(wealth.__file__).read_text(encoding="utf-8")
    assert not re.search(r"""execute\(\s*['"](BEGIN|COMMIT|ROLLBACK)""", source, re.I)
    assert source.count("with transaction(conn):") == 1


def test_t15_threshold_constants_are_traceable() -> None:
    assert wealth.TRADE_TIER == "L2" and wealth.CONFIRM_DAYS == 1
    source = Path(wealth.__file__).read_text(encoding="utf-8")
    for needle in ("来源：规格 §2 T15", "来源：规格 T15 的 expected_confirm_date"):
        assert needle in source


def test_t15_confirm_ref_machinery_is_imported_not_copied() -> None:
    """卡 07 第 3 条：复用 card-06 的凭证机制（import），**不复制第三份**。"""
    assert wealth.check_confirm_ref is sub.check_confirm_ref
    assert wealth.check_confirm_ref.__module__ == "tools.subscription"
    source = Path(wealth.__file__).read_text(encoding="utf-8")
    assert "from tools.subscription import" in source
    assert "_CONFIRM_REFS" not in source                 # 没有自己的凭证表
