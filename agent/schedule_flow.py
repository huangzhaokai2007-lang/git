"""预约的编排层**只读**入口（界面用）：列待执行的预约。

界面（`interfaces/web/`）只能调 `agent/`，不能直连 `tools/`；本模块把 T24 的待执行列表
包一层给卡面用。**只读、无副作用**（不结算、不落库）。
"""

from __future__ import annotations


def pending_schedules() -> list[dict]:
    """待执行的预约（按执行时间排序，工具层已排好）。失败 → 空列表（卡面不画）。"""
    from tools import _transfer_schedule                      # 延迟 import：避免顶层循环
    listed = _transfer_schedule.list_scheduled("pending")
    if not listed.ok:
        return []
    return [dict(item) for item in (listed.facts.get("items") or [])]
