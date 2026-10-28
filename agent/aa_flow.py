"""群收款的编排层**只读**入口（界面用）：列进行中 / 全部 群收款请求。

界面（`interfaces/web/`）只能调 `agent/`，不能直连 `tools/`；本模块把 T42 包一层给卡面用。
**只读、无副作用**（不登记、不撤销、不改状态）。
"""

from __future__ import annotations


def requests(status: str | None = None) -> list[dict]:
    """群收款请求列表（`status=None` = 全部）。失败 → 空列表（卡面不画）。"""
    from tools import _aa_request                       # 延迟 import：避免顶层循环
    listed = _aa_request.list_aa_requests(status)
    if not listed.ok:
        return []
    return [dict(item) for item in (listed.facts.get("items") or [])]


def active() -> list[dict]:
    """进行中的群收款（卡面只对这几笔给操作按钮）。"""
    return requests("active")
