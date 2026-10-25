"""Shortcuts start a fresh workspace without carrying old panels or write drafts."""
import pytest

from agent import classifier, confirm_card, llm, scene_context
from tests.conftest import raw
from tests.test_card_binding_ui import CARD, app, bind, click


def offline(monkeypatch):
    routes = {'我买了哪些理财': ('wealth_holdings', {}),
              '我有哪些订阅': ('subscription_list', {}),
              '看看还有哪些理财产品': ('wealth_all_products', {}),
              '我的白名单有哪些': ('payee_list', {}),
              '给王五转 100 元': ('transfer_single', {'payee': '王五', 'amount': '100'}),
              '出一份上个月的账单报告': ('bill_report', {'period': '2026-08', 'kind': 'monthly'})}
    def resolve(text, history=None):
        intent, slots = next((route for prompt, route in routes.items() if prompt in text), ('out_of_scope', {}))
        return classifier.IntentOut(intent=intent, confidence=1, slots=slots)
    monkeypatch.setattr(classifier, 'classify', resolve)
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))


@pytest.mark.parametrize(('label', 'intent'), [
    ('我的理财', 'wealth_holdings'), ('管订阅', 'subscription_list'),
    ('全部产品', 'wealth_all_products'), ('收款人', 'payee_list'),
    ('转账', 'transfer_single'), ('看账单', 'bill_report'),
    ('调整额度', 'card_limit_adjust'), ('挂失补卡', 'card_report_lost')])
def test_shortcut_closes_card_workspace_and_opens_selected_scene(monkeypatch, seeded, label, intent):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    messages = list(at.session_state['messages'])

    click(at, label)

    assert not at.session_state['bound-card-query']
    assert not any(button.key == f'detail-{CARD}' for button in at.button)
    assert not any(button.label in ('绑定银行卡', '关闭详情') for button in at.button)
    assert at.session_state['messages'][:len(messages)] == messages
    assert at.session_state['messages'][-1]['turn']['intent'] == intent
    if label == '全部产品':
        assert at.session_state['messages'][-1]['turn']['error_code'] == 'INVALID_STATE'
        assert '风险测评' in at.session_state['messages'][-1]['text']
    else:
        assert not at.session_state['messages'][-1]['turn']['error_code']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == 'normal'
    if label == '挂失补卡':
        assert any(button.key == f'lost-card-{CARD}' or button.label == '确认挂失并补卡' for button in at.button)


def test_bill_workspace_does_not_follow_another_shortcut(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click(at, '看账单')
    assert any(item.label == '📊 这份账单的图表' for item in at.expander)
    messages = list(at.session_state['messages'])
    click(at, '管卡')
    assert any('我的银行卡' in item.value for item in at.markdown)
    assert not any(item.label == '📊 这份账单的图表' for item in at.expander)
    click(at, '我的理财')
    assert not any(button.label == '绑定银行卡' for button in at.button)
    assert not any(item.label == '📊 这份账单的图表' for item in at.expander)
    assert at.session_state['messages'][:len(messages)] == messages


def test_session_reset_restores_a_fresh_chat_workspace(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    click(at, '我的理财')
    click(at, '重置会话')
    at.chat_input[0].set_value('出一份上个月的账单报告').run()
    assert not at.exception
    assert any(item.label == '📊 这份账单的图表' for item in at.expander)


def test_switching_from_transfer_to_holdings_cancels_only_unsubmitted_draft(monkeypatch, seeded):
    from tools import transfer

    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    session = at.session_state['session_id']
    before = raw(seeded, 'SELECT id,balance FROM account ORDER BY id')
    click(at, '转账')
    draft = confirm_card.current(session)
    assert draft and draft.intent == 'transfer_single', at.session_state['messages'][-1]

    click(at, '我的理财')

    assert confirm_card.current(session) is None
    assert at.session_state['messages'][-1]['turn']['intent'] == 'wealth_holdings'
    assert '需要再确认' not in at.session_state['messages'][-1]['text']
    transfer.set_session_id(session)
    assert not transfer.execute_transfer(draft.credential, '123456').ok
    assert raw(seeded, 'SELECT id,balance FROM account ORDER BY id') == before


def test_switching_away_cancels_lock_preview_and_clears_selected_card_context(monkeypatch, seeded):
    from agent import card_lock_flow

    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    next(button for button in at.button if button.key == f'lock-toggle-{CARD}').click().run()
    token = at.session_state['lock-view']['preview']['token']
    session = at.session_state['session_id']

    click(at, '我的理财')

    assert not any(item.key == f'lock-inline-otp-{CARD}' for item in at.text_input)
    assert not scene_context.snapshot(session).get('card_id')
    assert not card_lock_flow.submit(token, '123456', session)['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == 'normal'
