"""Keep newly merged subscription and wealth controls in the current workspace."""
from agent import card_binding_flow, orchestrator
from tests.test_card_binding_ui import app, click


def test_overdue_only_subscription_keeps_cancel_and_disappears_after_switch(monkeypatch, seeded):
    source = orchestrator.Turn(trace_id='overdue-only', intent='subscription_remind', reply='当前有逾期订阅',
        facts={'items': [], 'overdue_items': [{'subscription_id': 'sub_0001', 'merchant': '合成服务',
            'amount_yuan': '15.00', 'cycle': 'monthly', 'status': 'active',
            'next_charge_date': '2026-08-01', 'overdue_cn': '已过 4 天'}]})
    def respond(text, **kwargs):
        return (orchestrator.Turn(trace_id='cards-after-subs', intent='card_query', reply='我的银行卡')
                if text == '卡片查询' else source)
    monkeypatch.setattr(card_binding_flow, 'handle', respond)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('查看订阅提醒').run()
    assert not at.exception
    assert sum(button.label == '取消订阅' for button in at.button) == 1
    click(at, '管卡')
    assert not any(button.label == '取消订阅' for button in at.button)
    assert any(message.get('turn', {}).get('trace_id') == 'overdue-only'
               for message in at.session_state['messages'] if message.get('turn'))


def test_failed_wealth_recommendation_keeps_risk_assessment_entry(monkeypatch, seeded):
    source = orchestrator.Turn(trace_id='needs-assessment', intent='wealth_recommend',
                              reply='请先完成风险测评', error_code='INVALID_STATE')
    monkeypatch.setattr(card_binding_flow, 'handle', lambda *a, **kw: source)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('推荐理财').run()
    assert not at.exception
    assert any(button.label == '开始风险测评' for button in at.button)
    assert at.error
