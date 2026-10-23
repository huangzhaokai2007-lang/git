"""收款人**解析卡**（设计 §3：「命中多个 → 出解析卡」）。

同名多命中时：编排层记下待选快照（意图 + 已填槽位 + 候选），界面据此出卡；
用户点一位 → 带着 `payee_id` 重走 write_flow.start（**不再二次解析**）。
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, payee_picker, session_memory, write_flow
from tests.conftest import raw
from tools import payee

SESSION = "sess-picker"
DUPLICATE = "李四"                     # 种子里有两个李四（139****1001 / 139****1002）


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    payee_picker.clear(SESSION)
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    payee_picker.clear(SESSION)


def _fake(monkeypatch, payee_name: str = DUPLICATE, amount: str = "100") -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                          "slots": {"payee": payee_name, "amount": amount},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_same_name_records_candidates(wired, monkeypatch) -> None:
    """「给李四转 100」→ 同名多命中 → 记下 2 个候选（界面出解析卡），并照旧给追问文案。"""
    _fake(monkeypatch)
    turn = orchestrator.handle(f"给{DUPLICATE}转 100 元", session_id=SESSION)
    assert turn.executed is False and confirm_card.current(SESSION) is None
    assert "请确认是哪一个" in turn.reply or "李四" in turn.reply
    pending = payee_picker.pending(SESSION)
    assert pending is not None and len(pending["candidates"]) == 2
    phones = sorted(item["phone"] for item in pending["candidates"])
    assert phones == ["139****1001", "139****1002"]


def test_pick_one_carries_id_and_skips_resolution(wired, monkeypatch) -> None:
    """点「选这位」→ 带 payee_id 重走 → 直接出确认卡（不再解析、不再追问）。"""
    _fake(monkeypatch)
    orchestrator.handle(f"给{DUPLICATE}转 100 元", session_id=SESSION)
    pending = payee_picker.pending(SESSION)
    chosen = next(item for item in pending["candidates"] if item["phone"] == "139****1002")
    intent, slots = payee_picker.pick(SESSION, chosen["id"])
    assert intent == "transfer_single" and slots["payee_id"] == chosen["id"]
    step = write_flow.start(intent, slots, SESSION)
    assert "CONFIRM_CARD" in step.states and "139****1002" in step.reply
    assert payee_picker.pending(SESSION) is None                       # 待选已消费


def test_picker_state_is_cleared_by_new_start(wired, monkeypatch) -> None:
    """新起一笔（非歧义）会把旧的待选清掉，不会残留一张过期的解析卡。"""
    _fake(monkeypatch)
    orchestrator.handle(f"给{DUPLICATE}转 100 元", session_id=SESSION)
    assert payee_picker.pending(SESSION) is not None
    _fake(monkeypatch, payee_name="房东", amount="50")
    orchestrator.handle("给房东转 50 元", session_id=SESSION)
    assert payee_picker.pending(SESSION) is None


def test_pick_unknown_candidate_returns_none(wired, monkeypatch) -> None:
    _fake(monkeypatch)
    orchestrator.handle(f"给{DUPLICATE}转 100 元", session_id=SESSION)
    assert payee_picker.pick(SESSION, "payee_ghost") is None
    assert payee_picker.pending(SESSION) is not None                   # 没选成功，待选还在
