"""Keep newly merged subscription and wealth controls in the current workspace."""
import pytest
from agent import card_binding_flow, orchestrator
from tests.test_card_binding_ui import app, click


@pytest.mark.parametrize('destination', ['管卡', '操作记录', '重置会话'])
def test_switching_away_from_missing_slots_closes_the_form(monkeypatch, seeded, destination):
    from agent import classifier, llm, pending_slots
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    monkeypatch.setattr(classifier, 'classify', lambda text, *a, **kw: classifier.IntentOut(
        intent='card_query' if '卡片查询' in text else 'transfer_scheduled', confidence=1, slots={}))
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('预约转账').run()
    assert any(button.label == '继续' for button in at.button)
    click(at, destination)
    assert not any(button.label == '继续' for button in at.button)
    assert pending_slots.peek(at.session_state['session_id']) is None


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


def test_risk_shortcut_keeps_assessment_entry_in_current_workspace(monkeypatch, seeded):
    source = orchestrator.Turn(trace_id='risk-not-assessed', intent='risk_assess',
        reply='您还没有完成风险测评。', facts={'assessed': False, 'query': True})
    monkeypatch.setattr(card_binding_flow, 'handle', lambda *a, **kw: source)
    at = app(monkeypatch, seeded)
    click(at, '风险程度')
    assert at.session_state['workspace-result']['scene'] == 'risk_assess'
    assert any(button.label == '开始风险测评' for button in at.button)
    assert not any(form.key == 'risk-form' for form in at.get('form'))
