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


def test_risk_event_factor_is_not_yet_allowed_by_ddl(seeded: Path) -> None:
    """已知缺口（提醒用例）：`risk_event.factor` 的 CHECK 枚举里还没有越权取值 → 这一笔留痕暂时写不进去。

    本用例在「DDL 加上该枚举值」后会自动变红，提醒把 `test_rejection_writes_the_audit_trail`
    补回 risk_event 断言（卡 14b 一并做）。
    """
    allowed = raw(seeded, "SELECT sql FROM sqlite_master WHERE name = 'risk_event'")[0]["sql"]
    assert tool_guard.UNAUTHORIZED_FACTOR not in allowed


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


def test_existing_payee_reference_passes(seeded: Path) -> None:
    tool_guard.require_payee_exists("王五")                     # 可被收款人检索命中的引用 → 放行
