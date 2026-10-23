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


# ---------------- 2026-10-24 人类规格：候选排序 / 5 分钟过期 ----------------

def test_candidates_are_ordered_by_recency_then_whitelist(wired) -> None:
    """解析卡候选顺序：**最近转账优先 → 白名单优先**（只影响卡面，不改追问文案）。"""
    from agent import payee_picker as picker
    from agent.payee_resolve import order_candidates
    _fake_candidates = [
        {"id": "p_old", "name": "李四", "phone": "139****1001",
         "last_used_ts": "2026-01-01T09:00", "is_whitelist": True},
        {"id": "p_new", "name": "李四", "phone": "139****1002",
         "last_used_ts": "2026-09-01T09:00", "is_whitelist": False},
    ]
    ordered = order_candidates(_fake_candidates)
    assert [c["id"] for c in ordered] == ["p_new", "p_old"]        # 最近转账的排前面
    picker.clear(SESSION)
    picker.capture(SESSION, "transfer_single", {"payee": "李四"}, _fake_candidates)
    pending = picker.pending(SESSION)
    assert [c["id"] for c in pending["candidates"]] == ["p_new", "p_old"]
    picker.clear(SESSION)


def test_picker_expires_after_five_minutes(wired, monkeypatch) -> None:
    """解析卡 5 分钟未选择 → 自动作废（回到空闲态）。"""
    from agent import payee_picker as picker
    picker.clear(SESSION)
    now = 1_000.0
    monkeypatch.setattr(picker.time, "monotonic", lambda: now)      # 先钉住时钟，再捕获
    picker.capture(SESSION, "transfer_single", {"payee": "李四"},
                   [{"id": "p1", "name": "李四", "phone": "139****1001"}])
    assert picker.pending(SESSION) is not None                     # 刚捕获：还在
    monkeypatch.setattr(picker.time, "monotonic", lambda: now + 299)
    assert picker.pending(SESSION) is not None                     # 4 分 59 秒：还在
    monkeypatch.setattr(picker.time, "monotonic", lambda: now + 301)
    assert picker.pending(SESSION) is None                          # 超过 5 分钟：作废
    assert picker.pending(SESSION) is None                          # 且已被清掉


def test_aa_same_name_offers_the_picker(wired, monkeypatch) -> None:
    """AA 里出现同名（两个李四）→ 也要出**解析卡**，选中后替换名单里那一个人。"""
    from agent import confirm_card, llm, payee_picker as picker, session_memory, write_flow

    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["李四", "王五"], "amount": "500"},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    confirm_card.reset_state()
    session_memory.reset("aa-pick")
    picker.clear("aa-pick")
    orchestrator.handle("向李四和王五 AA 收款 500 元", session_id="aa-pick")
    pending = picker.pending("aa-pick")
    assert pending is not None and len(pending["candidates"]) == 2
    assert pending["slot"] == "payee_ids"
    chosen = pending["candidates"][0]
    intent, slots = picker.pick("aa-pick", chosen["id"])
    assert slots["payee_ids"][0] == chosen["phone"]          # 名单里那一个换成唯一手机号
    assert slots["payee_ids"][1] == "王五"                    # 另一个不受影响
    step = write_flow.start(intent, slots, "aa-pick")
    assert "CONFIRM_CARD" in step.states and "AA 收款确认卡" in step.reply


def test_monthly_on_a_specific_day_lands_on_that_day(wired) -> None:
    """「每月 15 日」→ 首期是 15 号，**不是月末**（实测 bug）。"""
    from agent._write_transfer import _first_occurrence
    when = _first_occurrence("monthly", "每月15日")
    assert when.endswith("T09:00") and when[8:10] == "15"
    assert _first_occurrence("monthly", "每个月月末").endswith("T09:00")
