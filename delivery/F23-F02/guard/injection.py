"""注入检测（占位）。"""

from __future__ import annotations

from dataclasses import dataclass

UNSAFE_INTENT = "unsafe_request"


@dataclass
class ScreenResult:
    blocked: bool
    reason: str = ""


def detect(text: str) -> ScreenResult:
    """检测恶意输入。"""
    dangerous = ["绕过", "破解", "删除", "修改", "注入", "sql", "hack"]
    for kw in dangerous:
        if kw in text.lower():
            return ScreenResult(blocked=True, reason=f"检测到危险关键词: {kw}")
    return ScreenResult(blocked=False)
