"""单一聊天工作区：快捷入口不清空聊天与正在填写的表单。"""
from tests.test_card_binding_ui import app, click, fill


def test_single_chat_has_shortcuts_instead_of_page_navigation(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    assert not any(r.label == '页面' for r in at.radio)
    assert len(at.chat_input) == 1
    assert any(button.label == '💳 管卡' for button in at.button)
    assert any(button.label == '🧾 看账单' for button in at.button)
    assert any(button.label == '🧾 操作记录' for button in at.button)


def test_operation_history_keeps_chat_and_binding_form(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    click(at, '绑定银行卡')
    fill(at, '模拟银行卡号', '6222000000000001')
    before = list(at.session_state['messages'])
    click(at, '🧾 操作记录')
    assert not at.exception
    assert len(at.chat_input) == 1
    assert at.session_state['messages'] == before
    assert next(i for i in at.text_input if i.label == '模拟银行卡号').value == '6222000000000001'
    assert any('操作记录' in item.value for item in at.markdown)
    assert any(item.label == '技术审计详情' for item in at.expander)
    click(at, '收起操作记录')
    assert any(i.label == '银行卡密码' for i in at.text_input)
    assert next(i for i in at.text_input if i.label == '模拟银行卡号').value == '6222000000000001'


def test_bill_shortcut_keeps_unsubmitted_binding_form(monkeypatch, seeded):
    from agent import card_binding_flow, orchestrator
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    click(at, '绑定银行卡')
    fill(at, '模拟银行卡号', '6222000000000001')
    original = card_binding_flow.handle
    def reply(text, **kwargs):
        if text.startswith('出一份') and text.endswith('账单报告'):
            return orchestrator.Turn(trace_id='bill-test', intent='bill_report',
                                     reply='2026-08 账单：支出 9,152.00 元\n| 电商 | 4,370.00 | 48% |\n| 转账 | 3,000.00 | 32% |')
        return original(text, **kwargs)
    monkeypatch.setattr(card_binding_flow, 'handle', reply)
    click(at, '🧾 看账单')
    assert any(i.label == '银行卡密码' for i in at.text_input)
    assert any('2026-08 账单' in item.value for item in at.markdown)
    assert any(item.label == '📊 这份账单的图表' for item in at.expander)
    assert next(i for i in at.text_input if i.label == '模拟银行卡号').value == '6222000000000001'
    assert len(at.chat_input) == 1
    click(at, '⬇️ 拉取近 3 期账单（逐期走编排层）')
    assert next(i for i in at.text_input if i.label == '模拟银行卡号').value == '6222000000000001'
