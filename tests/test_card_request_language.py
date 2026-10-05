"""Natural card requests remain usable offline without swallowing unrelated actions."""
import pytest

from tests.conftest import raw

LANGUAGE_CASES = [
    ("帮我查一下我的卡", "query"), ("帮我看看我的银行卡", "query"),
    ("麻烦你列出我已绑定的银行卡，谢谢", "query"), ("我绑定了哪些卡", "query"),
    ("我名下有几张银行卡？", "query"), ("查询一下我绑定的银行卡", "query"),
    ("你好，请帮我看看我有哪些卡", "query"), ("显示我的卡片列表", "query"),
    ("  帮我 看看 我的 银行卡 ！", "query"), ("我的银行卡在哪里", "query"),
    ("添加一张银行卡", "bind"), ("帮我绑定一下这张卡", "bind"),
    ("我有张银行卡想绑定", "bind"), ("我需要绑定银行卡", "bind"),
    ("请帮我关联一张银行卡", "bind"), ("能不能帮我绑卡", "bind"),
    ("我想绑一张储蓄卡", "bind"), ("新增银行卡", "bind"),
    ("怎么绑定银行卡？", "bind"), ("添加一张新的银行卡", "bind"),
    ("我要办信用卡", "apply"), ("信用卡怎么申请", "apply"),
    ("如何申请一张信用卡？", "apply"), ("我想办理一张信用卡", "apply"),
    ("麻烦帮我申请信用卡，谢谢！", "apply"),
    ("能帮我看看我的卡吗？", "query"), ("查查我的银行卡", "query"),
    ("我已经绑定了哪些银行卡", "query"), ("帮我绑卡吧", "bind"),
    ("我想把这张银行卡绑定一下", "bind"), ("我有张卡，想绑定", "bind"),
]


@pytest.mark.parametrize("text,kind", LANGUAGE_CASES)
def test_natural_card_requests_work_offline_without_writes(seeded, monkeypatch, text, kind):
    from agent import card_binding_flow as flow, llm

    def offline(*args, **kwargs):
        raise llm.LLMUnavailable("offline")

    monkeypatch.setattr(llm, "chat_json", offline)
    turn = flow.handle(text, session_id="language-test")
    assert turn.intent == "card_query" and turn.tool_calls == ["list_bound_cards"]
    assert not turn.executed
    assert (turn.ask == flow.BINDING_PROMPT) == (kind != "query")
    if kind == "apply":
        assert "不支持新信用卡申请或审批" in turn.reply
    assert raw(seeded, "SELECT * FROM agent_card_binding") == []
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE trace_id=?", (turn.trace_id,))) == 1


@pytest.mark.parametrize("text", [
    "不要绑定银行卡", "我不想申请信用卡", "取消绑定银行卡", "解绑我的银行卡",
    "帮我挂失银行卡", "冻结我的卡", "查询银行卡后转账", "绑定银行卡并转账",
    "我想申请信用卡并提高额度", "先绑卡再还款", "请帮我支付", "我有张银行卡丢了",
])
def test_other_actions_and_negations_keep_the_original_classifier(seeded, monkeypatch, text):
    from agent import card_binding_flow as flow, classifier

    calls = []

    def classify(request, history):
        calls.append(request)
        return classifier.IntentOut(intent="out_of_scope", confidence=0)

    monkeypatch.setattr(classifier, "classify", classify)
    turn = flow.handle(text, session_id="language-negative")
    assert calls and turn.intent == "out_of_scope" and turn.tool_calls == []
