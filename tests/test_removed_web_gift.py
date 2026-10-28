"""The web product no longer opens gift-planning forms or confirmation cards."""
from agent import classifier, confirm_card, llm, pending_slots
from tests.test_card_binding_ui import app
from tests.conftest import raw


def test_gift_plan_is_unavailable_without_creating_a_draft(monkeypatch, seeded):
    monkeypatch.setattr(classifier, 'classify', lambda *a, **kw:
        classifier.IntentOut(intent='gift_plan', confidence=1, slots={}))
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    at = app(monkeypatch, seeded)
    before = raw(seeded, 'SELECT id,balance,available FROM account ORDER BY id')
    at.chat_input[0].set_value('我要送礼').run()
    assert not at.exception
    session = at.session_state['session_id']
    assert not pending_slots.peek(session)
    assert not confirm_card.current(session)
    assert at.session_state['workspace-result']['turn']['intent'] == 'out_of_scope'
    assert not any(b.label in ('继续', '✅ 确认') for b in at.button)
    assert raw(seeded, 'SELECT id,balance,available FROM account ORDER BY id') == before
