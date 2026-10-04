"""工具层共享薄封装与归属断言（任务卡 04b 拆分）。

铁律：
- 错误消息**一律不含数字**（回执的每个数字都必须能在 facts 里找到）。
- 金额转换 `_money` 只在这里做，保证口径一致。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from pydantic import ValidationError

from tools.schemas import ErrorCode, ToolResult

logger = logging.getLogger(__name__)

#: 当前用户 ID（线程/协程隔离）
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)

PCT_TOTAL = 100  # 百分比总和


class ToolError(Exception):
    """工具层内部错误（带错误码，供上层转 ToolResult）。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def set_current_user(user_id: str | None) -> None:
    _user_id.set(user_id)


def current_user_id() -> str:
    uid = _user_id.get()
    if uid is None:
        raise ToolError(ErrorCode.FORBIDDEN, "用户未登录")
    return uid


def current_session_id() -> str:
    return _session_id.get() or "unknown"


def _money(cents: int) -> str:
    """整数分 → 展示字符串（如 4663400 → "46,634.00"）。"""
    negative = cents < 0
    cents = abs(cents)
    yuan = cents // 100
    fen = cents % 100
    s = f"{yuan:,}.{fen:02d}"
    return f"-{s}" if negative else s


def _money_facts(cents: int, prefix: str) -> dict:
    """为金额生成 facts 键值对（如 prefix='balance' → balance=分, balance_yuan=展示）。"""
    return {prefix: cents, f"{prefix}_yuan": _money(cents)}


def _owned_account_ids() -> list[str]:
    """当前用户拥有的账户 ID 列表。"""
    from data import dao
    return dao.list_account_ids(current_user_id())


def require_owned(resource_type: str, owner_id: str, resource_id: str, *, tool: str) -> None:
    """归属断言：资源不属于当前用户 → FORBIDDEN。"""
    if owner_id != current_user_id():
        logger.warning(
            "%s: %s %s 属于用户 %s，当前用户 %s 无权访问",
            tool, resource_type, resource_id, owner_id, current_user_id()
        )
        raise ToolError(ErrorCode.FORBIDDEN, f"无权访问该{resource_type}")


def _invalid(model_cls, **kwargs) -> ToolResult | None:
    """Pydantic 校验入参；失败返回 INVALID_ARGUMENT（消息不含数字）。"""
    try:
        model_cls(**kwargs)
    except ValidationError as exc:
        return _fail(ErrorCode.INVALID_ARGUMENT, "参数格式不正确")
    return None


def _fail(code: ErrorCode, message: str) -> ToolResult:
    """构造失败结果（facts 为空，消息不含数字）。"""
    return ToolResult(ok=False, error_code=code.value, message=message, facts={})


def _ok(data: dict, facts: dict, message: str) -> ToolResult:
    """构造成功结果。"""
    return ToolResult(ok=True, data=data, facts=facts, message=message)


def _dao_reject(exc: ValueError) -> ToolResult:
    """DAO 层抛 ValueError → 转 INVALID_ARGUMENT。"""
    return _fail(ErrorCode.INVALID_ARGUMENT, str(exc))
