"""Streamlit 真界面回归：绑定确认、隐藏余额和逐卡验证。"""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import raw

APP = Path(__file__).resolve().parents[1] / "interfaces/web/app.py"
NUMBER, PIN, CARD = "6222000000000001", "314159", "card_savings_0001"


def app(monkeypatch, seeded):
    monkeypatch.setenv("DB_PATH", str(seeded))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    return at


def click(at, label):
    next(button for button in at.button if button.label == label).click().run()
    assert not at.exception


def fill(at, label, value):
    next(element for element in at.text_input if element.label == label).set_value(value)


def bind(at):
    click(at, "绑定银行卡")
    fill(at, "模拟银行卡号", NUMBER)
    fill(at, "银行卡密码", PIN)
    click(at, "验证银行卡")
    click(at, "确认绑定")


def test_card_page_is_available(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    assert "💳 我的银行卡" in at.radio[0].options


def test_form_preview_confirmation_and_hidden_balance(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value("💳 我的银行卡").run()
    click(at, "绑定银行卡")
    fill(at, "模拟银行卡号", NUMBER)
    fill(at, "银行卡密码", PIN)
    click(at, "验证银行卡")
    assert raw(seeded, "SELECT * FROM agent_card_binding") == []
    assert not any(element.value == PIN for element in at.text_input)
    click(at, "确认绑定")
    assert len(raw(seeded, "SELECT * FROM agent_card_binding")) == 1
    texts = [element.value for element in at.markdown]
    assert "******" in texts and not any("46,634.00" in text for text in texts)
    next(button for button in at.button if button.key == f"balance-{CARD}").click().run()
    assert any("46,634.00" in element.value for element in at.markdown)
    next(button for button in at.button if button.key == f"balance-{CARD}").click().run()
    assert not any("46,634.00" in element.value for element in at.markdown)


def test_wrong_password_and_cancellation_never_bind(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value("💳 我的银行卡").run()
    click(at, "绑定银行卡")
    fill(at, "模拟银行卡号", NUMBER)
    fill(at, "银行卡密码", "999999")
    click(at, "验证银行卡")
    assert at.error
    assert raw(seeded, "SELECT * FROM agent_card_binding") == []
    click(at, "取消绑定")
    assert not any(element.label == "银行卡密码" for element in at.text_input)


def test_details_reveal_password_and_close(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value("💳 我的银行卡").run()
    bind(at)
    click(at, "6222 **** **** 0001")
    assert any("银行卡详情" in element.value for element in at.markdown)
    assert NUMBER not in str(at.session_state["messages"])
    click(at, "查看完整卡号")
    fill(at, "请输入此卡的银行卡密码", "999999")
    click(at, "验证并查看")
    assert not any(NUMBER in element.value for element in at.markdown)
    fill(at, "请输入此卡的银行卡密码", PIN)
    click(at, "验证并查看")
    assert any(NUMBER in element.value for element in at.markdown)
    assert NUMBER not in str(at.session_state["messages"])
    assert not any(element.value == PIN for element in at.text_input)
    click(at, "收起完整卡号")
    assert not any(NUMBER in element.value for element in at.markdown)


def test_reset_clears_sensitive_views_but_keeps_binding(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value("💳 我的银行卡").run()
    bind(at)
    click(at, "6222 **** **** 0001")
    click(at, "查看完整卡号")
    fill(at, "请输入此卡的银行卡密码", PIN)
    click(at, "验证并查看")
    click(at, "🧹 重置会话态")
    assert not any(NUMBER in element.value for element in at.markdown)
    assert len(raw(seeded, "SELECT * FROM agent_card_binding")) == 1


def test_chat_card_query_uses_bound_cards_and_keeps_details_open(monkeypatch, seeded):
    from agent import classifier, llm

    def classify(system, user, schema):
        if schema is classifier.IntentOut:
            return schema(intent="card_query", confidence=0.95, slots={})
        raise llm.LLMUnavailable("测试不润色")

    monkeypatch.setattr(llm, "chat_json", classify)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value("我有哪些卡").run()
    replies = str(at.session_state["messages"])
    assert "6222 **** **** 0001" not in replies
    assert "list_bound_cards" in replies
    bind(at)
    click(at, "6222 **** **** 0001")
    assert any(button.label == "查看完整卡号" for button in at.button)


@pytest.mark.parametrize("number", [NUMBER, "62 22000000000001", "6222  0000  0000  0001"])
def test_pasted_credentials_are_never_kept_or_sent_to_model(monkeypatch, seeded, number):
    from agent import llm

    seen = []

    def record(system, user, schema):
        seen.append(str(user))
        raise llm.LLMUnavailable("测试不调用模型")

    monkeypatch.setattr(llm, "chat_json", record)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value(f"绑定银行卡 {number}").run()
    text = str(at.session_state["messages"]) + str(at.session_state["turns"]) + str(seen)
    assert NUMBER not in text and number not in text and PIN not in text
    assert seen == []


def test_chat_password_is_hidden_even_without_a_card_number(monkeypatch, seeded):
    from agent import llm

    seen = []
    monkeypatch.setattr(llm, "chat_json", lambda *args: seen.append(args))
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value(f"银行卡密码 {PIN}").run()
    assert PIN not in str(at.session_state["messages"]) + str(at.session_state["turns"])
    assert seen == []


def test_switching_user_clears_card_chat_and_sensitive_ui(monkeypatch, foreign):
    from tools._query_common import set_current_user

    at = app(monkeypatch, foreign)
    at.radio[0].set_value("💳 我的银行卡").run()
    bind(at)
    click(at, "6222 **** **** 0001")
    at.session_state["messages"].append({"role": "assistant", "text": "旧查询 6222 **** **** 0001",
                                         "turn": {"intent": "card_query"}})
    at.session_state["turns"].append({"text": "旧查询", "turn": {"intent": "card_query"}})
    set_current_user("u_mallory_0002")
    at.run()
    assert "6222 **** **** 0001" not in str(at.session_state["messages"])
    assert not any(button.label == "查看完整卡号" for button in at.button)
    assert "旧查询" not in str(at.session_state["turns"])
