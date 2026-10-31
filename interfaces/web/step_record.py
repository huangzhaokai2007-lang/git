"""按钮 / 解析卡路径的**回执记账**（与 `app._ask` 同口径）。

为什么单独一个模块：L3「待人工复核」卡（`app._render_pending`）依赖
`messages[-1].turn.pending_id`；以前按钮路径记的是 `{"turn": None}`，
于是 L3 明明发生了、界面却**什么都没显示**（用户实测：点第二个同名收款人"没反应"）。
"""

from __future__ import annotations

import time

import streamlit as st


def record_step(step, summary: str) -> None:
    """把一步 `write_flow.Step` 记进会话：回执文本 + turn 存根 + `pending_id` 同步。"""
    state = st.session_state
    turn = {
        "intent": getattr(step, "intent", None),
        "tier": getattr(step, "tier", None),
        "states": list(getattr(step, "states", []) or []),
        "pending_id": getattr(step, "pending_id", None),
        "executed": bool(getattr(step, "executed", False)),
        "error_code": getattr(step, "error_code", None),
    }
    text = getattr(step, "reply", "") or getattr(step, "ask", "") or ""
    state["messages"].append({"role": "assistant", "text": text, "turn": turn})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": turn})
    state["pending_id"] = turn["pending_id"] or state.get("pending_id")
