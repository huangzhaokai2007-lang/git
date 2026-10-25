"""标签（T47）+ 定时转账时间合法性（2026-10-24 人类规格）。

| 验收点 | 用例 |
|---|---|
| T47：多标签设置 / 清空 / 超限拒绝 | `test_set_tags_normalizes_and_limits` |
| 标签可用于**指代人员**（解析关卡走 tag 层） | `test_tag_resolves_the_person` |
| 改标签意图（L1）卡面与落库 | `test_tags_intent` |
| 定时时间：过去 / 5 分钟内 / 超 1 年 → 拒绝并提示 | `test_schedule_time_guards` |
| 定时时间：正常将来时间 → 出卡 | `test_schedule_ok` |
"""

from __future__ import annotations

import datetime
import json

import pytest

from agent import confirm_card, llm, orchestrator, payee_resolve, session_memory
from tests.conftest import raw

SESSION = "sess-tags"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    payee_resolve.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    payee_resolve.reset()


def _pid(wired, name: str) -> str:
    return raw(wired, "SELECT id FROM payee WHERE name = ?", (name,))[0]["id"]


def _fake(monkeypatch, intent: str, slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_set_tags_normalizes_and_limits(wired) -> None:
    """去空、去重、保序；清空；超 5 个 / 单标签超 8 字 → 拒绝。"""
    from tools import payee
    pid = _pid(wired, WANGWU)
    got = payee.set_payee_tags(pid, [" 房东 ", "房东", "家人"])
    assert got.ok is True and got.data["tags"] == ["房东", "家人"]
    assert raw(wired, "SELECT tags FROM payee WHERE id = ?", (pid,))[0]["tags"] == "房东,家人"
    assert payee.set_payee_tags(pid, []).ok is True
    assert raw(wired, "SELECT tags FROM payee WHERE id = ?", (pid,))[0]["tags"] is None
    assert payee.set_payee_tags(pid, ["a", "b", "c", "d", "e", "f"]).ok is False
    assert payee.set_payee_tags(pid, ["这个标签实在是太长了啊啊"]).ok is False


def test_tag_resolves_the_person(wired) -> None:
    """标签用于指代：「房东」→ 王五（解析关卡的 tag 层）。"""
    from tools import payee
    pid = _pid(wired, WANGWU)
    assert payee.set_payee_tags(pid, ["房东"]).ok is True
    result = payee_resolve.resolve("房东")
    assert result.kind == payee_resolve.KIND_UNIQUE
    assert result.payees[0]["name"] == WANGWU and result.how == payee_resolve.HOW_TAG


def test_tags_intent(wired, monkeypatch) -> None:
    """「给王五加上标签房东」→ L1 卡（当前标签→新标签）→ 确认即生效。"""
    pid = _pid(wired, WANGWU)
    _fake(monkeypatch, "payee_tags", {"payee": WANGWU, "tags": "房东、家人"})
    card = orchestrator.handle("给王五加上标签：房东、家人", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "当前标签" in card.reply and "新标签" in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True
    assert raw(wired, "SELECT tags FROM payee WHERE id = ?", (pid,))[0]["tags"] == "房东,家人"


def test_schedule_time_guards(wired, monkeypatch, clock) -> None:
    """过去 / 5 分钟内 / 超 1 年 → 拒绝并提示（人类规格）。"""
    now = clock.now() if hasattr(clock, "now") else clock()
    for bad in ("2020-01-01 09:00",
                (now + datetime.timedelta(minutes=2)).isoformat(timespec="minutes"),
                (now + datetime.timedelta(days=400)).isoformat(timespec="minutes")):
        _fake(monkeypatch, "transfer_scheduled",
              {"payee": WANGWU, "amount": "100", "schedule": bad})
        turn = orchestrator.handle(f"{bad} 给王五转 100 元", session_id=SESSION)
        assert turn.executed is False and "CONFIRM_CARD" not in turn.states
        assert ("过去" in turn.reply or "太近" in turn.reply or "1 年" in turn.reply)
        confirm_card.reset_state()


def test_schedule_ok(wired, monkeypatch, clock) -> None:
    """正常将来时间（> 5 分钟、< 1 年）→ 出卡。"""
    now = clock.now() if hasattr(clock, "now") else clock()
    good = (now + datetime.timedelta(days=3)).isoformat(timespec="minutes")
    _fake(monkeypatch, "transfer_scheduled",
          {"payee": WANGWU, "amount": "100", "schedule": good})
    card = orchestrator.handle(f"{good} 给王五转 100 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and "预约时间" in card.reply
