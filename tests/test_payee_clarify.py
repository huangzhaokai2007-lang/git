"""F17 收款人解析单测：同名/零匹配时把**候选透出给用户**（`agent/payee_clarify.py` + `agent/write_flow.py`）。

背景：T6 `resolve_payee` 早就返回了候选（含脱敏手机号），但编排层只回一句"请补充收款人"，
候选被丢弃 —— 用户看不到是哪两个李四，也就无法选择。本文件的用例钉住修复后的行为。

沿用仓库做法：**真工具 + 假 LLM**（`monkeypatch` 掉 `llm.chat_json`），断言的是编排层真实回执。

种子事实（`data/seed.py`）：`payee_0001` 李四 139****1001 / `payee_0002` 李四 139****1002（同名 → 歧义）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import confirm_card, llm, orchestrator, payee_clarify, templates, write_flow
from tools import transfer

SESSION = "sess-f17"


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def intent(slots: dict, confidence: float = 0.95) -> dict:
    """一条转账意图（槽位名与 classifier 的 SLOT_SCHEMA 一致：payee = 名字/手机号、amount = 元）。"""
    return {"intent": "transfer_single", "confidence": confidence, "slots": slots, "missing_slots": []}


@pytest.fixture()
def wired(seeded: Path, monkeypatch: pytest.MonkeyPatch, clock):
    """把确认流程的时钟与会话态一起钉住（每用例从干净状态开始）。"""
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    yield seeded
    confirm_card.reset_state()


def turn_for(monkeypatch: pytest.MonkeyPatch, text: str, script: list[dict]):
    fake_llm(monkeypatch, script)
    return orchestrator.handle(text, session_id=SESSION)


def facts_of(count: int, *, first: int = 1001) -> dict:
    """伪造一份 `resolve_payee` 事实包（只用于文案单测，不碰数据库）。"""
    return {"candidate_count": count,
            "candidates": [{"id": f"payee_{first + i:04d}", "name": "李四",
                            "phone": f"139****{first + i}"} for i in range(count)]}


# ---------------- 核心：同名时候选必须透出给用户 ----------------

def test_ambiguous_payee_lists_every_masked_candidate(wired, monkeypatch) -> None:
    """同名两位 → 追问里必须列出**全部**候选的姓名与脱敏手机号（F17 验收第 1 条）。"""
    facts = transfer.resolve_payee("李四").facts
    turn = turn_for(monkeypatch, "给李四转 100 元", [intent({"payee": "李四", "amount": "100.00"})])
    assert turn.executed is False and turn.missing_slots == ["payee"]
    assert turn.reply.startswith(f"找到 {facts['candidate_count']} 个")        # 条数来自 facts
    for cand in facts["candidates"]:                                          # 逐个候选都在回执里
        assert cand["name"] in turn.reply and cand["phone"] in turn.reply
    assert transfer.resolve_payee("李四").data["ambiguous"] is True            # 确实歧义，不是唯一命中


def test_payee_clarify_numbers_all_come_from_facts(wired, monkeypatch) -> None:
    """铁律 2：追问文案里的每个数字都必须能在 `resolve_payee` 的事实包里找到。"""
    turn = turn_for(monkeypatch, "给李四转 100 元", [intent({"payee": "李四", "amount": "100.00"})])
    facts = transfer.resolve_payee("李四").facts
    assert templates.verify_numbers(turn.reply, facts) == set()
    assert payee_clarify.ask_for(facts, ["payee"]) == turn.reply               # 就是那一份文案


def test_payee_clarify_never_offers_index_selection(wired, monkeypatch) -> None:
    """防回归：`dao.find_payee` 是子串匹配，单个数字会同时命中多个手机号。

    所以追问**绝不能**引导"回复序号" —— 回 "1" 会再次歧义、变成无限追问。这里双向钉住。
    """
    turn = turn_for(monkeypatch, "给李四转 100 元", [intent({"payee": "李四", "amount": "100.00"})])
    assert "序号" not in turn.reply and "回复 1" not in turn.reply
    again = transfer.resolve_payee("1")                                        # 反证：数字选择器不可用
    assert again.data["ambiguous"] is True


def test_masked_phone_fragment_really_disambiguates(wired, monkeypatch) -> None:
    """追问引导"补充手机号其中几位"必须真的能解开歧义（否则追问就是死循环）。"""
    picked = transfer.resolve_payee("1001")
    assert picked.ok and picked.data["ambiguous"] is False
    assert picked.facts["candidates"][0]["id"] == "payee_0001"
    turn = turn_for(monkeypatch, "给李四转 100 元", [intent({"payee": "1001", "amount": "100.00"})])
    assert turn.missing_slots == [] and "CONFIRM_CARD" in turn.states          # 唯一命中 → 进确认卡
    assert turn.tier in ("L1", "L2") and turn.executed is False


# ---------------- 边界：零匹配 / 候选过多 / 收款人与金额同时缺 ----------------

def test_unknown_payee_gets_guidance_instead_of_generic_prompt(wired, monkeypatch) -> None:
    """零匹配 → 给引导语（而不是干巴巴的"请补充收款人"），且**不回显**用户原话。"""
    turn = turn_for(monkeypatch, "给查无此人转 100 元",
                    [intent({"payee": "查无此人", "amount": "100.00"})])
    assert turn.missing_slots == ["payee"] and turn.executed is False
    assert turn.reply == payee_clarify.NO_CANDIDATE
    assert "查无此人" not in turn.reply


def test_payee_clarify_caps_long_lists_without_inventing_a_remainder() -> None:
    """候选过多 → 只列前 N 条；截断提示只报**总数**（来自 facts），绝不报"还剩几个"。"""
    facts = facts_of(7)
    text = payee_clarify.render(facts)
    shown = payee_clarify.CANDIDATES_SHOWN
    assert len(text.splitlines()) == 1 + shown + 1 + 1                         # 头 + N 条 + 截断 + 尾
    assert f"139****{1000 + shown + 1}" not in text                            # 第 N+1 条起不列
    assert f"共 {facts['candidate_count']} 个匹配" in text
    assert "还剩" not in text and "剩余" not in text                            # 差值不在 facts 里
    assert templates.verify_numbers(text, facts) == set()                      # 截断后数字仍全部可溯


def test_payee_clarify_without_candidates_is_a_pure_guidance_sentence() -> None:
    assert payee_clarify.render({"candidate_count": 0, "candidates": [], "query": "查无此人"}) \
        == payee_clarify.NO_CANDIDATE
    assert payee_clarify.render({}) == payee_clarify.NO_CANDIDATE


def test_ambiguous_payee_with_missing_amount_asks_both(wired, monkeypatch) -> None:
    """收款人和金额同时缺 → 候选照列，金额也要问，两件事不互相盖掉。"""
    turn = turn_for(monkeypatch, "给李四转点钱", [intent({"payee": "李四"})])
    assert turn.missing_slots == ["payee", "amount"]
    assert "139****1001" in turn.reply and "139****1002" in turn.reply
    assert templates.SLOT_CN["amount"] in turn.reply


def test_candidate_text_with_stray_numbers_falls_back_to_generic_prompt(wired, monkeypatch) -> None:
    """铁律 2 的 fail-safe：候选文案出现 facts 之外的数字 → 退回通用追问，宁可不显示。"""
    monkeypatch.setattr(payee_clarify, "render", lambda facts: "找到 999999 个收款人")
    turn = turn_for(monkeypatch, "给李四转 100 元", [intent({"payee": "李四", "amount": "100.00"})])
    assert turn.reply == templates.clarify_missing(["payee"])                  # 通用兜底
    assert "999999" not in turn.reply


def test_write_path_without_payee_query_keeps_the_generic_prompt(wired, monkeypatch) -> None:
    """压根没提收款人（没跑 T6）→ 保持原有通用追问，不影响既有行为。"""
    turn = turn_for(monkeypatch, "转 100 元", [intent({"amount": "100.00"})])
    assert turn.missing_slots == ["payee"]
    assert turn.reply == templates.clarify_missing(["payee"])
