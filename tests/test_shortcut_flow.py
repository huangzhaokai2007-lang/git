"""Shortcut navigation discards drafts without resetting security or submitted work."""
from agent import confirm_card, payee_flow, payee_picker, scene_context, session_memory, shortcut_flow, write_flow
from tests.conftest import raw
from tools import transfer


def test_begin_is_session_scoped_and_preserves_security_and_pending_work(monkeypatch, seeded):
    for name, value in (('_CONFIRMATIONS', {}), ('_OTP_ERRORS', {}), ('_LOCKED', set()), ('_PENDING', {})):
        monkeypatch.setattr(confirm_card, name, value)
    session, other = 'shortcut-security', 'shortcut-other'
    write_flow.start('transfer_single', {'payee': '王五', 'amount': '100'}, session)
    draft = confirm_card.current(session)
    assert draft
    write_flow.start('transfer_single', {'payee': '王五', 'amount': '200'}, other)
    other_draft = confirm_card.current(other)
    assert other_draft
    confirm_card.note_otp_error(session)
    confirm_card._LOCKED.add(other)
    pending = confirm_card.Pending(pending_id='submitted-pending', session_id=session,
        credential_kind='confirm_ref', credential='submitted-credential', target_id='payee_0003',
        amount_cents=10000, created_at='2026-09-12T12:00:00')
    confirm_card.remember_pending(pending)
    scene_context.update(session, card_id='card_savings_0001', period='2026-08', pending_id=pending.pending_id)
    session_memory.remember(session, 'old query', 'old response')
    payee_flow.capture_pending(session, {'name': '新收款人'})
    payee_picker.capture(session, 'transfer_single', {}, [{'id': 'payee_0003', 'name': '王五'}])
    before = raw(seeded, 'SELECT id,balance FROM account ORDER BY id')

    shortcut_flow.begin(session)

    assert confirm_card.current(session) is None
    assert confirm_card.current(other) == other_draft
    assert confirm_card.otp_errors(session) == 1 and confirm_card.is_locked(other)
    assert confirm_card.pending_of(pending.pending_id, session) == pending
    assert scene_context.snapshot(session) == {'candidates': {}, 'pending_id': pending.pending_id}
    assert session_memory.recent(session) == []
    assert not payee_flow.pending_slots(session) and payee_picker.pending(session) is None
    transfer.set_session_id(session)
    assert not transfer.execute_transfer(draft.credential, '123456').ok
    assert raw(seeded, 'SELECT id,balance FROM account ORDER BY id') == before
    audit = raw(seeded, "SELECT params_json FROM audit_log WHERE session_id=? AND tool='cancel_shortcut_draft'", (session,))
    assert audit and draft.credential not in audit[0]['params_json']
