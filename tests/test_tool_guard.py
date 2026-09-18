"""卡 14a 单测：工具层统一入口（越权收口 + 参数边界）。

覆盖卡文点名的三类：越权访问他人账户/卡/持仓（+订阅）、非法金额（0/负/浮点/超限）、不存在的收款人。
判据取自 `guard/tool_guard.py`：它是**唯一实现**（`tools/_query_common.require_owned` 只是转发），
所以这里直接打统一入口 + 一条经真实工具的集成断言。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guard import tool_guard
from tools import _query_common, transfer
from tools.schemas import ErrorCode

from tests.conftest import FOREIGN_ACCOUNT, FOREIGN_USER, PAYEE, count, raw

OWNED_ACCOUNT = "acc_savings_0001"


def _codes() -> list[str]:
    return [member.name for member in ErrorCode]


def test_error_codes_we_use_are_declared() -> None:
    """用到的错误码必须在冻结枚举里（防止手写字符串漂移）。"""
    assert {"FORBIDDEN", "INVALID_ARGUMENT", "OVER_LIMIT", "NOT_FOUND"} <= set(_codes())


# ---------------- ① 越权：四类资源都 fail-closed ----------------

@pytest.mark.parametrize("resource", ["账户", "卡片", "持仓", "订阅"])
def test_foreign_resource_is_forbidden(seeded: Path, resource: str) -> None:
    with pytest.raises(_query_common.ToolError) as exc:
        tool_guard.require_owned(resource, FOREIGN_USER, FOREIGN_ACCOUNT)
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.message == f"{resource}不属于当前用户"          # 文案与卡 06/07 一致
    assert FOREIGN_USER not in exc.value.message                      # 不泄露对方 user id


def test_foreign_owner_none_is_also_forbidden(seeded: Path) -> None:
    """owner 为 None（查不到归属）也必须拒 —— fail-closed，不"查不到就放行"。"""
    with pytest.raises(_query_common.ToolError):
        tool_guard.require_owned("账户", None, OWNED_ACCOUNT)


def test_own_resource_passes(seeded: Path) -> None:
    tool_guard.require_owned("账户", _query_common.current_user_id(), OWNED_ACCOUNT)   # 不抛即通过


def test_rejection_writes_the_audit_trail(seeded: Path) -> None:
    """裁决 ②：越权必须写 audit_log.result='rejected'（tool=被调工具名、trace_id 可溯）。"""
    before_audit = count(seeded, "audit_log")
    with pytest.raises(_query_common.ToolError):
        tool_guard.require_owned("账户", FOREIGN_USER, FOREIGN_ACCOUNT, tool="get_balance",
                                 trace_id="trace-14a", intent="balance_query")
    assert count(seeded, "audit_log") == before_audit + 1
    audit = raw(seeded, "SELECT tool, result, error_code, trace_id FROM audit_log"
                        " ORDER BY rowid DESC LIMIT 1")[0]
    assert (audit["tool"], audit["result"], audit["error_code"]) == ("get_balance", "rejected", "FORBIDDEN")
    assert audit["trace_id"] == "trace-14a"
    assert tool_guard.UNAUTHORIZED_FACTOR == "unauthorized_resource"


def test_risk_event_factor_is_allowed_by_ddl(seeded: Path) -> None:
    """卡 14b：枚举已补齐，越权的安全留痕（risk_event.factor='unauthorized_resource'）现在写得进去。"""
    allowed = raw(seeded, "SELECT sql FROM sqlite_master WHERE name = 'risk_event'")[0]["sql"]
    assert "unauthorized_resource" in allowed


# ---------------- ④ 限流（滚动 60s、先计数再校验） ----------------

def test_sixth_write_in_window_is_rejected(seeded: Path) -> None:
    """写死字面量 5/6（**不复用 `RATE_MAX_WRITES`**）——否则阈值被放宽时这条用例也跟着放宽，就测不出东西。"""
    user = "u_rate_test"
    for index in range(5):                                                    # 字面量：窗口内允许 5 次
        assert tool_guard.check_write_rate(user, tool="transfer") == index + 1
    with pytest.raises(_query_common.ToolError) as exc:
        tool_guard.check_write_rate(user, tool="transfer")                     # 第 6 次
    assert exc.value.code is ErrorCode.OVER_LIMIT and "频繁" in exc.value.message
    assert count(seeded, "rate_limit") == 6                                    # 被拒的尝试也留痕


def test_window_slides_and_writes_are_allowed_again(seeded: Path) -> None:
    import datetime

    user = "u_rate_slide"
    base = datetime.datetime(2026, 9, 12, 12, 0, 0)
    for _ in range(tool_guard.RATE_MAX_WRITES):
        tool_guard.check_write_rate(user, tool="transfer", now=base)
    later = base + datetime.timedelta(seconds=tool_guard.RATE_WINDOW_SECONDS + 1)
    assert tool_guard.check_write_rate(user, tool="transfer", now=later) == 1     # 窗口滑走后重新放行


def test_reads_do_not_touch_the_limiter(seeded: Path) -> None:
    """只读不限流：不调 check_write_rate 就不该有任何计数。"""
    before = count(seeded, "rate_limit")
    tool_guard.require_amount_cents(10_000)
    tool_guard.require_owned("账户", _query_common.current_user_id(), OWNED_ACCOUNT)
    assert count(seeded, "rate_limit") == before


# ---------------- ⑤ 幂等落库 ----------------

def test_idempotent_replay_returns_same_result_and_runs_once(seeded: Path) -> None:
    calls: list[int] = []

    def producer(tag: str):
        return lambda: (calls.append(1), {"txn_id": tag})[1]

    first, replayed = tool_guard.idempotent_execute("tok-1", "transfer", "u_x", producer("t1"))
    second, replayed_again = tool_guard.idempotent_execute("tok-1", "transfer", "u_x", producer("t2"))
    assert first == second == {"txn_id": "t1"}                                   # 重放拿到既有结果
    assert (replayed, replayed_again) == (False, True) and len(calls) == 1        # 只执行一次


def test_idempotency_snapshot_survives_a_restart(seeded: Path) -> None:
    """落库而非内存：用**全新连接**（等价于进程重启）读回同一份结果快照。"""
    import json as _json
    import sqlite3

    tool_guard.idempotent_execute("tok-restart", "transfer", "u_x", lambda: {"txn_id": "txn-restart"})
    with sqlite3.connect(seeded) as fresh:
        row = fresh.execute("SELECT tool, user_id, result_json FROM idempotency WHERE token = ?",
                            ("tok-restart",)).fetchone()
    assert row[0] == "transfer" and row[1] == "u_x"
    assert _json.loads(row[2]) == {"txn_id": "txn-restart"}


def test_replay_does_not_count_toward_the_rate_limit(seeded: Path) -> None:
    """幂等重放不算一次写操作：只有真正的新执行才进限流表。"""
    user = "u_idem_rate"
    before = count(seeded, "rate_limit")
    tool_guard.idempotent_execute("tok-rate", "transfer", user, lambda: {"ok": True})
    tool_guard.idempotent_execute("tok-rate", "transfer", user, lambda: {"ok": False})    # 重放
    tool_guard.check_write_rate(user, tool="transfer")                            # 新写操作 → 计 1
    assert count(seeded, "rate_limit") == before + 1


# ---------------- ⑥ 越权双写（枚举补齐后 risk_event 也写得进） ----------------

def test_foreign_access_writes_audit_and_risk_event(seeded: Path) -> None:
    before_audit, before_risk = count(seeded, "audit_log"), count(seeded, "risk_event")
    with pytest.raises(_query_common.ToolError):
        tool_guard.require_owned("账户", FOREIGN_USER, FOREIGN_ACCOUNT, tool="get_balance",
                                 trace_id="trace-14b")
    assert count(seeded, "audit_log") == before_audit + 1
    assert count(seeded, "risk_event") == before_risk + 1
    risk = raw(seeded, "SELECT factor, trace_id FROM risk_event ORDER BY rowid DESC LIMIT 1")[0]
    assert risk["factor"] == "unauthorized_resource" and risk["trace_id"] == "trace-14b"


def test_query_common_require_owned_is_a_forwarder(seeded: Path) -> None:
    """收口后仍是同一个入口：老调用点（39 处）走的还是这份实现。"""
    before_audit = count(seeded, "audit_log")
    with pytest.raises(_query_common.ToolError):
        _query_common.require_owned("卡片", FOREIGN_USER, FOREIGN_ACCOUNT, tool="get_card")
    assert count(seeded, "audit_log") == before_audit + 1


# ---------------- ② 金额边界 ----------------

@pytest.mark.parametrize(("cents", "expected"), [
    (0, ErrorCode.INVALID_ARGUMENT),
    (-1, ErrorCode.INVALID_ARGUMENT),
    (10.5, ErrorCode.INVALID_ARGUMENT),
    (True, ErrorCode.INVALID_ARGUMENT),
    ("100", ErrorCode.INVALID_ARGUMENT),
    (None, ErrorCode.INVALID_ARGUMENT),
    (tool_guard.AMOUNT_HARD_MAX_CENTS + 1, ErrorCode.OVER_LIMIT),
])
def test_illegal_amounts_are_rejected(cents: object, expected: ErrorCode) -> None:
    with pytest.raises(_query_common.ToolError) as exc:
        tool_guard.require_amount_cents(cents, tool="preview_transfer")
    assert exc.value.code is expected


def test_legal_amount_is_returned() -> None:
    assert tool_guard.require_amount_cents(10_000) == 10_000
    assert tool_guard.require_amount_cents(tool_guard.AMOUNT_HARD_MAX_CENTS) == 5_000_000


def test_preview_transfer_uses_the_same_bound(seeded: Path) -> None:
    """集成：超硬上限的转账在工具入口就被挡下（不再各自重复校验）。"""
    huge = tool_guard.AMOUNT_HARD_MAX_CENTS + 1
    result = transfer.preview_transfer(PAYEE, huge)
    assert result.ok is False and result.data is None and result.facts == {}


# ---------------- ③ 收款人存在性 ----------------

def test_missing_payee_is_not_found(seeded: Path) -> None:
    with pytest.raises(_query_common.ToolError) as exc:
        tool_guard.require_payee_exists("查无此人-0000")
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_valid_payee_id_is_not_misjudged(seeded: Path) -> None:
    """reviewer 发现：模糊子串搜不到 id 列 → 有效 id（payee_0001）曾被误判 NOT_FOUND；现为按 id 精确查找。"""
    tool_guard.require_payee_exists(PAYEE)                      # payee_0001：有效 id → 放行
    with pytest.raises(_query_common.ToolError) as exc:
        tool_guard.require_payee_exists("payee_9999")           # 不存在的 id → NOT_FOUND
    assert exc.value.code is ErrorCode.NOT_FOUND
