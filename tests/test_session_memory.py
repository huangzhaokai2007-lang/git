"""追问续接的上下文**下沉到编排层**（`agent/session_memory.py`）的回归守卫。

**为什么必须有这条**：同一条「列出两个李四 → 用户回 1001」的断链，此前在
`interfaces/api`、`interfaces/im`、`app/cli` **三个入口都存在** —— 因为它们调
`orchestrator.handle(payload, session_id=…)` 时都不传 `history`（全项目只有网页端手工拼过）。

现在记忆挂在 `orchestrator.handle` 上，所以**调用方一个参数都不传**也必须能续接。
本用例因此**显式不传 `history`** —— 钉的正是「新入口忘了传」这一类断链不会再发生。
"""

from __future__ import annotations

import json

import pytest

from agent import classifier, confirm_card, llm, orchestrator, session_memory


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    """隔离：清会话记忆 + 在途确认；顺带把确认卡的时钟钉在白天（避开 night 因子）。"""
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(seen: list[str], first_slots: dict, second_slots: dict):
    """假分类器：第 1 轮给 first_slots、第 2 轮起给 second_slots；并记录它看到的 user 消息。"""
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            seen.append(str(user))
            slots = first_slots if len(seen) == 1 else second_slots
            return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        raise llm.LLMUnavailable("测试：只要分类")
    return chat_json


def test_second_turn_sees_the_first_reply_without_any_caller_plumbing(wired, monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(llm, "chat_json", _fake(seen, {"payee": "李四", "amount": "100.00"},
                                                {"payee": "李四（139****1001）", "amount": "100.00"}))

    first = orchestrator.handle("给李四转100元", session_id="s-followup")      # ← 不传 history
    assert "CLARIFY" in first.states and "139****1002" in first.reply

    second = orchestrator.handle("1001", session_id="s-followup")              # ← 也不传
    assert len(seen) == 2
    assert "139****1001" in seen[1], f"第 2 次分类没拿到第 1 轮的回执：{seen[1]!r}"
    assert "CONFIRM_CARD" in second.states
    assert second.executed is False                                            # 出卡而已，没扣款


def test_memory_is_scoped_per_session(wired, monkeypatch) -> None:
    """**会话隔离**：别的会话的上下文不能串进来（否则 A 用户的候选会污染 B 用户的追问）。"""
    seen: list[str] = []
    monkeypatch.setattr(llm, "chat_json", _fake(seen, {"payee": "李四", "amount": "100.00"},
                                                {"payee": "李四（139****1001）", "amount": "100.00"}))
    orchestrator.handle("给李四转100元", session_id="s-a")
    orchestrator.handle("1001", session_id="s-b")               # 换会话，没有上下文
    assert "139****1001" not in seen[1], "会话之间泄漏了上下文"


def test_memory_caps_how_much_it_keeps(wired) -> None:
    """上限 4 条：提示词不能随对话轮次无限增长。"""
    for i in range(10):
        session_memory.remember("s-cap", f"u{i}", f"r{i}")
    kept = session_memory.recent("s-cap")
    assert len(kept) == session_memory.CAP == 4
    assert kept[-2:] == ["u9", "r9"]
