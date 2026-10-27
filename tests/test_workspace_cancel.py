"""Cancelling confirmation restores the source page, including trusted scope."""
import pytest
from agent import classifier, confirm_card, llm, scene_context
from tests.test_card_binding_ui import app, click
from tests.conftest import raw


@pytest.mark.parametrize('otp', [False, True])
@pytest.mark.parametrize('shortcut', [False, True])
def test_cancel_restores_bill_in_confirm_and_otp(monkeypatch, seeded, otp, shortcut):
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    monkeypatch.setattr(classifier, 'classify', lambda *a, **kw: classifier.IntentOut(
        intent='transfer_single', confidence=1, slots={'payee': '王五', 'amount': '100'}))
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('查八月账单').run()
    original = at.session_state['workspace-result']['turn']['trace_id']
    balances = raw(seeded, 'SELECT id,balance FROM account ORDER BY id')
    if shortcut:
        click(at, '转账')
    else:
        at.chat_input[0].set_value('给王五转100元').run()
    session = at.session_state['session_id']
    assert confirm_card.current(session)
    if otp:
        confirm_card.to_otp_stage(session)
        at.run()
    cancel = next(b for b in at.button if b.key == ('otp-cancel' if otp else 'confirm-cancel'))
    cancel.click().run()
    assert not at.exception
    assert confirm_card.current(session) is None
    assert at.session_state['workspace-result']['turn']['trace_id'] == original
    assert scene_context.snapshot(session)['period'] == '2026-08'
    assert scene_context.snapshot(session)['intent'] == 'bill_report'
    assert any(b.label == '查看流水' for b in at.button)
    assert raw(seeded, 'SELECT id,balance FROM account ORDER BY id') == balances
    click(at, '查看流水')
    assert scene_context.snapshot(session)['period'] == '2026-08'


def test_read_checkpoint_is_owned_single_use_and_expires(monkeypatch, seeded):
    from agent import view_navigation
    scene_context.update('owner', intent='txn_query', period='2026-08', category='电商',
                         txn_id='selected', read_request={'date_from': '2026-08-02', 'date_to': '2026-08-10'})
    token = view_navigation.capture('owner')
    assert not view_navigation.restore('other-session', token)
    scene_context.update('owner', intent='transfer_single', category=None)
    assert view_navigation.restore('owner', token)
    assert scene_context.snapshot('owner')['category'] == '电商'
    assert scene_context.snapshot('owner')['txn_id'] == 'selected'
    assert scene_context.snapshot('owner')['read_request']['date_from'] == '2026-08-02'
    assert not view_navigation.restore('owner', token)
    token = view_navigation.capture('owner')
    now = view_navigation.monotonic()
    monkeypatch.setattr(view_navigation, 'monotonic', lambda: now + 1801)
    assert not view_navigation.restore('owner', token)
