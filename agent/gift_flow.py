"""送礼订单的编排层**只读**入口（界面用）：列订单（可按状态筛）。

界面（`interfaces/web/`）只能调 `agent/`；本模块把 T37 包一层给卡面用。
**只读、无副作用**（不支付、不取消、不改状态）。
"""

from __future__ import annotations


def orders(status: str | None = None) -> list[dict]:
    """送礼订单列表（`status=None` = 全部）。失败 → 空列表（卡面不画）。"""
    from tools import _gift_order                       # 延迟 import：避免顶层循环
    listed = _gift_order.list_gift_orders(status)
    if not listed.ok:
        return []
    return [dict(item) for item in (listed.facts.get("items") or [])]


def cancellable() -> list[dict]:
    """可取消的订单（`created`）—— 卡面只对这几笔给「取消 / 支付」按钮。"""
    return orders("created")
