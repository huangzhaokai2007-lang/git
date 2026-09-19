"""任务卡 05 单测（拆分后）：T8 `execute_transfer` —— 幂等只扣一次款 / 事务原子性 / OTP / 越权。

共享脚手架在 conftest.py（卡 04b 拆分）。"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

from data import dao, db
from data.seed import SAVINGS_ID
from tools import query, transfer

from tests.conftest import (PAYEE, PAYEE_NEW, OTP, Clock, raw, write_sql, count, balance, assert_covered, preview_ok)


def test_execute_debits_once_and_writes_txn_plus_audit(seeded: Path) -> None:
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    before, start = (count(seeded, "txn"), count(seeded, "audit_log")), balance(seeded)
    result = transfer.execute_transfer(token)
    assert result.ok and result.data["balance_after"] == start - 10_000 == balance(seeded)
    assert result.data["amount"] == 10_000 and result.data["payee_name"] == "李四"
    row = raw(seeded, "SELECT * FROM txn WHERE id = ?", (result.data["txn_id"],))[0]
    assert (row["amount"], row["direction"], row["category"], row["counterparty"]) == \
        (-10_000, "out", "转账", "李四")
    assert (count(seeded, "txn"), count(seeded, "audit_log")) == (before[0] + 1, before[1] + 1)
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert (audit["tool"], audit["intent"], audit["result"], audit["permission_tier"]) == \
        ("execute_transfer", "transfer_single", "success", "L1")
    assert audit["session_id"] == "session-test" and audit["trace_id"].startswith("trace-")
    assert_covered(result.message, result.facts)


def test_execute_twice_with_the_same_token_debits_only_once(seeded: Path) -> None:
    """卡 05 点名用例：重复调用同一 token 只扣一次款。"""
    token = preview_ok(PAYEE, 20_000).data["preview_token"]
    start, txn_count = balance(seeded), count(seeded, "txn")
    first = transfer.execute_transfer(token)
    after_first = balance(seeded)
    second = transfer.execute_transfer(token)
    assert first.data == second.data and first.facts == second.facts
    assert balance(seeded) == after_first == start - 20_000
    assert count(seeded, "txn") == txn_count + 1
    assert len(raw(seeded, "SELECT id FROM txn WHERE id = ?", (first.data["txn_id"],))) == 1


def test_two_tokens_for_the_same_transfer_move_money_twice(seeded: Path) -> None:
    """幂等键是 token，不是"收款人+金额"：两次独立预览 = 两笔真实转账。"""
    first = transfer.execute_transfer(preview_ok(PAYEE, 5_000).data["preview_token"])
    second = transfer.execute_transfer(preview_ok(PAYEE, 5_000).data["preview_token"])
    assert first.data["txn_id"] != second.data["txn_id"]
    assert first.data["balance_after"] == second.data["balance_after"] + 5_000


def test_execute_requires_the_right_otp_for_l2(seeded: Path) -> None:
    token = preview_ok(PAYEE_NEW, 5_000).data["preview_token"]
    before = (count(seeded, "txn"), balance(seeded))
    missing = transfer.execute_transfer(token)
    wrong = transfer.execute_transfer(token, otp="000000")
    assert missing.error_code == wrong.error_code == "FORBIDDEN"
    assert (count(seeded, "txn"), balance(seeded)) == before
    ok = transfer.execute_transfer(token, otp=OTP)
    assert ok.ok and count(seeded, "txn") == before[0] + 1
    assert transfer.execute_transfer(token, otp=OTP).data == ok.data       # 通过后再重放仍幂等


def test_execute_rejects_expired_token(seeded: Path, clock: Clock) -> None:
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    clock.tick(seconds=transfer.PREVIEW_TTL_SECONDS + 1)
    before = (count(seeded, "txn"), balance(seeded))
    result = transfer.execute_transfer(token)
    assert result.error_code == "TOKEN_EXPIRED" and result.facts == {}
    assert (count(seeded, "txn"), balance(seeded)) == before
    assert transfer.execute_transfer(token).error_code == "TOKEN_EXPIRED"   # 过期即被摘除


def test_execute_unknown_token_is_token_expired(seeded: Path) -> None:
    result = transfer.execute_transfer("pt_does_not_exist")
    assert result.ok is False and result.error_code == "TOKEN_EXPIRED" and not re.search(r"\d", result.message)


def test_execute_refuses_a_token_of_another_user(seeded: Path) -> None:
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    query.set_current_user("u_someone_else")
    result = transfer.execute_transfer(token)
    assert result.error_code == "FORBIDDEN" and result.facts == {}
    assert transfer.preview_transfer(PAYEE, 1_000).error_code == "FORBIDDEN"      # 收款人也不归属


def test_execute_refuses_l3_instead_of_auto_executing(seeded: Path, clock: Clock) -> None:
    """L3 = 人工复核/延迟生效（规格 §4 PENDING_REVIEW）：本卡不自动执行，交编排层。"""
    clock.moment = datetime(2026, 9, 12, 2, 30, 0)
    for _ in range(2):
        transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"], otp=OTP)
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    assert transfer._TOKENS[token]["tier"] == "L3"               # 先确认这一版确实是 L3
    before = (count(seeded, "txn"), balance(seeded))
    result = transfer.execute_transfer(token, otp=OTP)
    assert result.error_code == "INVALID_STATE" and result.data is None
    assert (count(seeded, "txn"), balance(seeded)) == before


def test_execute_is_atomic_when_a_step_fails(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写审计时抛错 → 扣款与流水一并回滚；且不抛"嵌套 BEGIN"（台账 R1 的正面证据）。"""
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    before = (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded))
    original = dao.insert_audit

    def boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("审计写入失败")

    monkeypatch.setattr(dao, "insert_audit", boom)
    with pytest.raises(RuntimeError):
        transfer.execute_transfer(token)
    assert (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded)) == before
    monkeypatch.setattr(dao, "insert_audit", original)          # 只还原这一处，时钟补丁要留着
    assert transfer._TOKENS[token]["state"] == "preview"        # 未被消费 → 仍可重试
    assert transfer.execute_transfer(token).ok


