"""任务卡 06 单测：T10 订阅列表（含僵尸订阅标注）+ T11 取消订阅（`confirm_ref` 确认闭环）。

时间由 conftest 的 `clock` fixture 钉死（2026-09-12 12:00）；僵尸窗口的「3 个月」在断言里写成
**字面量**（不复用被测常量 —— 复用等于自证，卡 04 的教训）。

口径来源：`docs/cards/card-06.md` 第 1~3 条 + 规格 §2 T10/T11 + §5 权限矩阵。
"""


from __future__ import annotations

from pathlib import Path

from tools import subscription as sub
from tools.schemas import ErrorCode

from tests.conftest import FOREIGN_SUB, assert_covered, assert_no_floats, count, raw, write_sql

ACTIVE_IDS = ["sub_0001", "sub_0002", "sub_0003", "sub_0004"]
ZOMBIE_ID = "sub_0004"          # 好买会员（年费，只在 2025-11 扣过一次 → 窗口内无扣费）
CANCELLED_ID = "sub_0005"       # 健悦健身（seed 里就是已取消）


def _ref(action: str = sub.CANCEL_ACTION, target: str = "sub_0001") -> str:
    return sub.issue_confirm_ref(action, target)


# ---------------- T10 list_subscriptions ----------------

def test_t10_data_fields_are_frozen_to_the_spec(seeded: Path) -> None:
    """规格 T10 的 item 字段冻结为 5 个：僵尸标注只能走 facts，不得增字段。"""
    result = sub.list_subscriptions()
    assert result.ok, result.message
    assert set(result.data) == {"items"}
    for item in result.data["items"]:
        assert set(item) == {"id", "merchant", "amount", "cycle", "next_charge_date"}


def test_t10_active_subscriptions_match_the_seed(seeded: Path) -> None:
    result = sub.list_subscriptions("active")
    assert [item["id"] for item in result.data["items"]] == ACTIVE_IDS
    assert [item["amount"] for item in result.data["items"]] == [1_500, 2_500, 1_200, 19_800]
    assert all(isinstance(item["amount"], int) for item in result.data["items"])
    assert {item["cycle"] for item in result.data["items"]} == {"monthly", "yearly"}
    assert result.facts["subscription_count"] == 4


def test_t10_zombie_is_the_yearly_subscription_only(seeded: Path) -> None:
    """近 3 个月没有对应扣费流水的订阅 → 疑似僵尸（卡 06 第 1 条）。"""
    facts = sub.list_subscriptions().facts
    assert facts["zombie_ids"] == [ZOMBIE_ID]
    assert facts["zombie_count"] == 1
    assert facts["zombie_window_months"] == 3


def test_t10_zombie_usage_record_matches_ts_and_source_txn(seeded: Path) -> None:
    """独立口径复算（自己写 SQL，不碰工具层实现）：窗口起点 = 3 个月前的月初，锚点 = 数据集的今天。

    使用记录 = `counterparty == merchant` 的流水 **或** `source_txn_id` 指向的流水落在窗口内。
    """
    charged = {row["counterparty"] for row in raw(
        seeded,
        "SELECT counterparty FROM txn WHERE ts >= '2026-06-01' AND ts <= '2026-09-12'")}
    fresh = {row["id"] for row in raw(
        seeded, "SELECT id FROM txn WHERE ts >= '2026-06-01' AND ts <= '2026-09-12'")}
    active = raw(seeded, "SELECT id, merchant, source_txn_id FROM subscription WHERE status = 'active'")
    expected = sorted(row["id"] for row in active
                      if row["merchant"] not in charged and row["source_txn_id"] not in fresh)
    result = sub.list_subscriptions()
    assert result.facts["zombie_ids"] == expected == [ZOMBIE_ID]
    assert result.facts["zombie_as_of"] == "2026-09-12"        # 锚点是固定常量（跨天可复现）


def test_t10_source_txn_in_window_counts_as_usage(seeded: Path) -> None:
    """边角：把 source_txn_id 指回窗口内的扣费 → 不再算僵尸（使用记录的第二个信号）。"""
    write_sql(seeded, [("UPDATE subscription SET source_txn_id = (SELECT id FROM txn"
                        " WHERE ts >= '2026-06-01' ORDER BY ts DESC LIMIT 1) WHERE id = ?",
                        (ZOMBIE_ID,))])
    assert sub.list_subscriptions().facts["zombie_ids"] == []


def test_t10_message_numbers_all_come_from_facts(seeded: Path) -> None:
    result = sub.list_subscriptions()
    assert_covered(result.message, result.facts)
    assert result.facts["subscription_count"] == 4 and result.facts["zombie_count"] == 1


def test_t10_no_floats_anywhere(seeded: Path) -> None:
    result = sub.list_subscriptions()
    assert_no_floats(result.data, "data")
    assert_no_floats(result.facts, "facts")


def test_t10_status_filters(seeded: Path) -> None:
    assert [item["id"] for item in sub.list_subscriptions("cancelled").data["items"]] == [
        CANCELLED_ID, "sub_0006"]
    paused = sub.list_subscriptions("paused")
    assert paused.ok and paused.data["items"] == [] and paused.facts["zombie_count"] == 0


def test_t10_invalid_status_is_rejected(seeded: Path) -> None:
    for bogus in ("all", "ACTIVE", "", 1, None):
        assert sub.list_subscriptions(bogus).error_code == ErrorCode.INVALID_ARGUMENT


def test_t10_is_read_only_and_leaves_no_audit_trail(seeded: Path) -> None:
    before = count(seeded, "audit_log")
    sub.list_subscriptions()
    assert count(seeded, "audit_log") == before


def test_t10_never_leaks_another_users_subscription(foreign: Path) -> None:
    """越权：他人（mallory）的订阅不得出现在列表里。"""
    merchants = [item["merchant"] for item in sub.list_subscriptions("active").data["items"]]
    assert merchants == ["云音乐", "星辰视频", "云端网盘", "好买会员"]
    assert raw(foreign, "SELECT merchant FROM subscription WHERE id = ?", (FOREIGN_SUB,))[0][
        "merchant"] not in merchants
    ids = [item["id"] for item in sub.list_subscriptions("cancelled").data["items"]]
    assert FOREIGN_SUB not in ids


def test_t10_empty_database_gives_empty_list(blank: Path) -> None:
    result = sub.list_subscriptions()
    assert result.ok and result.data["items"] == []
    assert result.facts == {"status": "active", "subscription_count": 0, "zombie_count": 0,
                            "zombie_ids": [], "zombie_window_months": 3,
                            "zombie_as_of": "2026-09-12", "items": []}
    assert "没有符合条件" in result.message
