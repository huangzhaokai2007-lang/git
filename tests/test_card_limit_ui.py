"""真实 Streamlit 表单与三状态记录验收。"""
from datetime import date, timedelta

import pytest

from tests.test_card_binding_ui import app, click, fill
from tests.conftest import raw


@pytest.mark.parametrize("page", ["💬 聊天", "📊 账单图表", "🧾 审计时间轴", "💳 我的银行卡"])
def test_sidebar_limit_shortcut_opens_inline_panel_from_every_page(monkeypatch, seeded, page):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value(page).run()
    click(at, "💹 调整额度")
    assert not at.exception
    assert at.radio[0].value == "💬 聊天"
    assert any(item.value == "### 额度调整申请" for item in at.main.markdown)
    assert not at.get("dialog")
    assert any(button.label == "固定授信额度" for button in at.button)
    assert any(button.label == "临时授信额度" for button in at.button)
    assert any(button.label == "单日交易限额" for button in at.button)
    assert not raw(seeded, "SELECT * FROM agent_card_limit_application")


def test_inline_limit_draft_allows_card_shortcut_and_page_navigation(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, "💹 调整额度")
    click(at, "固定授信额度")
    click(at, "💳 管卡")
    assert not at.exception and at.chat_input
    assert any("请选择已绑定银行卡" in item.value for item in at.main.markdown)
    at.radio[0].set_value("🧾 审计时间轴").run()
    at.radio[0].set_value("💬 聊天").run()
    assert not at.exception
    assert any("请选择已绑定银行卡" in item.value for item in at.main.markdown)
    assert not raw(seeded, "SELECT * FROM agent_card_limit_application")


def test_three_cards_bind_apply_and_records(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value("💳 我的银行卡").run()
    assert any(tab.label == "额度调整记录" for tab in at.tabs)
    for digit in ("1", "2", "3"):
        click(at, "绑定银行卡")
        fill(at, "模拟银行卡号", digit * 16)
        fill(at, "银行卡密码", digit * 6)
        click(at, "验证银行卡")
        click(at, "确认绑定")
    for digit in ("1", "2", "3"):
        click(at, "调整额度")
        click(at, "固定授信额度")
        click(at, f"选择 {digit * 4} **** **** {digit * 4}")
        fill(at, "申请调整后的额度（元）", "30000")
        click(at, "核对申请")
        assert len(raw(seeded, "SELECT * FROM agent_card_limit_application")) == int(digit) - 1
        fill(at, "模拟短信验证码", "123456")
        click(at, "确认提交申请")
    click(at, "查看申请记录")
    records = raw(seeded, "SELECT * FROM agent_card_limit_application")
    assert {row["status"] for row in records} == {"pending", "approved", "rejected"}
    assert len(at.dataframe[0].value) == 3
    assert set(at.dataframe[0].value["状态"]) == {"审核中", "已通过并生效", "未通过"}
    assert any(element.label == "申请日期范围" for element in at.date_input)
    future = date.fromisoformat(records[0]["created_at"][:10]) + timedelta(days=1)
    next(element for element in at.date_input if element.label == "申请日期范围").set_value((future, future)).run()
    assert not at.exception and len(at.dataframe[0].value) == 0
    click(at, "🧹 重置会话态")
    assert len(raw(seeded, "SELECT * FROM agent_card_limit_application")) == 3
    assert not at.exception


@pytest.mark.parametrize("topic", ["我想调整银行卡额度", "额度", "我的额度是多少", "银行卡每日限额", "固定额度", "临时额度", "能提额吗", "我想申请信用卡并提高额度"])
def test_chat_adjust_and_record_query_do_not_need_llm(monkeypatch, seeded, topic):
    from agent import llm

    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable("offline")))
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value(topic).run()
    assert not at.exception
    assert {"固定授信额度", "临时授信额度", "单日交易限额"} <= {button.label for button in at.button}
    assert not raw(seeded, "SELECT * FROM agent_card_limit_application")
    at.chat_input[0].set_value("查看额度调整记录").run()
    assert not at.exception
    assert any("暂无额度调整申请记录" in item.value for item in at.info)


def test_temporary_form_wrong_otp_and_cancel_preserve_card(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value("💳 我的银行卡").run()
    click(at, "绑定银行卡")
    fill(at, "模拟银行卡号", "2222222222222222")
    fill(at, "银行卡密码", "222222")
    click(at, "验证银行卡")
    click(at, "确认绑定")
    click(at, "调整额度")
    click(at, "临时授信额度")
    click(at, "选择 2222 **** **** 2222")
    assert any(element.label == "临时额度有效天数" for element in at.number_input)
    fill(at, "申请调整后的额度（元）", "30000")
    click(at, "核对申请")
    fill(at, "模拟短信验证码", "999999")
    click(at, "确认提交申请")
    assert at.error
    assert not raw(seeded, "SELECT * FROM agent_card_limit_application")
    click(at, "取消申请")
    assert not raw(seeded, "SELECT * FROM agent_card_limit_application")
    assert raw(seeded, "SELECT credit_limit FROM card WHERE id='card_limit_approved'")[0]["credit_limit"] == 2000000
