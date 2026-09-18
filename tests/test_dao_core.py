"""任务卡 03/04b 单测（卡 05b 拆分后）：数据层**内核**侧用例 —— 连接与环境变量、非法参数表、
幂等时钟（自生成 ts 的豁免边界）、`get_payee` / `update_account_balance` 两个写原语。

自生成 ts 的假时钟必须打在 `data._dao_core.datetime`（`_stamp` 住在那里）；
打错模块会变成空补丁 → 假绿，故用例里显式断言补丁生效。"""

from __future__ import annotations

import itertools
from datetime import datetime
from pathlib import Path

import pytest

from data import _dao_core, dao
from data.db import transaction
from data.seed import CREDIT_ID, SAVINGS_ID, USER_ID

from tests.conftest import (count, raw)

ACCOUNT = SAVINGS_ID
STAMP = "2026-08-31T23:59:00"

def test_connect_db_defaults_to_env_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "from_env.db"
    monkeypatch.setenv("DB_PATH", str(target))
    dao.connect_db()
    try:
        assert dao.connection().execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()["n"] == 12
        assert dao.get_balance("savings") is None
    finally:
        dao.close()
    monkeypatch.delenv("DB_PATH", raising=False)
    assert dao.connect_db(tmp_path / "explicit.db").name == "explicit.db"
    dao.close()


ILLEGAL: list[tuple[str, tuple, dict]] = [
    ("get_balance", ("checking",), {}),
    ("get_balance", (None,), {}),
    ("list_txn", ("2026/03/01", "2026-03-31"), {}),
    ("list_txn", ("2026-03-31", "2026-03-01"), {}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"limit": 0}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"limit": 501}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"limit": True}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"min_amount": 1.5}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"min_amount": -1}),
    ("list_txn", ("2026-03-01", "2026-03-31"), {"category": ""}),
    ("sum_by_category", ("2026-13",), {}),
    ("sum_by_category", ("2026-3",), {}),
    ("sum_by_category", ("去年",), {}),
    ("sum_by_category", (None,), {}),
    ("find_payee", ("",), {}),
    ("find_payee", ("   ",), {}),
    ("get_card", ("",), {}),
    ("update_card", ("card_savings_0001",), {}),
    ("update_card", ("card_savings_0001",), {"balance": 1}),
    ("update_card", ("card_savings_0001",), {"status": "gone"}),
    ("update_card", ("card_savings_0001",), {"type": "debit"}),
    ("update_card", ("card_savings_0001",), {"credit_limit": 100.5}),
    ("update_card", ("card_savings_0001",), {"credit_limit": True}),
    ("update_card", ("card_savings_0001",), {"status": "frozen", "daily_limit": "500"}),
    ("list_subscriptions", ("", "active"), {}),
    ("list_subscriptions", (USER_ID, "bogus"), {}),
    ("list_subscriptions", (USER_ID, "ACTIVE"), {}),
    ("get_subscription", ("",), {}),
    ("update_subscription", ("sub_0001",), {}),
    ("update_subscription", ("sub_0001",), {"status": "bogus"}),
    ("update_subscription", ("sub_0001",), {"cycle": "weekly"}),
    ("update_subscription", ("sub_0001",), {"amount": 15.0}),
    ("update_subscription", ("sub_0001",), {"next_charge_date": "2026/10/02"}),
    ("update_subscription", ("sub_0001",), {"user_id": "u1"}),
    ("list_products", (), {"risk_level": "R9"}),
    ("list_products", (), {"risk_level": 3}),
    ("get_product", ("",), {}),
    ("insert_txn", (ACCOUNT, STAMP, 100.5, "in", 0), {}),
    ("insert_txn", (ACCOUNT, STAMP, True, "in", 0), {}),
    ("insert_txn", (ACCOUNT, STAMP, 0, "in", 0), {}),
    ("insert_txn", (ACCOUNT, STAMP, -100, "in", 0), {}),
    ("insert_txn", (ACCOUNT, STAMP, 100, "up", 0), {}),
    ("insert_txn", (ACCOUNT, "昨天", 100, "in", 0), {}),
    ("insert_txn", (ACCOUNT, "2026-13-01T10:00:00", 100, "in", 0), {}),
    ("insert_txn", ("", STAMP, 100, "in", 0), {}),
    ("insert_txn", (ACCOUNT, STAMP, 100, "in", 1.5), {}),
    ("insert_audit", ("", "sess-1"), {}),
    ("insert_audit", ("trace-1", "sess-1"), {"actor": "robot"}),
    ("insert_audit", ("trace-1", "sess-1"), {"result": "maybe"}),
    ("insert_audit", ("trace-1", "sess-1"), {"permission_tier": "L9"}),
    ("insert_audit", ("trace-1", "sess-1"), {"params_json": "{oops"}),
    ("insert_audit", ("trace-1", "sess-1"), {"params_json": 123}),
    ("insert_risk_event", (USER_ID, "weather"), {}),
    ("insert_risk_event", (USER_ID, "night"), {"action_taken": "ignore"}),
    ("insert_risk_event", ("", "night"), {}),
    ("insert_risk_event", (USER_ID, "night"), {"ts": "昨天"}),
]


