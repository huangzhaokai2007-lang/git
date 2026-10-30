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

import time

#: 解析卡有效期（人类规格：「解析卡片 5 分钟未选择自动过期，回到空闲态」）
TTL_SECONDS = 300

_PENDING: dict[str, dict] = {}


def capture(session_id: str, intent: str, slots: dict, candidates: list[dict],
            *, slot: str = "payee") -> None:
    """记下「等用户在解析卡里挑一位」的现场（带时间戳，超过 5 分钟自动作废）。

    候选在这里按人类规格排序（**最近转账 → 白名单 → 姓名**）—— 只影响**卡面顺序**，
    不改追问文案（那一份由 `payee_clarify` 按工具事实包原序给出）。
    """
    from agent.payee_resolve import order_candidates      # 延迟 import：避免顶层相互引用
    ordered = order_candidates([dict(item) for item in candidates])
    _PENDING[session_id] = {"intent": intent, "slots": dict(slots), "slot": slot,
                            "candidates": ordered, "at": time.monotonic()}


def pending(session_id: str) -> dict | None:
    """取待选现场（无 / 已超 5 分钟 → None，并顺手清掉）。界面每次渲染取一次，不消费。"""
    state = _PENDING.get(session_id)
    if state is None:
        return None
    if time.monotonic() - float(state.get("at") or 0) > TTL_SECONDS:
        _PENDING.pop(session_id, None)                 # 过期即作废：回到空闲态
        return None
    return state


def clear(session_id: str) -> None:
    """清掉待选（新起一笔 / 已挑完时调）。"""
    _PENDING.pop(session_id, None)


def clear_all() -> None:
    """清掉**所有会话**的待选（用例隔离用；进程级全局状态不该跨用例泄漏）。"""
    _PENDING.clear()


def pick(session_id: str, payee_id: str) -> tuple[str, dict] | None:
    """用户在解析卡里选了某一位 → `(intent, 补齐 payee_id 的槽位)`，并清掉待选。"""
    state = _PENDING.get(session_id)
    if state is None:
        return None
    chosen = next((item for item in state["candidates"] if item.get("id") == payee_id), None)
    if chosen is None:
        return None
    slots = dict(state["slots"])
    slot = str(state.get("slot") or "payee")
    if slot == "payee_ids":                            # AA：把名单里第一个同名项换成唯一手机号
        name = str(chosen.get("name") or "")
        phone = str(chosen.get("phone") or "")
        items = [str(x) for x in (slots.get("payee_ids") or [])]
        for index, item in enumerate(items):
            if item == name and phone:
                items[index] = phone                   # 脱敏手机号在解析关卡里唯一可辨
                break
        slots["payee_ids"] = items
    else:
        slots["payee_id"] = chosen["id"]
        slots["masked_phone"] = chosen.get("phone") or ""
    _PENDING.pop(session_id, None)
    return state["intent"], slots
