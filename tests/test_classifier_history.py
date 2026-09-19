"""卡 16b 单测：分类器的「入参形状」与「出参归一化」（两条现场实测的坑）。

① **history 串味**：旧实现把 history 与当前话拼成一条 user 消息 → 模型把上一轮的意图算到当前话头上
   （实测 6 句常用话全部串味）。改成 role-separated（history 每轮一条独立消息）后 6/6 正确。
② **槽位取值**：分类器会填中文/变体（'储蓄卡' / '储蓄账户' / 'credit_card'），而工具层只认
   `savings|credit` → 用户看到 `INVALID_ARGUMENT: 参数不合法：account_type`。这里按代码归一化。
"""

from __future__ import annotations

import pytest

from agent import classifier, llm


def _patch_chat_json(monkeypatch: pytest.MonkeyPatch, outcome: object) -> list[tuple]:
    """把 `llm.chat_json` 换成固定出招的假函数，记录 `(system, user, schema)`。"""
    seen: list[tuple] = []

    def fake(system: str, user: object, schema: object) -> object:
        seen.append((system, user, schema))
        return outcome

    monkeypatch.setattr(llm, "chat_json", fake)
    return seen


# ---------------- ① role-separated ----------------

def test_messages_of_keeps_a_plain_string_as_one_user_message() -> None:
    """没有 history 时逐字保持旧行为（单条 user 消息），不做任何包装。"""
    assert llm.messages_of("S", "查余额") == [{"role": "system", "content": "S"},
                                              {"role": "user", "content": "查余额"}]


def test_messages_of_expands_a_turn_list() -> None:
    turns = [{"role": "user", "content": "第一句"}, {"role": "user", "content": "第二句"}]
    assert llm.messages_of("S", turns) == [{"role": "system", "content": "S"}, *turns]


def test_classify_passes_history_as_separate_user_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = classifier.IntentOut(intent="balance_query", confidence=0.9, slots={})
    seen = _patch_chat_json(monkeypatch, verdict)
    assert classifier.classify("查一下余额", history=["出一份上个月的账单报告"]) is verdict
    system, user, schema = seen[0]
    assert schema is classifier.IntentOut
    assert user == [{"role": "user", "content": "出一份上个月的账单报告"},
                    {"role": "assistant", "content": classifier.HISTORY_MARKER},
                    {"role": "user", "content": "查一下余额"}]
    assert "出一份上个月的账单报告" not in system


def test_history_marker_keeps_history_out_of_the_current_turn() -> None:
    """每条历史后面都补一条 assistant 边界，最后一条永远是**当前话**（多轮时同样成立）。"""
    _, turns = classifier.build_messages("当前", history=["第一轮", "第二轮"])
    assert [turn["role"] for turn in turns] == ["user", "assistant", "user", "assistant", "user"]
    assert turns[-1] == {"role": "user", "content": "当前"}
    assert all(turn["content"] == classifier.HISTORY_MARKER
               for turn in turns if turn["role"] == "assistant")


def test_classify_without_history_passes_the_bare_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_chat_json(monkeypatch, classifier.IntentOut(intent="smalltalk", confidence=0.9))
    classifier.classify("你好")
    assert seen[0][1] == "你好"


# ---------------- ② 槽位取值归一化 ----------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("储蓄卡", "savings"), ("储蓄账户", "savings"), ("储蓄帐户", "savings"), ("储蓄", "savings"),
    ("借记卡", "savings"), ("savings", "savings"), ("SAVINGS", "savings"), ("储蓄卡 ", "savings"),
    ("信用卡", "credit"), ("信用卡账户", "credit"), ("信用账户", "credit"), ("贷记卡", "credit"),
    ("credit_card", "credit"), ("credit card", "credit"), ("credit", "credit"),
])
def test_account_type_aliases_are_normalized(raw: str, expected: str) -> None:
    assert classifier.normalize_slot_values({"account_type": raw}) == {"account_type": expected}


def test_unknown_or_non_string_slot_values_are_left_untouched() -> None:
    """认不出的取值原样保留（工具层会明确拒，不猜不编）；非字符串槽位（如金额）一律不动。"""
    assert classifier.normalize_slot_values({"account_type": "活期存折"}) == {"account_type": "活期存折"}
    assert classifier.normalize_slot_values({"amount": 100, "period": "上个月"}) == {"amount": 100,
                                                                                    "period": "上个月"}


def test_classify_returns_tool_ready_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：LLM 填中文 → 交给编排层与工具层的已是枚举值（现场那条 INVALID_ARGUMENT 的回归）。"""
    verdict = classifier.IntentOut(intent="balance_query", confidence=0.95,
                                   slots={"account_type": "储蓄卡"})
    _patch_chat_json(monkeypatch, verdict)
    assert classifier.classify("查一下我的储蓄卡余额") is verdict      # 契约：返回同一份对象
    assert verdict.slots == {"account_type": "savings"}


def test_normalization_does_not_whitewash_unknown_slot_names() -> None:
    """归一化只改**取值**、不放松**结构**校验：表外槽位仍然判失败（也不该被洗成合法）。"""
    bogus = classifier.IntentOut(intent="balance_query", confidence=0.5, slots={"bogus": "储蓄卡"})
    with pytest.raises(classifier.ClassifierOutputError):
        classifier.check_structure(bogus)
