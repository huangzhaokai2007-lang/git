"""缺槽续接（`agent/pending_slots.py`）的回归守卫。

用户实测（2026-10-27）：
```
我：我要送礼                          → 请补充：联系人、日期、预算、平台、收货地址、平台订单号。
我：我要在2027.10.10给妈妈送手表，价值1200 → 请补充：平台、收货地址、平台订单号。
我：京东，家，JD20261024001            → 「连续两次没能确认您的需求，我先转人工客服」❌
```
第三句是**补槽**，却被当成新意图 → 转人工。本文件钉住：按序补槽、同意图分类槽位优先、
换了事就放手、过期失效，以及**整段对话能出确认卡**。
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, pending_slots, session_memory

SESSION = "sess-pending"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    pending_slots.clear()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    pending_slots.clear()


def _fake(monkeypatch, intent: str, slots: dict) -> None:
    def chat_json(system, user, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_merge_fills_slots_in_order() -> None:
    item = {"intent": "gift_plan", "slots": {"contact": "妈妈"},
            "missing": ["platform", "address", "order_no"], "at": 0}
    assert pending_slots.merge(item, "京东，家，JD1") == {
        "contact": "妈妈", "platform": "京东", "address": "家", "order_no": "JD1"}


def test_merge_rejects_new_requests() -> None:
    item = {"intent": "gift_plan", "slots": {}, "missing": ["platform"], "at": 0}
    assert pending_slots.merge(item, "给王五转 100 元") is None      # 换事了
    assert pending_slots.merge(item, "取消这笔") is None


def test_merge_packs_extra_parts_into_last_slot() -> None:
    item = {"intent": "gift_plan", "slots": {}, "missing": ["platform", "address"], "at": 0}
    merged = pending_slots.merge(item, "京东 上海市某某路 1 号 2 楼")
    assert merged["platform"] == "京东"
    assert merged["address"] == "上海市某某路1号2楼"


def test_ttl_expires_the_pending(monkeypatch) -> None:
    clock = {"t": 0.0}
    monkeypatch.setattr(pending_slots.time, "monotonic", lambda: clock["t"])
    pending_slots.remember(SESSION, "gift_plan", {}, ["platform"])   # 记在 t=0
    assert pending_slots.peek(SESSION) is not None
    clock["t"] = pending_slots.TTL_SECONDS + 10                     # 时间跳过去
    assert pending_slots.peek(SESSION) is None


def test_same_intent_uses_classifier_slots(wired, monkeypatch) -> None:
    """用户把话说全了（同意图 + 有槽位）→ 分类器槽位优先，而不是按序硬填。"""
    _fake(monkeypatch, "gift_plan", {})                      # 先只有意图
    first = orchestrator.handle("我要送礼", session_id=SESSION)
    assert "请补充" in first.reply
    assert pending_slots.peek(SESSION) is not None

    _fake(monkeypatch, "gift_plan", {"contact": "妈妈", "date": "2027-10-10", "budget": "1200"})
    second = orchestrator.handle("我要在2027.10.10给妈妈送手表，价值1200", session_id=SESSION)
    assert "联系人" not in second.reply and "日期" not in second.reply
    assert "平台" in second.reply                           # 只剩平台/地址/单号


def test_short_answer_completes_the_card(wired, monkeypatch) -> None:
    """「京东，家，JD1」这样的短答要把最后几项补齐并**直接出确认卡**（不再转人工）。"""
    _fake(monkeypatch, "gift_plan", {})
    orchestrator.handle("我要送礼", session_id=SESSION)
    _fake(monkeypatch, "gift_plan", {"contact": "王五", "date": "2026-11-20", "budget": "300"})
    orchestrator.handle("给王五送个礼物，2026-11-20，预算 300", session_id=SESSION)

    _fake(monkeypatch, "smalltalk", {})                      # 短答不像请求 → 按序补槽
    done = orchestrator.handle("京东，家，JD20261024001", session_id=SESSION)
    assert "CONFIRM_CARD" in done.states and done.tier == "L2"
    assert "平台：京东" in done.reply and "JD20261024001" in done.reply
    assert pending_slots.peek(SESSION) is None               # 补齐后清掉
