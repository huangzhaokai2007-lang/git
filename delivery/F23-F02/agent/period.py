"""时间解析模块（卡 17b 拆分）。

纯函数，无状态，无 LLM 参与。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from data.seed import AS_OF


def anchor_period() -> str:
    """锚点账期：当前日期所在月份（YYYY-MM）。"""
    return AS_OF.strftime("%Y-%m")


def resolve_period(text: str) -> str | None:
    """解析相对/绝对账期。"""
    text = text.strip().lower()
    if text in ("本月", "这个月", "当月", "current_month"):
        return anchor_period()
    if text in ("上个月", "上月", "last_month"):
        d = date.fromisoformat(anchor_period() + "-01")
        prev = d.replace(day=1) - timedelta(days=1)
        return prev.strftime("%Y-%m")
    # 尝试 YYYY-MM
    try:
        if len(text) == 7 and text[4] == "-":
            date.strptime(text, "%Y-%m")
            return text
    except ValueError:
        pass
    # 尝试 YYYY
    try:
        if len(text) == 4:
            date.strptime(text, "%Y")
            return text
    except ValueError:
        pass
    return None


def resolve_day(text: str | None, edge: Literal["start", "end"]) -> str | None:
    """解析日期（YYYY-MM-DD）。"""
    if text is None:
        return None
    text = text.strip()
    try:
        d = datetime.strptime(text, "%Y-%m-%d").date()
        return d.isoformat()
    except ValueError:
        pass
    # 尝试相对表达
    if text.lower() in ("今天", "today"):
        return AS_OF.isoformat()
    return None
