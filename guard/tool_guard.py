"""护栏层：工具层统一入口校验（卡 14a）——越权统一收口 + 参数边界。

三件事，**各工具不再各做一份**（口径漂移就是漏洞）：

1. `require_owned`：资源归属。非当前用户 → `FORBIDDEN`（fail-closed，data/facts 一律为空，不泄露任何信息），
   并**同时**留两笔痕（语义不同，缺一不可）：
   - `audit_log`：`result='rejected'`、`tool=<被调工具名>`、`error_code='FORBIDDEN'`（业务流程视角：这次调用被拒了）；
   - `risk_event`：`factor='unauthorized_resource'`（安全视角：发生了越权尝试，用于检索与告警）。
2. `require_amount_cents`：金额必须是**正整数分**；非正整数（0/负/浮点/bool）→ `INVALID_ARGUMENT`；
   超过硬上限 `AMOUNT_HARD_MAX_CENTS`（5,000,000 分 = 50,000 元）→ `OVER_LIMIT`（沿用 `transfer.py` 的口径）。
3. `require_payee_exists`：收款人 id 必须存在（不存在 → `NOT_FOUND`）。
   ⚠ `resolve_payee` 的"空候选由编排层反问"口径**不受影响**（那是名字检索，不是 id 校验）。

分层：本模块属于 `guard/`，被 `tools/` 调用（卡 14a 明确要求）；对内引用 `tools._query_common` 的
`ToolError` / `current_user_id` 采用**函数内延迟导入**，避免 `tools → guard → tools` 的模块级循环。
"""

from __future__ import annotations

import logging
from typing import Any

from data import dao

logger = logging.getLogger(__name__)

#: 金额硬上限（整数分）：5,000,000 分 = 50,000 元。来源：卡 14a 第 2 条
AMOUNT_HARD_MAX_CENTS = 5_000_000

#: 越权尝试在 risk_event 里的固定 factor（单值，便于检索）。来源：卡 14a 口径裁决 ①
#: ⚠ 卡 14a 实测发现：`data/schema.sql` 对 `risk_event.factor` 有 CHECK 枚举，只允许
#: (night/geo/device/velocity/amount_jump/new_payee) —— 本值**超出该枚举**，写入会被 DDL 拒绝。
#: 由于 data 层不在卡 14a 范围，这里的写入目前会失败并被 `_risk_event` 记为告警；
#: 需要人类裁定（改 schema.sql 加枚举值 / 换一个已允许的取值 / 14b 一并做）。见交付说明待拍板。
UNAUTHORIZED_FACTOR = "unauthorized_resource"


def _common() -> Any:
    """延迟导入 tools 侧原语（避免模块级循环依赖）。"""
    from tools import _query_common

    return _query_common


def _tool_error(code: str, message: str) -> Exception:
    """构造 `tools._query_common.ToolError`（延迟导入）。"""
    common = _common()
    return common.ToolError(common.ErrorCode[code], message)


def _audit_rejected(tool: str | None, trace_id: str | None, intent: str | None,
                    detail: dict | None = None) -> None:
    """越权/伪造被拒 → `audit_log.result='rejected'`（业务流程视角）。写失败只告警，不吞掉拒绝。

    ⚠ 只在**调用方报出工具名**时写：39 个既有调用点（卡 06/07 起）走的是不带 `tool` 的转发，
    若给它们凭空多写一行审计就是行为变更（会打红既有基线用例）。把工具名一路传下来是
    卡 14a 的机械收尾（未完成，见交付说明），到那时再加这行断言。
    """
    if tool is None:
        return
    common = _common()
    try:
        dao.insert_audit(trace_id or "trace-unknown", common.current_session_id(), actor="agent",
                         intent=intent or "unknown", tool=tool or "unknown_tool",
                         params_json=detail or {}, risk_level="L2", permission_tier="L2",
                         result="rejected", error_code="FORBIDDEN")
    except Exception:                                          # noqa: BLE001 —— 审计失败不改变拒绝决定
        logger.exception("写审计失败（拒绝仍然生效）：tool=%s trace=%s", tool, trace_id)


def _risk_event(trace_id: str | None) -> None:
    """越权尝试 → `risk_event`（安全视角）。写失败只告警，不吞掉拒绝。"""
    common = _common()
    try:
        dao.insert_risk_event(common.current_user_id(), UNAUTHORIZED_FACTOR, trace_id=trace_id)
    except Exception:                                          # noqa: BLE001
        logger.exception("写 risk_event 失败（拒绝仍然生效）：trace=%s", trace_id)


def require_owned(resource: str, owner_id: str | None, resource_id: str, *,
                  tool: str | None = None, trace_id: str | None = None,
                  intent: str | None = None) -> None:
    """资源归属断言：不属于当前用户 → `FORBIDDEN`（并写 audit_log + risk_event）。fail-closed。"""
    common = _common()
    if owner_id != common.current_user_id():
        logger.warning("越权访问被拦：resource=%s id=%s owner=%s user=%s",
                       resource, resource_id, owner_id, common.current_user_id())
        _audit_rejected(tool, trace_id, intent,
                        {"resource": resource, "resource_id": resource_id})
        _risk_event(trace_id)
        raise _tool_error("FORBIDDEN", f"{resource}不属于当前用户")


def require_amount_cents(cents: object, *, tool: str | None = None, trace_id: str | None = None,
                         intent: str | None = None) -> int:
    """金额边界：必须正整数分，且不超过硬上限。返回合法金额（便于链式使用）。

    - 非整数 / bool / 0 / 负数 → `INVALID_ARGUMENT`
    - 超过 `AMOUNT_HARD_MAX_CENTS` → `OVER_LIMIT`（沿用 transfer.py 的口径）
    """
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        logger.warning("非法金额被拦：tool=%s amount=%r", tool, cents)
        raise _tool_error("INVALID_ARGUMENT", "金额必须是正整数分")
    if cents > AMOUNT_HARD_MAX_CENTS:
        logger.warning("金额超硬上限被拦：tool=%s amount=%s", tool, cents)
        raise _tool_error("OVER_LIMIT", "超过单笔金额硬上限")
    return cents


def require_payee_exists(payee_id: str, *, tool: str | None = None) -> None:
    """收款人 id 必须存在（不存在 → `NOT_FOUND`，不泄露是否存在他人同名收款人）。"""
    del tool
    if not dao.find_payee(payee_id):                            # 名字/手机号/id 任一可检索到即视为存在
        raise _tool_error("NOT_FOUND", "收款人不存在")