def test_execute_rechecks_the_balance_after_preview(seeded: Path) -> None:
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    write_sql(seeded, [("UPDATE account SET balance = 1, available = 1 WHERE id = ?", (SAVINGS_ID,))])
    result = transfer.execute_transfer(token)
    assert result.error_code == "INSUFFICIENT_FUNDS" and balance(seeded) == 1


def test_execute_never_leaks_the_otp(seeded: Path) -> None:
    token = preview_ok(PAYEE_NEW, 1_000).data["preview_token"]
    result = transfer.execute_transfer(token, otp=OTP)
    blob = json.dumps({"data": result.data, "facts": result.facts, "message": result.message}, ensure_ascii=False)
    assert OTP not in blob and OTP not in json.dumps(transfer._TOKENS[token], default=str)


@pytest.mark.parametrize("token", ["", None, 123, []])
def test_execute_rejects_illegal_arguments(seeded: Path, token: object) -> None:
    result = transfer.execute_transfer(token)                   # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"


# ---------------- 卡 04b：真并发（线程级 TOCTOU 回归） ----------------


def _threaded_connect(path: object) -> sqlite3.Connection:
    """与 `data.db.connect` 同口径，但允许跨线程使用（仅测试；生产是单线程 demo）。

    为什么必须开：`data/db.py:50` 的 `sqlite3.connect(...)` 没传 `check_same_thread=False`，
    而 DAO 是**进程内单连接**。真并发用例里第二个线程一碰这个连接就会
    `ProgrammingError: SQLite objects created in a thread can only be used in that same thread` ——
    那样用例会红在 sqlite 线程亲和上，而不是我们要验的 TOCTOU 上（假红比没有用例更坏）。
    """
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_execute_is_idempotent_under_real_concurrency(seeded: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """同 token 两线程同时执行：只扣一次款、两侧拿到同一结果（卡 04b 修复的 TOCTOU 回归用例）。"""
    monkeypatch.setattr(db, "connect", _threaded_connect)
    dao.close()                                                 # 丢弃已有连接，让下一次连接走上面这个实现
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    before = balance(seeded)
    barrier = threading.Barrier(2)                              # 两线程同时冲线，制造确定性竞争
    results: list[transfer.ToolResult] = []

    def worker() -> None:
        barrier.wait()
        results.append(transfer.execute_transfer(token))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2 and all(result.ok for result in results), [r.message for r in results]
    assert results[0].data == results[1].data                   # 幂等：同一结果快照
    txn_id = results[0].data["txn_id"]
    assert balance(seeded) == before - 10_000                   # 只扣一次款
    assert raw(seeded, "SELECT COUNT(*) AS n FROM txn WHERE id = ?", (txn_id,))[0]["n"] == 1


# ---------------- 卡 14b-6：幂等落库端到端（重启后重放仍有效） ----------------


def test_execute_survives_a_restart_and_replays_identically(seeded: Path) -> None:
    """preview → execute → 重启(清内存 + 换新连接) → 重放同 token 逐字相同，且只扣一次款。

    重启的等价模拟：清空进程内 `_TOKENS` 内存缓存 + 丢弃 DAO 连接后重连同一库文件——
    内存归零、只剩 `idempotency` 落库快照，重放必须从这里取回同一份结果。
    """
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    start_balance, txn_before, audit_before = balance(seeded), count(seeded, "txn"), count(seeded, "audit_log")
    first = transfer.execute_transfer(token)
    assert first.ok and balance(seeded) == start_balance - 10_000
    transfer._TOKENS.clear()                                    # 内存快照清零（模拟进程重启）
    dao.close()
    dao.connect_db(seeded)                                      # 全新连接，只读库文件
    second = transfer.execute_transfer(token)
    assert second.ok
    assert second.data == first.data and second.facts == first.facts and second.message == first.message
    assert balance(seeded) == start_balance - 10_000            # 余额只扣一次
    assert count(seeded, "txn") == txn_before + 1               # 流水只 +1
    assert count(seeded, "audit_log") == audit_before + 1       # 审计只 +1
    row = raw(seeded, "SELECT result_json FROM idempotency WHERE token = ?", (token,))[0]
    assert json.loads(row["result_json"])["state"] == "executed"   # 落库快照已翻转到 executed


def test_execute_replay_does_not_count_toward_the_rate_limit(seeded: Path) -> None:
    """幂等重放不算一次写操作：只有真正的新执行才进 `rate_limit` 表（卡 14b-6 铁律）。"""
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    before = count(seeded, "rate_limit")
    transfer.execute_transfer(token)                            # 新执行 → 计 1 次
    transfer.execute_transfer(token)                            # 重放 → 不计数
    assert count(seeded, "rate_limit") == before + 1
