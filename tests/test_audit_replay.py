"""审计回放（T52 / `agent.audit_flow` / `interfaces/web/audit_ui`）。

| 验收点 | 用例 |
|---|---|
| 一个 trace 能回放出多步（分类→预览→执行） | `test_replay_shows_the_chain` |
| 不存在的 trace_id → 明确的空结果（不报错） | `test_unknown_trace_returns_empty` |
| 空入参 → INVALID_ARGUMENT | `test_empty_trace_is_rejected` |
| 链路行人类可读（带工具名/档位/结果） | `test_replay_lines_are_readable` |
"""

from __future__ import annotations

import json

import pytest

from agent import audit_flow, confirm_card, llm, orchestrator, session_memory
from tools import audit

SESSION = "sess-audit"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, intent: str = "transfer_single", slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {"payee": "王五", "amount": "100"},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_replay_shows_the_chain(wired, monkeypatch) -> None:
    """一次真实转账（确认 + 执行）能回放出多步，且带上工具名。"""
    _fake(monkeypatch)
    card = orchestrator.handle("给王五转 100 元", session_id=SESSION)
    done = orchestrator.handle("确认", session_id=SESSION)
    trace_id = done.trace_id or card.trace_id
    result = audit.replay_trace(trace_id)
    assert result.ok is True and len(result.data["steps"]) >= 2
    tools = [step["tool"] for step in result.data["steps"]]
    assert "preview_transfer" in tools and "execute_transfer" in tools


def test_unknown_trace_returns_empty(wired) -> None:
    result = audit.replay_trace("trace-does-not-exist")
    assert result.ok is True and result.data["steps"] == []
    assert "没有找到" in result.message


def test_empty_trace_is_rejected(wired) -> None:
    assert audit.replay_trace("").ok is False


def test_replay_lines_are_readable(wired, monkeypatch) -> None:
    _fake(monkeypatch)
    orchestrator.handle("给王五转 100 元", session_id=SESSION)
    done = orchestrator.handle("确认", session_id=SESSION)
    lines, factors, data = audit_flow.replay(done.trace_id)
    assert lines and "意图 transfer_single" in lines[0]
    assert "档位" in lines[0] and data["trace_id"] == done.trace_id
