"""收款人**解析卡**的待选状态（设计 §3 关卡 1：「命中多个 → 出解析卡」）。

同名多命中时，只有编排层知道「用户点名的那个词 + 本次意图 + 已填槽位」；界面要出卡让人挑，
就必须把这三样暂存下来。本模块只存**不带业务判断**的待选快照：

- `capture()`：SLOT_FILL 判出同名多命中时记下（`write_flow.start` 调）；
- `pending()`：界面取一次，据此渲染候选按钮；
- `pick()`：界面选了某一位 → 返回 `(intent, slots)`（槽位里补好 `payee_id`），
  由界面调 `write_flow.start` 重走 shape/风控 —— **本模块不调写流水线**（避免与
  `write_flow` 循环依赖），也不改任何数据。
"""

from __future__ import annotations

_PENDING: dict[str, dict] = {}


def capture(session_id: str, intent: str, slots: dict, candidates: list[dict]) -> None:
    """记下「等用户在解析卡里挑一位」的现场。"""
    _PENDING[session_id] = {"intent": intent, "slots": dict(slots),
                            "candidates": [dict(item) for item in candidates]}


def pending(session_id: str) -> dict | None:
    """取待选现场（无 → None）。界面每次渲染取一次，不消费。"""
    return _PENDING.get(session_id)


def clear(session_id: str) -> None:
    """清掉待选（新起一笔 / 已挑完时调）。"""
    _PENDING.pop(session_id, None)


def pick(session_id: str, payee_id: str) -> tuple[str, dict] | None:
    """用户在解析卡里选了某一位 → `(intent, 补齐 payee_id 的槽位)`，并清掉待选。"""
    state = _PENDING.get(session_id)
    if state is None:
        return None
    chosen = next((item for item in state["candidates"] if item.get("id") == payee_id), None)
    if chosen is None:
        return None
    slots = dict(state["slots"])
    slots["payee_id"] = chosen["id"]
    slots["masked_phone"] = chosen.get("phone") or ""
    _PENDING.pop(session_id, None)
    return state["intent"], slots
