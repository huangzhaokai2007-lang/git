"""S5 可编辑卡片：卡内改值 → **重走** shape/风控 → 出新卡（不是直接改卡）。

验收点（设计 §4 + §5 的 S5）：
- 改金额后**重走风控**（额度内 L1 → 超额度 L2）；
- 改值只动被改字段，其它字段照旧；
- 换收款人 → 解析关卡重新跑、档位跟着新收款人走；
- OTP 阶段**不允许**改卡；
- 聊天里「金额改成 X」→ 同意图 → 合并旧卡字段出新卡；不同意图 → 维持旧行为（清卡追问）；
- `CardDraft` 数据契约：字段/值来自确认卡快照，无卡 → None。
"""

from __future__ import annotations

import json

import pytest

from agent import (
    card_draft,
    classifier,
    confirm_card,
    llm,
    orchestrator,
    session_memory,
    write_flow,
    pending_slots,
)
from data import payee_dao
from tests.conftest import raw
from tools import payee


def _is_pick(widget) -> bool:
    """选卡按钮：新版是「选择这张卡」，收款人卡列表里的是「银行名 + 6222 掩码」。"""
    label = str(getattr(widget, "label", ""))
    return "6222" in label or "选择这张卡" in label

UID = "u_zhangsan_0001"
SESSION = "sess-edit"
WANGWU, ZHANG = "王五", "张小美"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, payee_name: str = "房东", amount: str = "100") -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                          "slots": {"payee": payee_name, "amount": amount},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


#: 2026-10-31：转账现在**必须选收款卡** → 用例里统一补一张（房东=王五 pc_0003_a）
CARD_ID = "pc_0003_a"


def _start_transfer(wired, monkeypatch, amount: str = "100"):
    """出确认卡（含"必须先选收款卡"这一步）。"""
    _fake(monkeypatch, amount=amount)
    turn = orchestrator.handle(f"给房东转 {amount} 元", session_id=SESSION)
    #: 2026-10-31：网页里必须选卡是开关（agent 层默认关），这里做兼容 ——
    #: 万一以后打开开关也不会炸（pending 还在才提交）。
    if "收款卡" in (turn.reply or "") and pending_slots.peek(SESSION) is not None:
        turn = pending_slots.submit_form(SESSION, {"payee_card": CARD_ID})
    return turn


def test_edit_amount_rereuns_risk(wired, monkeypatch) -> None:
    """额度内 L1 → 改金额超额度 → **重走风控**变 L2。"""
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (WANGWU,))[0]["id"]
    payee_dao.update_payee(pid, UID, whitelist_quota=5_000)            # 王五额度 50 元
    card = _start_transfer(wired, monkeypatch, amount="20")
    assert card.tier == "L1"                                            # 20 元额度内免码
    edited = write_flow.edit(SESSION, {"amount": "100"})
    assert "CONFIRM_CARD" in edited.states and edited.tier == "L2"
    assert "100.00" in edited.reply and "超出白名单专属额度" in edited.reply


def test_edit_keeps_untouched_fields(wired, monkeypatch) -> None:
    """只改金额：收款人照旧（王五），金额换成新值。"""
    card = _start_transfer(wired, monkeypatch)
    edited = write_flow.edit(SESSION, {"amount": "88"})
    assert "88.00" in edited.reply and WANGWU in edited.reply


def test_edit_payee_switches_person_and_tier(wired, monkeypatch) -> None:
    """换收款人：解析关卡重跑，档位跟着新收款人走（白名单 → 普通）。"""
    card = _start_transfer(wired, monkeypatch)
    assert card.tier == "L1"
    edited = write_flow.edit(SESSION, {"payee": ZHANG})
    #: 2026-10-31：换了收款人 → 旧卡作废，必须重新选他的卡（这里选张小美的 pc_0004_a）
    if "收款卡" in (edited.reply or "") and pending_slots.peek(SESSION) is not None:
        edited = pending_slots.submit_form(SESSION, {"payee_card": "pc_0004_a"})
    assert ZHANG in edited.reply and edited.tier == "L2"                # 张小美非白名单


def test_edit_rejected_at_otp_stage(wired, monkeypatch) -> None:
    """验证码阶段不能改卡（先完成或撤销）。用张小美（L2 要码）走到 OTP 阶段。"""
    _fake(monkeypatch, payee_name=ZHANG)
    orchestrator.handle(f"给{ZHANG}转 100 元", session_id=SESSION)
    assert "CONFIRM_CARD" in orchestrator.handle("确认", session_id=SESSION).states
    assert confirm_card.current(SESSION).stage == "otp"
    blocked = write_flow.edit(SESSION, {"amount": "50"})
    assert blocked.result == "rejected" and "验证码阶段" in blocked.reply
    assert confirm_card.current(SESSION) is not None                    # 卡没被清


def test_resume_classify_edit_in_chat(wired, monkeypatch) -> None:
    """聊天口径：卡在途时说「金额改成 50」→ 分类同意图 → 合并旧字段出新卡。"""
    _fake(monkeypatch)
    first = orchestrator.handle("给房东转 100 元", session_id=SESSION)
    assert first.tier == "L1"
    resolver = lambda text, history: classifier.IntentOut(               # noqa: E731
        intent="transfer_single", confidence=0.95,
        slots={"amount": "50"}, missing_slots=[])
    edited = orchestrator.handle("金额改成 50 元", session_id=SESSION, intent_resolver=resolver)
    assert "50.00" in edited.reply and WANGWU in edited.reply           # 收款人从旧卡继承


