"""演示预设的编排层入口（界面用）：只转调 `tools.demo_info.demo_presets`。

分层：`interfaces/` 只能调 `agent/`，所以这里做一层薄封装；不含任何业务判断。
"""

from __future__ import annotations


def presets() -> dict:
    """返回演示用的合成预设（卡号/密码/收款人/限额/OTP）；失败时返回 `{}`。"""
    from tools import demo_info

    result = demo_info.demo_presets()
    return dict(result.data or {}) if result.ok else {}
