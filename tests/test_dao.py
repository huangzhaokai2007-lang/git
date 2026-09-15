"""任务卡 03 单测（卡 05b 拆分后）：14 个公开读函数的正常 / 边界用例（外键、窗口含尾日、空库、LIKE 字面量）。

脚手架（seeded / blank / raw / count）在 `tests/conftest.py`；连接原语、非法参数表、幂等时钟与
04b 新增写原语的用例在 `tests/test_dao_core.py`。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data import _dao_core, dao
from data.db import transaction
from data.seed import SAVINGS_ID, USER_ID

from tests.conftest import (count, raw)

ACCOUNT = SAVINGS_ID
STAMP = "2026-08-31T23:59:00"

def test_get_balance_matches_account_rows(seeded: Path) -> None:
    assert dao.get_balance("savings") == raw(seeded, "SELECT * FROM account WHERE type = 'savings'")[0]
    credit = dao.get_balance("credit")
    assert credit == raw(seeded, "SELECT * FROM account WHERE type = 'credit'")[0]
    assert isinstance(credit["balance"], int) and credit["balance"] < 0 < credit["available"]


def test_list_txn_full_range_counts_all_rows_but_limits_page(seeded: Path) -> None:
    page = dao.list_txn("2025-09-01", "2026-08-31", limit=500)
    assert page["total_count"] == count(seeded, "txn") == 600
    assert len(page["items"]) == 500                                  # limit 生效，total_count 不受影响
    stamps = [item["ts"] for item in page["items"]]
    assert stamps == sorted(stamps, reverse=True)                     # 时间倒序
    assert dao.list_txn("2025-09-01", "2026-08-31", limit=1)["total_count"] == 600


def test_list_txn_month_window_category_and_min_amount_match_sql(seeded: Path) -> None:
    month = dao.list_txn("2026-03-01", "2026-03-31", limit=500)
    expect = raw(seeded, "SELECT COUNT(*) AS n FROM txn WHERE substr(ts, 1, 7) = '2026-03'")[0]["n"]
    assert month["total_count"] == expect == len(month["items"])
    food = dao.list_txn("2026-03-01", "2026-03-31", category="餐饮", limit=500)
    assert food["total_count"] == raw(
        seeded, "SELECT COUNT(*) AS n FROM txn WHERE category = '餐饮' AND substr(ts, 1, 7) = '2026-03'"
    )[0]["n"]
    assert {item["category"] for item in food["items"]} == {"餐饮"}
    big = dao.list_txn("2025-09-01", "2026-08-31", min_amount=100_000, limit=500)
    assert big["items"] and all(abs(item["amount"]) >= 100_000 for item in big["items"])
    assert big["total_count"] == raw(
        seeded, "SELECT COUNT(*) AS n FROM txn WHERE amount >= 100000 OR amount <= -100000")[0]["n"]


def test_list_txn_window_includes_the_last_day(seeded: Path) -> None:
    last = max(row["ts"][:10] for row in raw(seeded, "SELECT ts FROM txn"))
    day = dao.list_txn(last, last, limit=500)
    assert day["total_count"] == len([r for r in raw(seeded, "SELECT ts FROM txn") if r["ts"][:10] == last]) > 0


def test_sum_by_category_matches_sql_grouping(seeded: Path) -> None:
    got = dao.sum_by_category("2026-03")
    assert got == raw(seeded, "SELECT category, SUM(amount) AS amount, COUNT(*) AS count FROM txn"
                              " WHERE substr(ts, 1, 7) = '2026-03' GROUP BY category ORDER BY category")
    assert sum(group["amount"] for group in got) == raw(
        seeded, "SELECT SUM(amount) AS s FROM txn WHERE substr(ts, 1, 7) = '2026-03'")[0]["s"]
    assert sum(group["count"] for group in got) == raw(
        seeded, "SELECT COUNT(*) AS n FROM txn WHERE substr(ts, 1, 7) = '2026-03'")[0]["n"]


def test_sum_by_category_year_covers_all_months_of_that_year(seeded: Path) -> None:
    year = {g["category"]: g["amount"] for g in dao.sum_by_category("2026")}
    expect = {r["category"]: r["amount"] for r in raw(
        seeded, "SELECT category, SUM(amount) AS amount FROM txn WHERE substr(ts, 1, 7) LIKE '2026-%'"
                " GROUP BY category")}
    assert year == expect and year["工资"] > 0                        # 收入为正、支出为负（代数和）
    assert all(g["amount"] < 0 for g in dao.sum_by_category("2026-03") if g["category"] == "订阅")


def test_find_payee_returns_all_same_name_candidates(seeded: Path) -> None:
    got = dao.find_payee("李四")
    assert [p["name"] for p in got] == ["李四", "李四"] and len({p["phone"] for p in got}) == 2
    assert got == raw(seeded, "SELECT * FROM payee WHERE name = '李四' ORDER BY name, id")


def test_find_payee_matches_phone_and_bank(seeded: Path) -> None:
    assert {p["id"] for p in dao.find_payee("139")} == {"payee_0001", "payee_0002"}
    assert [p["id"] for p in dao.find_payee("工商银行")] == ["payee_0002"]
    assert dao.find_payee("赵六")[0]["last_used_ts"] is None


def test_get_card_and_update_card_persist(seeded: Path) -> None:
    assert dao.get_card("card_savings_0001") == raw(seeded, "SELECT * FROM card WHERE id = 'card_savings_0001'")[0]
    first = dao.update_card("card_savings_0001", status="locked")
    assert first is not None and first["status"] == "locked"
    assert raw(seeded, "SELECT status FROM card WHERE id = 'card_savings_0001'")[0]["status"] == "locked"
    assert dao.update_card("card_savings_0001", status="locked") == first          # 幂等
    assert count(seeded, "card") == 3
    limit = dao.update_card("card_credit_0002", credit_limit=5_000_000, single_limit=None)
    assert (limit["credit_limit"], limit["single_limit"]) == (5_000_000, None)


def test_update_card_does_not_apply_guard_rules(seeded: Path) -> None:
    """已挂失的卡也照改：状态机与权限判定是 guard/ 的活，DAO 不做业务判断。"""
    assert raw(seeded, "SELECT status FROM card WHERE id = 'card_savings_0003'")[0]["status"] == "lost"
    changed = dao.update_card("card_savings_0003", status="normal")
    assert changed is not None and changed["status"] == "normal"


def test_list_subscriptions_by_user_and_status(seeded: Path) -> None:
    active = dao.list_subscriptions(USER_ID, "active")
    assert {s["id"] for s in active} == {"sub_0001", "sub_0002", "sub_0003", "sub_0004"}
    assert [s["next_charge_date"] for s in active] == sorted(s["next_charge_date"] for s in active)
    assert {s["id"] for s in dao.list_subscriptions(USER_ID, "cancelled")} == {"sub_0005", "sub_0006"}


def test_get_subscription_and_update_subscription_cancel(seeded: Path) -> None:
    sub = dao.get_subscription("sub_0005")
    assert sub == raw(seeded, "SELECT * FROM subscription WHERE id = 'sub_0005'")[0]
    updated = dao.update_subscription("sub_0001", status="cancelled", next_charge_date="2026-10-18")
    assert (updated["status"], updated["next_charge_date"]) == ("cancelled", "2026-10-18")
    assert "sub_0001" in {s["id"] for s in dao.list_subscriptions(USER_ID, "cancelled")}
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0001'")[0]["status"] == "cancelled"


def test_list_and_get_products(seeded: Path) -> None:
    every = dao.list_products()
    assert every == raw(seeded, "SELECT * FROM wealth_product ORDER BY min_amount, id") and len(every) == 6
    assert [p["min_amount"] for p in every] == sorted(p["min_amount"] for p in every)
    assert {p["risk_level"] for p in dao.list_products("R2")} == {"R2"} and len(dao.list_products("R2")) == 2
    assert dao.get_product("prod_r3_mixed365")["risk_level"] == "R3"


def test_insert_txn_persists_and_is_visible_to_list_txn(seeded: Path) -> None:
    row = dao.insert_txn(ACCOUNT, STAMP, -12_345, "out", 100, counterparty="悦食快餐", category="餐饮",
                         channel="二维码", memo="午饭", id="txn_test_0001")
    assert row == raw(seeded, "SELECT * FROM txn WHERE id = 'txn_test_0001'")[0] and len(row) == 10
    assert (row["amount"], row["direction"], row["balance_after"], row["memo"]) == (-12_345, "out", 100, "午饭")
    assert dao.list_txn("2026-08-31", "2026-08-31", limit=500)["items"][0]["id"] == "txn_test_0001"
    assert dao.insert_txn(ACCOUNT, STAMP, 500, "in", 600)["direction"] == "in"
    assert count(seeded, "txn") == 602


def test_insert_txn_generates_unique_ids(seeded: Path) -> None:
    first = dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1)
    second = dao.insert_txn(ACCOUNT, STAMP, -200, "out", 1)
    assert first["id"] != second["id"] and first["id"].startswith("txn_")


def test_insert_txn_is_idempotent_and_rejects_conflicting_id(seeded: Path) -> None:
    first = dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1, id="txn_dup")
    assert dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1, id="txn_dup") == first
    assert count(seeded, "txn") == 601
    with pytest.raises(ValueError, match="幂等键冲突"):
        dao.insert_txn(ACCOUNT, STAMP, -999, "out", 1, id="txn_dup")
    assert count(seeded, "txn") == 601


def test_insert_txn_does_not_write_audit_log(seeded: Path) -> None:
    before = count(seeded, "audit_log")
    dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1)
    assert count(seeded, "audit_log") == before        # 审计由编排层显式写，DAO 不代写


def test_insert_audit_persists_and_serializes_params(seeded: Path) -> None:
    row = dao.insert_audit("trace-1", "sess-1", intent="transfer_single", tool="execute_transfer",
                           params_json={"amount": 10_000, "payee_id": "payee_0001"}, risk_level="L1",
                           permission_tier="L2", result="success")
    assert raw(seeded, "SELECT * FROM audit_log WHERE id = ?", (row["id"],))[0] == row and len(row) == 12
    assert json.loads(row["params_json"]) == {"amount": 10_000, "payee_id": "payee_0001"}
    assert (row["actor"], row["result"], row["permission_tier"], row["error_code"]) == \
        ("agent", "success", "L2", None)
    assert row["ts"]


def test_insert_audit_keeps_json_string_and_explicit_stamp(seeded: Path) -> None:
    row = dao.insert_audit("trace-2", "sess-2", params_json='{"a": 1}', actor="user",
                           ts="2026-09-12T10:00:00", result="rejected", error_code="FORBIDDEN")
    assert (row["params_json"], row["ts"], row["actor"], row["error_code"]) == \
        ('{"a": 1}', "2026-09-12T10:00:00", "user", "FORBIDDEN")


def test_insert_audit_is_idempotent(seeded: Path) -> None:
    first = dao.insert_audit("trace-9", "sess-9", id="audit_dup")
    assert dao.insert_audit("trace-9", "sess-9", id="audit_dup") == first
    with pytest.raises(ValueError, match="幂等键冲突"):
        dao.insert_audit("trace-9", "sess-9", result="error", id="audit_dup")


def test_insert_risk_event_persists(seeded: Path) -> None:
    row = dao.insert_risk_event(USER_ID, "night", trace_id="trace-1", ts="2026-03-14T02:13:00",
                               detail="凌晨大额", action_taken="to_human")
    assert raw(seeded, "SELECT * FROM risk_event WHERE id = ?", (row["id"],))[0] == row and len(row) == 7
    assert (row["factor"], row["action_taken"], row["user_id"]) == ("night", "to_human", USER_ID)


def test_insert_risk_event_idempotent_and_allows_empty_optional_fields(seeded: Path) -> None:
    first = dao.insert_risk_event(USER_ID, "velocity", id="risk_dup")
    assert dao.insert_risk_event(USER_ID, "velocity", id="risk_dup") == first
    assert (first["trace_id"], first["detail"], first["action_taken"]) == (None, None, None)
    assert first["ts"]
    with pytest.raises(ValueError, match="幂等键冲突"):
        dao.insert_risk_event(USER_ID, "night", id="risk_dup")


def test_writes_join_an_outer_transaction_and_roll_back_together(seeded: Path) -> None:
    """外层事务已开时 DAO 不嵌套 BEGIN，且随外层一起回滚（card-01 transaction() 不可嵌套）。"""
    with pytest.raises(RuntimeError):
        with transaction(dao.connection()):
            dao.insert_audit("trace-tx", "sess-tx")
            dao.insert_risk_event(USER_ID, "device")
            dao.insert_txn(ACCOUNT, STAMP, -100, "out", 1, id="txn_rollback")
            raise RuntimeError("编排层中途失败")
    assert raw(seeded, "SELECT COUNT(*) AS n FROM audit_log WHERE trace_id = 'trace-tx'")[0]["n"] == 0
    assert count(seeded, "risk_event") == 0 and count(seeded, "txn") == 600


def test_all_reads_are_empty_on_a_fresh_database(blank: Path) -> None:
    assert dao.get_balance("savings") is None
    assert dao.list_txn("2025-09-01", "2026-08-31") == {"items": [], "total_count": 0}
    assert dao.sum_by_category("2026-03") == []
    assert dao.find_payee("李四") == []
    assert dao.get_card("card_savings_0001") is None
    assert dao.list_subscriptions(USER_ID, "active") == []
    assert dao.get_subscription("sub_0001") is None
    assert dao.list_products() == []
    assert dao.get_product("prod_r1_mmf") is None


def test_writes_on_fresh_database_create_no_phantom_rows(blank: Path) -> None:
    assert dao.update_card("card_savings_0001", status="locked") is None
    assert dao.update_subscription("sub_0001", status="cancelled") is None
    assert (count(blank, "card"), count(blank, "subscription")) == (0, 0)


def test_list_txn_empty_window_and_impossible_period(seeded: Path) -> None:
    assert dao.list_txn("2020-01-01", "2020-12-31") == {"items": [], "total_count": 0}
    assert dao.sum_by_category("2030-01") == [] and dao.sum_by_category("1999") == []


def test_find_payee_no_match_and_like_wildcards_are_literal(seeded: Path) -> None:
    assert dao.find_payee("查无此人") == []
    assert dao.find_payee("%") == [] and dao.find_payee("_") == []


def test_list_subscriptions_unknown_user_or_status_without_rows(seeded: Path) -> None:
    assert dao.list_subscriptions("u_nobody", "active") == []
    assert dao.list_subscriptions(USER_ID, "paused") == []          # 合法状态但库里没有 → 空
    assert dao.get_subscription("sub_nope") is None and dao.get_product("prod_nope") is None


STAMP = "2026-08-31T23:59:00"


ACCOUNT = SAVINGS_ID
