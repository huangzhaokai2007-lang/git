"""审计回放（**T52 `replay_trace`**，L0 只读）—— 2026-10-24 追加。

人类需求（我在建议里提的第 1 条）：「给一个 trace_id，把这次请求的**完整链路**摊开」——
意图 → 槽位 → 解析命中 → 风控因子 → 档位 → 工具入参 → 结果。这是「数字不是模型编的」最硬的证据。

只读 `audit_log`（同一 trace_id 的所有行，按时间正序）+ 同 trace 的 `risk_event`。
**不写库、不改任何状态**；`params_json` 落库时已脱敏，这里原样返回。
"""

from __future__ import annotations

import json
import logging

from data import dao
from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _ok, current_user_id, require_owned,
)
from tools.schemas import ErrorCode, ToolResult

logger = logging.getLogger(__name__)

REPLAY_TOOL = "replay_trace"


def _loads(raw: object) -> dict:
    """`params_json` 安全解析（坏数据 → 空 dict，不抛）。"""
    try:
        value = json.loads(str(raw or "{}"))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def replay_trace(trace_id: str) -> ToolResult:
    """T52 回放一个 `trace_id` 的完整链路（L0 只读）。`data` = `{trace_id, steps, factors}`。"""
    key = str(trace_id or "").strip()
    if not key:
        return _fail(ErrorCode.INVALID_ARGUMENT, "请提供 trace_id")
    try:
        require_owned("审计记录", DEMO_USER_ID, current_user_id(),
                      tool=REPLAY_TOOL, intent="audit_replay")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    conn = dao.connection()
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM audit_log WHERE trace_id = ? ORDER BY ts, rowid", (key,))]
    if not rows:
        return _ok({"trace_id": key, "steps": [], "factors": []},
                   {"trace_id": key, "step_count": 0, "factors": []},
                   f"没有找到 trace_id={key} 的审计记录")
    steps = [{"ts": row["ts"], "actor": row["actor"], "intent": row["intent"] or "",
              "tool": row["tool"] or "", "tier": row["permission_tier"] or "",
              "risk_level": row["risk_level"] or "", "result": row["result"] or "",
              "error_code": row["error_code"] or "", "params": _loads(row["params_json"])}
             for row in rows]
    factors = [dict(row) for row in conn.execute(
        "SELECT factor, detail, action_taken FROM risk_event WHERE trace_id = ?", (key,))]
    return _ok({"trace_id": key, "steps": steps, "factors": factors},
               {"trace_id": key, "step_count": len(steps),
                "factors": [item.get("factor") for item in factors]},
               f"trace_id={key} 共 {len(steps)} 条审计记录")
