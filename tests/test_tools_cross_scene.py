"""任务卡 07 单测：T16 `plan_gift`（跨场景联动 —— 锁定资金 + mock 预订清单）。

口径来源：`docs/cards/card-07.md` 第 4 条 + 规格 §2 T16。
价目表（`GIFT_CATALOG`）是 demo 常量、规格未给（已记「需要人类决定」），故断言里用字面量钉住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data import dao
from tools import cross_scene
from tools.schemas import ErrorCode

from tests.conftest import assert_covered, assert_no_money_floats, count, raw, write_sql

FLOWERS, CAKE = 19_900, 29_900          # 199.00 元 / 299.00 元（demo 价目）
FUTURE = "2026-10-01"


def _available(path: Path) -> dict:
    return raw(path, "SELECT balance, available FROM account WHERE type = 'savings'")[0]


def test_t16_data_fields_are_frozen_to_the_spec(seeded: Path) -> None:
    result = cross_scene.plan_gift("张小美", FUTURE, 100_000)
    assert result.ok, result.message
    assert set(result.data) == {"plan_id", "lock_id", "items", "total"}
    assert set(result.data["items"][0]) == {"item_id", "name", "price"}
    assert result.data["plan_id"].startswith("plan_") and result.data["lock_id"].startswith("lock_")


def test_t16_locks_funds_without_spending(seeded: Path) -> None:
    """「锁定」≠「支出」：只扣 `available`，`balance` 不动；并按 reviewer 口径写 audit_log。"""
    before = _available(seeded)
    audits_before = count(seeded, "audit_log")
    result = cross_scene.plan_gift("张小美", FUTURE, 100_000)
    assert result.ok and result.data["total"] == FLOWERS + CAKE
    after = _available(seeded)
    assert after["balance"] == before["balance"]                       # 余额不动
    assert after["available"] == before["available"] - result.data["total"]
    assert count(seeded, "audit_log") == audits_before + 1
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert audit["intent"] == "gift_plan" and audit["tool"] == "plan_gift"
    assert audit["permission_tier"] == "L2" and audit["result"] == "success"
    assert result.data["lock_id"] in audit["params_json"]


def test_t16_picks_the_bundle_when_budget_allows(seeded: Path) -> None:
    items = cross_scene.plan_gift("张小美", FUTURE, FLOWERS + CAKE).data["items"]
    assert [item["item_id"] for item in items] == ["gift_flowers", "gift_cake"]
    assert cross_scene.plan_gift("张小美", FUTURE, FLOWERS + CAKE - 1).data["items"]


def test_t16_degrades_to_one_item_then_refuses(seeded: Path) -> None:
    """预算不够组合 → 给单价最高的一件；一件也买不起 → INVALID_ARGUMENT。"""
    one = cross_scene.plan_gift("王五", FUTURE, CAKE).data["items"]
    assert [item["item_id"] for item in one] == ["gift_cake"] and one[0]["price"] == CAKE
    cheaper = cross_scene.plan_gift("王五", FUTURE, FLOWERS).data["items"]
    assert [item["item_id"] for item in cheaper] == ["gift_flowers"]
    refused = cross_scene.plan_gift("王五", FUTURE, FLOWERS - 1)
    assert refused.error_code == ErrorCode.INVALID_ARGUMENT


def test_t16_insufficient_available_balance(seeded: Path) -> None:
    write_sql(seeded, [("UPDATE account SET available = 1000 WHERE type = 'savings'", ())])
    before = _available(seeded)
    result = cross_scene.plan_gift("王五", FUTURE, 100_000)
    assert result.error_code == ErrorCode.INSUFFICIENT_FUNDS
    assert _available(seeded) == before                                # 未动


@pytest.mark.parametrize(("contact", "when", "budget"), [
    ("", FUTURE, 100_000), (None, FUTURE, 100_000), ("王五", "2026-01-01", 100_000),
    ("王五", "2026/10/01", 100_000), ("王五", "not-a-date", 100_000), ("王五", FUTURE, 0),
    ("王五", FUTURE, -1), ("王五", FUTURE, 100_000.0), ("王五", FUTURE, True),
])
def test_t16_bad_arguments_are_rejected(seeded: Path, contact: object, when: object,
                                       budget: object) -> None:
    assert cross_scene.plan_gift(contact, when, budget).error_code == ErrorCode.INVALID_ARGUMENT


def test_t16_today_is_allowed_but_the_past_is_not(seeded: Path) -> None:
    assert cross_scene.plan_gift("王五", "2026-09-12", 100_000).ok      # 基准日当天可下单
    assert cross_scene.plan_gift("王五", "2026-09-11", 100_000).error_code == ErrorCode.INVALID_ARGUMENT


def test_t16_audit_failure_rolls_back_the_lock(seeded: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """原子性：审计写不进去 → 锁资金必须回滚（不留"锁了钱没留痕"的半成品）。"""
    before = _available(seeded)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(dao, "insert_audit", boom)
    with pytest.raises(RuntimeError):
        cross_scene.plan_gift("王五", FUTURE, 100_000)
    assert _available(seeded) == before


def test_t16_each_call_is_a_new_plan(seeded: Path) -> None:
    """规格 T16 签名没有 `confirm_ref` → 没有幂等键：每次调用都是一份新计划（口径写进交付说明）。"""
    first = cross_scene.plan_gift("王五", FUTURE, FLOWERS)
    second = cross_scene.plan_gift("王五", FUTURE, FLOWERS)
    assert first.data["plan_id"] != second.data["plan_id"]
    assert first.data["lock_id"] != second.data["lock_id"]


def test_t16_unknown_account_fails_closed(blank: Path) -> None:
    """空库（没有张三的储蓄账户）→ 不得凭空锁钱：FORBIDDEN（fail-closed）。"""
    result = cross_scene.plan_gift("王五", FUTURE, 100_000)
    assert result.error_code == ErrorCode.FORBIDDEN


def test_t16_message_numbers_come_from_facts(seeded: Path) -> None:
    result = cross_scene.plan_gift("张小美", FUTURE, 100_000)
    assert_covered(result.message, result.facts)
    assert result.facts["total_yuan"] == "498.00"                     # 199 + 299 元
    assert_no_money_floats(result.data, "data")
    assert_no_money_floats(result.facts, "facts")


def test_t16_no_floats_and_integer_money(seeded: Path) -> None:
    result = cross_scene.plan_gift("张小美", FUTURE, 100_000)
    assert all(isinstance(item["price"], int) for item in result.data["items"])
    assert isinstance(result.data["total"], int) and isinstance(result.facts["total"], int)


def test_t16_module_red_lines() -> None:
    """不自己开事务、不发裸 BEGIN、不调 LLM、tier 常量有来源注释。"""
    source = Path(cross_scene.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)
    assert not re.search(r"""execute\(\s*['"](BEGIN|COMMIT|ROLLBACK)""", source, re.I)
    assert source.count("with transaction(conn):") == 1
    assert cross_scene.GIFT_TIER == "L2" and "来源：规格 §2 T16" in source


def test_t16_catalog_prices_are_documented_as_demo_constants() -> None:
    """价目表规格未给 → 必须是显式 demo 常量 + 来源注释（不许悄悄编数字）。"""
    assert [item["price"] for item in cross_scene.GIFT_CATALOG] == [FLOWERS, CAKE]
    source = Path(cross_scene.__file__).read_text(encoding="utf-8")
    assert "demo 常量" in source and "价目规格与 seed 均未提供" in source


def test_t16_failed_plan_never_touches_any_table(blank: Path) -> None:
    """拒单时库里必须零残留：没有账户、没有持仓、也没有凭空造出来的锁记录。"""
    assert cross_scene.plan_gift("王五", FUTURE, FLOWERS).error_code == ErrorCode.FORBIDDEN
    for table in ("account", "holding"):
        assert raw(blank, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0
