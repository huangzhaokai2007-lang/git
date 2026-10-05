"""理财工具（占位）。"""

from __future__ import annotations

from tools.schemas import ToolResult


def recommend_wealth(risk_level: str | None = None, horizon_days: int | None = None, amount: int | None = None) -> ToolResult:
    """推荐理财产品。"""
    from tools._query_common import _fail
    from tools.schemas import ErrorCode

    # 简化实现：未测评不能推荐
    if risk_level is None:
        return _fail(ErrorCode.INVALID_STATE, "请先完成风险测评")

    items = [
        {"id": "fund_001", "name": "稳健收益", "risk": "R2", "yield_7d": "2.5%"},
        {"id": "fund_002", "name": "平衡增长", "risk": "R3", "yield_7d": "3.8%"},
    ]
    return ToolResult(
        ok=True,
        data={"items": items, "total_count": len(items)},
        facts={"total_count": len(items)},
        message=f"为您推荐 {len(items)} 款理财产品。",
    )