WRITES = [case for case in ILLEGAL if case[0].startswith(("insert", "update"))]


@pytest.mark.parametrize("name,args,kwargs", ILLEGAL, ids=[f"{n}-{i}" for i, (n, _, _) in enumerate(ILLEGAL)])
def test_dao_rejects_illegal_arguments(seeded: Path, name: str, args: tuple, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        getattr(dao, name)(*args, **kwargs)


@pytest.mark.parametrize("name,args,kwargs", WRITES, ids=[f"{n}-{i}" for i, (n, _, _) in enumerate(WRITES)])
def test_rejected_write_leaves_database_untouched(seeded: Path, name: str, args: tuple, kwargs: dict) -> None:
    before = {table: count(seeded, table) for table in ("txn", "card", "subscription", "audit_log", "risk_event")}
    with pytest.raises(ValueError):
        getattr(dao, name)(*args, **kwargs)
    assert {table: count(seeded, table) for table in before} == before


def test_unknown_update_field_is_reported_with_whitelist(seeded: Path) -> None:
    with pytest.raises(ValueError, match="不允许改字段"):
        dao.update_card("card_savings_0001", cvv="123")
    with pytest.raises(ValueError, match="不允许改字段"):
        dao.update_subscription("sub_0001", user_id="u_other")


def test_credit_account_id_is_not_confused_with_savings(seeded: Path) -> None:
    """信用账户余额为负（未还欠款），DAO 不做任何"余额恒为正"的业务假设。"""
    credit = dao.get_balance("credit")
    assert credit["id"] == CREDIT_ID and credit["balance"] < 0
    assert dao.get_balance("savings")["id"] == SAVINGS_ID


def test_retry_across_a_second_boundary_is_still_idempotent(seeded: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """同一业务内容的重试即使跨过秒边界也必须返回既有行（自生成的 ts 不算"内容"）。

    用假时钟把 now() 每次调用前进 1 秒，把偶发的秒边界抖动变成必然触发的确定性用例。
    """
    ticks = itertools.count()

    class Clock(datetime):                     # 只替换 dao 模块里的 datetime 名字
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 12, 10, 0, next(ticks))

    monkeypatch.setattr(_dao_core, "datetime", Clock)   # 目标必须是有 _stamp 的模块（卡 05b）
    assert _dao_core._stamp(None).startswith("2026-09-12T10:00:")   # 补丁必须真的生效（防空补丁假绿）
    before = {table: count(seeded, table) for table in ("txn", "audit_log", "risk_event")}

    risk = dao.insert_risk_event(USER_ID, "velocity", id="risk_retry")
    assert dao.insert_risk_event(USER_ID, "velocity", id="risk_retry") == risk
    audit = dao.insert_audit("trace-retry", "sess-retry", id="audit_retry")
    assert dao.insert_audit("trace-retry", "sess-retry", id="audit_retry") == audit
    txn = dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1, id="txn_retry")
    assert dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1, id="txn_retry") == txn
    assert {table: count(seeded, table) for table in before} == {t: n + 1 for t, n in before.items()}
    assert raw(seeded, "SELECT COUNT(*) AS n FROM txn WHERE id = 'txn_retry'")[0]["n"] == 1


def test_explicitly_different_ts_still_conflicts(seeded: Path) -> None:
    """调用方显式给了 ts：同 id 但 ts 不同仍须报冲突（豁免只针对自动生成的值）。"""
    dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1, id="txn_ts_conflict")
    with pytest.raises(ValueError, match="幂等键冲突"):
        dao.insert_txn(ACCOUNT, "2026-08-31T23:58:00", -100, "out", 1, id="txn_ts_conflict")


def test_get_payee_returns_the_whole_row(seeded: Path) -> None:
    row = dao.get_payee("payee_0001")
    assert row == raw(seeded, "SELECT * FROM payee WHERE id = ?", ("payee_0001",))[0]
    assert row is not None and row["user_id"] == USER_ID and isinstance(row["is_whitelist"], int)


def test_get_payee_unknown_id_is_none(seeded: Path) -> None:
    assert dao.get_payee("payee_nope") is None


@pytest.mark.parametrize("bad", ["", "   ", None, 7, ["payee_0001"]])
def test_get_payee_rejects_illegal_arguments(seeded: Path, bad: object) -> None:
    with pytest.raises(ValueError):
        dao.get_payee(bad)                                       # type: ignore[arg-type]


def test_update_account_balance_moves_both_columns(seeded: Path) -> None:
    """扣款式增量：`balance` 与 `available` 同步，返回值与库内一致，全程整数分。"""
    before = raw(seeded, "SELECT * FROM account WHERE id = ?", (ACCOUNT,))[0]
    row = dao.update_account_balance(ACCOUNT, -25_000)
    assert row == raw(seeded, "SELECT * FROM account WHERE id = ?", (ACCOUNT,))[0]
    assert (row["balance"], row["available"]) == (before["balance"] - 25_000, before["available"] - 25_000)
    assert isinstance(row["balance"], int) and not isinstance(row["balance"], bool)