def test_resume_different_intent_falls_back(wired, monkeypatch) -> None:
    """改口说别的意图 → 维持旧行为：清卡、回追问，不执行、不误改。"""
    _start_transfer(wired, monkeypatch)
    resolver = lambda text, history: classifier.IntentOut(               # noqa: E731
        intent="balance_query", confidence=0.95, slots={}, missing_slots=[])
    turn = orchestrator.handle("帮我查余额", session_id=SESSION, intent_resolver=resolver)
    assert confirm_card.current(SESSION) is None                         # 旧卡已清
    assert turn.executed is False


def test_draft_of_contract(wired, monkeypatch) -> None:
    """CardDraft：字段/值来自确认卡快照；无卡 → None；AA（无编辑字段）→ None。"""
    assert card_draft.draft_of(SESSION) is None
    _start_transfer(wired, monkeypatch)
    draft = card_draft.draft_of(SESSION)
    assert draft is not None and draft.kind == "transfer_single"
    #: 草稿值是**用户输入的原值**（"100"），卡面展示的 100.00 由事实包负责 —— 不混
    assert [(f.name, f.value) for f in draft.fields] == [("payee", "房东"), ("amount", "100")]
    confirm_card.clear(SESSION)
    assert card_draft.draft_of(SESSION) is None


# ---------------- 改卡句式的确定性解析（不赌 LLM 脾气） ----------------

def test_parse_edit_stereotyped_phrases() -> None:
    """刻板句式直接解析成槽位更新；自由文本 / 无编辑字段的意图 → None。"""
    assert card_draft.parse_edit("金额改成 50 元", "transfer_single") == {"amount": "50"}
    assert card_draft.parse_edit("收款人改为张小美", "transfer_single") == {"payee": "张小美"}
    assert card_draft.parse_edit("改成 80", "transfer_single") == {"amount": "80"}
    assert card_draft.parse_edit("预约时间改成 2026-10-30", "transfer_scheduled") == {"schedule": "2026-10-30"}
    assert card_draft.parse_edit("预算调整为 300 元", "gift_plan") == {"budget": "300"}
    assert card_draft.parse_edit("帮我查余额", "transfer_single") is None
    assert card_draft.parse_edit("金额改成 50", "aa_collect") is None    # AA 没有编辑字段
    assert card_draft.parse_edit("日期改成 2026-10-30", "transfer_single") is None  # 字段不属于该意图


def test_resume_edit_via_stereotype_without_llm(wired, monkeypatch) -> None:
    """「金额改成 50 元」走确定性规则：**不再分类**、收款人保留、金额换成新值。"""
    _fake(monkeypatch)
    calls: list[int] = []
    real = llm.chat_json

    def counting(system, user, schema):
        calls.append(1)
        return real(system, user, schema)

    monkeypatch.setattr(llm, "chat_json", counting)
    orchestrator.handle("给房东转 100 元", session_id=SESSION)
    before = len(calls)
    edited = orchestrator.handle("金额改成 50 元", session_id=SESSION)
    assert len(calls) == before                                          # 改卡零 LLM 调用
    assert "50.00" in edited.reply and WANGWU in edited.reply


# ---------------- AA 每人金额：表格化修改要**真的生效** ----------------

def _fake_aa(monkeypatch) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": [WANGWU, ZHANG],
                                                    "amount": "300"},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_edit_aa_amounts_recomputes_total(wired, monkeypatch) -> None:
    """AA 卡：把每人金额改成 王五 200 / 赵六 100 → 逐人明细更新、总额跟着变成 300（原 300 不变）。"""
    _fake_aa(monkeypatch)
    card = orchestrator.handle("向王五和张小美 AA 收款 300 元", session_id=SESSION)
    #: 2026-10-28 含我口径：卡面写「含我一份」（份数 = 人数 + 1）
    assert card.tier == "L1" and "含我一份" in card.reply
    edited = write_flow.edit(SESSION, {"amounts": "王五 200 元、张小美 100 元"})
    assert "200.00" in edited.reply and "100.00" in edited.reply
    assert "按人金额" in edited.reply


def test_edit_aa_amounts_changes_the_total(wired, monkeypatch) -> None:
    """改金额不改总额的话就只是装饰 —— 这里改总额 300 → 500，卡面总额必须跟着变。"""
    _fake_aa(monkeypatch)
    orchestrator.handle("向王五和张小美 AA 收款 300 元", session_id=SESSION)
    edited = write_flow.edit(SESSION, {"amounts": "王五 300 元、张小美 200 元"})
    assert "500.00" in edited.reply                                     # 总额 = 合计
    assert "300.00" in edited.reply


def test_new_transfer_discards_the_inflight_draft(wired, monkeypatch) -> None:
    _fake(monkeypatch, [(WANGWU, "100"), (ZHANG, "200")])
    orchestrator.handle(f"给{WANGWU}转 100 元", session_id=SESSION)
    first = write_flow.start("transfer_single", {"payee": WANGWU, "amount": "100",
                                                 "payee_card_id": CARD_ID}, SESSION)
    assert "CONFIRM_CARD" in first.states
    assert confirm_card.current(SESSION) is not None

    # 用户改说另一笔（新请求，不是改卡）
    second = orchestrator.handle(f"给{ZHANG}转 200 元", session_id=SESSION)
    assert "原草稿已失效" not in (second.reply or ""), second.reply
    done = write_flow.start("transfer_single", {"payee": ZHANG, "amount": "200",
                                                "payee_card_id": "pc_0004_a"}, SESSION)
    assert "CONFIRM_CARD" in done.states
    assert ZHANG in done.reply and "200.00" in done.reply
