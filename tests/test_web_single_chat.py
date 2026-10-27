"""单一聊天工作区：快捷入口切换面板，聊天追问保留原工作区。"""
from tests.test_card_binding_ui import app, click, fill


def test_single_chat_has_shortcuts_instead_of_page_navigation(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    assert not any(r.label == '页面' for r in at.radio)
    assert len(at.chat_input) == 1
    assert any(button.label == '管卡' for button in at.button)
    assert any(button.label == '看账单' for button in at.button)
    assert any(button.label == '操作记录' for button in at.button)


def test_session_reset_closes_all_workspace_panels(monkeypatch, seeded):
    from agent import card_binding_flow, orchestrator

    monkeypatch.setattr(card_binding_flow, 'handle', lambda *args, **kwargs:
                        orchestrator.Turn(trace_id='reset-panels', intent='payee_list', reply='收款人列表'))
    at = app(monkeypatch, seeded)
    click(at, '收款人')
    assert any(item.value == '##### 👥 收款人' for item in at.markdown)
    # Older conversations can have multiple panel flags set by chat follow-ups.
    domains = ('card', 'payee', 'schedule', 'aa', 'gift', 'lost')
    for domain in domains:
        at.session_state[f'bound-{domain}-query'] = True
    click(at, '重置会话')
    assert not any(at.session_state[f'bound-{domain}-query'] for domain in domains)
    assert not any(item.value == '##### 👥 收款人' for item in at.markdown)
    assert not any(item.label == '选择收款人' for item in at.radio)
    assert len(at.session_state['messages']) == 1
    assert len(at.chat_input) == 1
    click(at, '收款人')
    assert any(item.value == '##### 👥 收款人' for item in at.markdown)


def test_operation_history_keeps_chat_and_closes_binding_form(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    click(at, '绑定银行卡')
    fill(at, '模拟银行卡号', '6222000000000001')
    before = list(at.session_state['messages'])
    click(at, '操作记录')
    assert not at.exception
    assert len(at.chat_input) == 1
    assert at.session_state['messages'] == before
    assert not any(i.label == '模拟银行卡号' for i in at.text_input)
    assert not at.session_state['bound-card-query']
    assert any('操作记录' in item.value for item in at.markdown)
    assert any(item.label == '技术审计详情' for item in at.expander)
    click(at, '收起操作记录')
    assert not any(i.label == '银行卡密码' for i in at.text_input)


def test_chat_query_replaces_current_business_and_retains_history(monkeypatch, seeded):
    from agent import card_binding_flow, orchestrator
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    click(at, '绑定银行卡')
    fill(at, '模拟银行卡号', '6222000000000001')
    before = list(at.session_state['messages'])
    original = card_binding_flow.handle
    def reply(text, **kwargs):
        if text.startswith('出一份') and text.endswith('账单报告'):
            return orchestrator.Turn(trace_id='bill-test', intent='bill_report',
                                     reply='2026-08 账单：支出 9,152.00 元\n| 电商 | 4,370.00 | 48% |\n| 转账 | 3,000.00 | 32% |')
        return original(text, **kwargs)
    monkeypatch.setattr(card_binding_flow, 'handle', reply)
    at.chat_input[0].set_value('出一份上个月的账单报告').run()
    assert not any(i.label == '银行卡密码' for i in at.text_input)
    assert any('2026-08 账单' in item.value for item in at.markdown)
    assert not any(item.label == '📊 这份账单的图表' for item in at.expander)
    assert at.session_state['messages'][:len(before)] == before
    assert at.session_state['workspace-result']['scene'] == 'bills'
    assert len(at.chat_input) == 1
