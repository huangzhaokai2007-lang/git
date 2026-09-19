"""卡 14b-6 MUST_FIX 用例：`idempotency` 的 DB 守卫必须**真成立**（假守卫 = 资金链路可二次扣款）。

三条：
① stale 内存（本进程缓存停在 `pending`）+ 库已 `executed` → 第二次执行必须走幂等返回，**不得二次扣款**；
② CAS 的 expected 必须是**加载时捕获的旧 json**：拿一份与库不符的旧值去写 → 必须命中 0 行、返回 False（守卫非假）；
③ 正向对照：expected 与库一致时 → 翻转成功。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools import _transfer_token, transfer

from tests.conftest import OTP, PAYEE, balance, count


def _stored_state(path: Path, token: str) -> dict:
    """直接从库文件读（新连接，绕过内存缓存）：库里那份快照的真实状态。"""
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT result_json FROM idempotency WHERE token = ?", (token,)).fetchone()
    assert row is not None, "idempotency 里应当有这条 token"
    return json.loads(row[0])


def test_stale_memory_cannot_bypass_the_db_guard(seeded: Path) -> None:
    """① stale 内存 + 库已 executed → 幂等返回，钱只扣一次。"""
    preview = transfer.preview_transfer(PAYEE, 10_000)
    token = preview.data["preview_token"]
    before_balance, before_txn = balance(seeded), count(seeded, "txn")

    first = transfer.execute_transfer(token, OTP)
    assert first.ok and balance(seeded) == before_balance - 10_000

    stale = dict(_transfer_token._TOKENS[token])            # 把内存缓存改回 pending（模拟落后于库）
    stale["state"] = "pending"
    _transfer_token._TOKENS[token] = stale
    assert _stored_state(seeded, token)["state"] == "executed"

    again = transfer.execute_transfer(token, OTP)
    assert again.ok and again.data == first.data and again.facts == first.facts
    assert balance(seeded) == before_balance - 10_000        # 只扣一次
    assert count(seeded, "txn") == before_txn + 1            # 只写一笔流水


def test_cas_rejects_a_stale_expected_snapshot(seeded: Path) -> None:
    """② 拿与库不符的旧值做 CAS → 必须失败（这条钉死"假守卫"回归）。"""
    preview = transfer.preview_transfer(PAYEE, 10_000)
    token = preview.data["preview_token"]
    stale_expect = json.dumps({"state": "pending"}, ensure_ascii=False)
    fresh = _transfer_token._load_token_authoritative(token)
    assert _transfer_token._store_token(token, fresh, expect_json=stale_expect) is False
    assert _transfer_token._raw_snapshot(token) != stale_expect          # 库里没被旧值覆盖


def test_cas_succeeds_when_expected_matches_the_store(seeded: Path) -> None:
    """③ 正向对照：expected 与库一致 → 翻转成功（守卫不是"永远拒绝"）。"""
    preview = transfer.preview_transfer(PAYEE, 10_000)
    token = preview.data["preview_token"]
    expect = _transfer_token._raw_snapshot(token)
    fresh = dict(_transfer_token._load_token_authoritative(token))
    assert _transfer_token._store_token(token, fresh, expect_json=expect) is True
