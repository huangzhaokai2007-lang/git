"""F16 工具层单测（**Stage 1**）：T21 落预约 / T22 结算 / T23 取消。

F16 验收第 2/3 条在工具层的对应：

| 验收点 | 本文件的用例 |
|---|---|
| **预约时间到达前不能按即时转账扣款** | `test_scheduling_moves_no_money`（落预约后**一分钱都没动**） |
| 到期才扣款、**重复触发**只扣一次 | `test_settlement_debits_exactly_once`（第二次结算幂等） |
| **取消**有明确状态、不动钱 | `test_cancel_moves_no_money_and_blocks_settlement` |
| 同一凭证不重复落预约 | `test_same_token_books_only_one_schedule` |
| 档位沿用预览结论（L2 要 OTP） | `test_l2_schedule_requires_the_otp` |
| 预约时间必须晚于当前 | `test_past_schedule_time_is_rejected` |

**本文件只测工具层**：惰性结算器、写意图、取消意图属 **Stage 2**。Stage 2 完成前
止损（`agent/_write_transfer._shape_scheduled`）继续生效 —— 用户还**发不起**预约，
但工具与表已就位且被这些用例覆盖。

种子（`data/seed.py`）：`payee_0001` 李四（白名单 → L1）、`payee_0002` 李四（非白名单 → L2）。
时钟由 `clock` 夹具钉在 **2026-09-12 12:00**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import _transfer_schedule as scheduled
from tools import transfer

from tests.conftest import ACCOUNT, OTP, PAYEE, PAYEE_NEW, count, raw

FUTURE = "2026-09-13T09:00"          # 晚于被钉住的时间（2026-09-12 12:00）
AMOUNT = 10_000                      # 100.00 元


def account_of(path: Path) -> dict:
    """储蓄账户的 `(balance, available)` —— 扣款断言必须两者分开看。"""
    return raw(path, "SELECT balance, available FROM account WHERE id = ?", (ACCOUNT,))[0]


def schedules_of(path: Path) -> list[dict]:
    return raw(path, "SELECT * FROM scheduled_transfer ORDER BY created_at, id")


def book(payee: str = PAYEE, amount: int = AMOUNT, when: str = FUTURE) -> str:
    """预览 → 落预约，返回 `schedule_id`（断言失败直接报错，不让后续断言在脏状态上跑）。"""
    preview = transfer.preview_transfer(payee, amount, schedule=when)
    assert preview.ok, preview.message
    booked = scheduled.schedule_transfer(preview.data["preview_token"], OTP)
    assert booked.ok, booked.message
    return booked.data["schedule_id"]


# ---------------- ① 落预约**不动钱**（F16 验收第 2 条） ----------------

def test_scheduling_moves_no_money(seeded: Path, clock: object) -> None:
    before, before_txn = account_of(seeded), count(seeded, "txn")
    preview = transfer.preview_transfer(PAYEE, AMOUNT, schedule=FUTURE)
    assert preview.ok, preview.message
    booked = scheduled.schedule_transfer(preview.data["preview_token"], None)
    assert booked.ok, booked.message
    assert booked.data["execute_at"] == FUTURE
    rows = schedules_of(seeded)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["execute_at"] == FUTURE and rows[0]["amount"] == AMOUNT
    assert account_of(seeded) == before                    # ← 核心：balance 与 available 都没动
    assert count(seeded, "txn") == before_txn              # ← 也没有流水


# ---------------- ② 结算才扣款，且重复结算只扣一次 ----------------

def test_settlement_debits_exactly_once(seeded: Path, clock: object) -> None:
    before, before_txn = account_of(seeded), count(seeded, "txn")
    schedule_id = book()
    done = scheduled.execute_scheduled(schedule_id)
    assert done.ok, done.message
    after = account_of(seeded)
    assert after["balance"] == before["balance"] - AMOUNT
    assert count(seeded, "txn") == before_txn + 1
    assert schedules_of(seeded)[0]["status"] == "success"
    # 重复触发（惰性结算器每轮都会扫）→ **幂等**，不再扣
    again = scheduled.execute_scheduled(schedule_id)
    assert again.ok and account_of(seeded) == after
    assert count(seeded, "txn") == before_txn + 1


# ---------------- ③ 取消：不动钱、且拦住结算 ----------------

def test_cancel_moves_no_money_and_blocks_settlement(seeded: Path, clock: object) -> None:
    before = account_of(seeded)
    schedule_id = book()
    cancelled = scheduled.cancel_scheduled(schedule_id)
    assert cancelled.ok and cancelled.data["status"] == "cancelled"
    assert account_of(seeded) == before                    # 取消不动钱（本来也没扣过）
    # 取消后再结算 → 不扣款、状态仍是 cancelled
    after_cancel = scheduled.execute_scheduled(schedule_id)
    assert after_cancel.ok and after_cancel.facts["status"] == "cancelled"
    assert account_of(seeded) == before
    assert schedules_of(seeded)[0]["status"] == "cancelled"


def test_cancelling_a_settled_schedule_is_refused(seeded: Path, clock: object) -> None:
    """Q7：**不做「已扣款后退款」** —— 已 success 的不能取消。"""
    schedule_id = book()
    assert scheduled.execute_scheduled(schedule_id).ok
    refused = scheduled.cancel_scheduled(schedule_id)
    assert refused.ok is False and "success" in refused.message


# ---------------- ④ 同一凭证不重复落预约 ----------------

def test_same_token_books_only_one_schedule(seeded: Path, clock: object) -> None:
    preview = transfer.preview_transfer(PAYEE, AMOUNT, schedule=FUTURE)
    token = preview.data["preview_token"]
    first = scheduled.schedule_transfer(token, None)
    second = scheduled.schedule_transfer(token, None)
    assert first.ok and second.ok
    assert first.data["schedule_id"] == second.data["schedule_id"]   # 幂等：同一份
    assert len(schedules_of(seeded)) == 1


# ---------------- ⑤ 档位沿用预览结论：L2 要 OTP ----------------

def test_l2_schedule_requires_the_otp(seeded: Path, clock: object) -> None:
    before = account_of(seeded)
    preview = transfer.preview_transfer(PAYEE_NEW, AMOUNT, schedule=FUTURE)   # 非白名单 → L2
    assert preview.ok and preview.data["tier"] == "L2"
    wrong = scheduled.schedule_transfer(preview.data["preview_token"], "000000")
    assert wrong.ok is False and "验证码" in wrong.message
    assert schedules_of(seeded) == [] and account_of(seeded) == before       # 失败不落库、不动钱


# ---------------- ⑥ 预约时间必须晚于当前 ----------------

def test_past_schedule_time_is_rejected(seeded: Path, clock: object) -> None:
    past = transfer.preview_transfer(PAYEE, AMOUNT, schedule="2020-01-01T00:00")
    assert past.ok is False and "晚于当前时间" in past.message


def test_garbage_schedule_time_is_rejected(seeded: Path, clock: object) -> None:
    bad = transfer.preview_transfer(PAYEE, AMOUNT, schedule="明天上午")
    assert bad.ok is False and "预约时间" in bad.message


def test_split_with_is_still_not_implemented(seeded: Path, clock: object) -> None:
    """拆分转账仍属后续卡 —— 不能被 F16 的改动顺带放开。"""
    split = transfer.preview_transfer(PAYEE, AMOUNT, split_with=[PAYEE_NEW])
    assert split.ok is False and "拆分" in split.message


@pytest.mark.parametrize("status", scheduled.SCHEDULE_STATUSES)
def test_status_vocabulary_matches_the_ddl_comment(status: str) -> None:
    """状态取值域与 `data/schema.sql` 的注释一致（四个值，不多不少）。"""
    assert status in {"pending", "success", "failed", "cancelled"}