def test_update_account_balance_accepts_zero_and_symmetric_deltas(seeded: Path) -> None:
    """边界：0 是合法增量（重放场景）；正负对称（退款回冲）。"""
    start = dao.get_balance("savings")["balance"]
    assert dao.update_account_balance(ACCOUNT, 0)["balance"] == start
    assert dao.update_account_balance(ACCOUNT, 1)["balance"] == start + 1
    assert dao.update_account_balance(ACCOUNT, -1)["balance"] == start


def test_update_account_balance_unknown_account_is_none_and_harmless(seeded: Path) -> None:
    before = raw(seeded, "SELECT id, balance FROM account ORDER BY id")
    assert dao.update_account_balance("acc_nope", 100) is None
    assert raw(seeded, "SELECT id, balance FROM account ORDER BY id") == before


@pytest.mark.parametrize("bad", [1.5, True, False, "100", None, [100]])
def test_update_account_balance_rejects_illegal_delta(seeded: Path, bad: object) -> None:
    """金额必须整数分：float / bool / 字符串 / None 一律拒，且库内余额分毫不动。"""
    before = raw(seeded, "SELECT balance FROM account WHERE id = ?", (ACCOUNT,))[0]["balance"]
    with pytest.raises(ValueError):
        dao.update_account_balance(ACCOUNT, bad)                 # type: ignore[arg-type]
    assert raw(seeded, "SELECT balance FROM account WHERE id = ?", (ACCOUNT,))[0]["balance"] == before


def test_update_account_balance_joins_an_outer_transaction(seeded: Path) -> None:
    """外层事务回滚 → 余额一并回退（与 insert_txn 同事务的原子性前提；卡 01 的 transaction 不可嵌套）。"""
    before = raw(seeded, "SELECT balance FROM account WHERE id = ?", (ACCOUNT,))[0]["balance"]
    with pytest.raises(RuntimeError):
        with transaction(dao.connection()):
            dao.update_account_balance(ACCOUNT, -1_000)
            raise RuntimeError("模拟编排层中途失败")
    assert raw(seeded, "SELECT balance FROM account WHERE id = ?", (ACCOUNT,))[0]["balance"] == before


@pytest.fixture()
def ticking_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 dao 模块里的时钟换成每次调用前进 1 秒的假时钟：幂等用例不靠真实秒边界的巧合。"""
    ticks = itertools.count()

    class Clock(datetime):                     # 只替换 dao 模块里的 datetime 名字
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 12, 11, 0, next(ticks))

    monkeypatch.setattr(_dao_core, "datetime", Clock)   # 目标必须是有 _stamp 的模块（卡 05b）


def test_insert_txn_with_self_generated_ts_is_idempotent(seeded: Path, ticking_clock: None) -> None:
    """ts=None 即自生成：同 id 同内容连续两次 → 第二次返回既有行、不产生第二行。

    假时钟每次调用前进 1 秒，所以"两次生成的 ts 恰好相同"不可能靠巧合成立：豁免路径一旦失效，
    第二次调用就会因 ts 不同而误报冲突。末尾的断言证明补丁确实生效（防空补丁假绿）。
    """
    before = count(seeded, "txn")
    first = dao.insert_txn(ACCOUNT, None, -12_345, "out", 100, id="txn_auto")
    assert first["ts"] == "2026-09-12T11:00:00"
    assert dao.insert_txn(ACCOUNT, None, -12_345, "out", 100, id="txn_auto") == first
    assert count(seeded, "txn") == before + 1
    assert raw(seeded, "SELECT COUNT(*) AS n FROM txn WHERE id = 'txn_auto'")[0]["n"] == 1
    later = dao.insert_txn(ACCOUNT, None, -12_345, "out", 100, id="txn_auto_2")   # 别 id，看时钟在走
    assert datetime.fromisoformat(later["ts"]) > datetime.fromisoformat(first["ts"])


def test_self_generated_ts_never_exempts_amount(seeded: Path, ticking_clock: None) -> None:
    """豁免只针对自生成的 ts：ts=None、同 id、异 amount 必须 ValueError（金额永远参与比对）。"""
    first = dao.insert_txn(ACCOUNT, None, -12_345, "out", 100, id="txn_auto_amt")
    before = count(seeded, "txn")
    with pytest.raises(ValueError, match="幂等键冲突"):
        dao.insert_txn(ACCOUNT, None, -99_999, "out", 100, id="txn_auto_amt")
    assert count(seeded, "txn") == before                    # 冲突不留半行
    assert raw(seeded, "SELECT amount FROM txn WHERE id = 'txn_auto_amt'")[0]["amount"] == -12_345
    assert first["amount"] == -12_345
