"""F09 persistent card-only loss and replacement acceptance tests."""
from datetime import timedelta
import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from data import dao
from tests.conftest import raw
from tools import card_binding, query


def bound():
    result = card_binding.preview_binding('6222000000000001', '123456', session_id='lost')
    return card_binding.confirm_binding(result.data['token'], session_id='lost').data['card_id']


def prepared():
    from agent import card_lost_flow as flow
    card_id = bound()
    return flow, card_id, flow.preview(card_id, 'lost')['data']


def submit(flow, preview, **kw):
    return flow.submit(preview['token'], kw.get('pin', '123456'), kw.get('otp', '123456'),
                       kw.get('branch', preview['default_branch_id']), kw.get('session', 'lost'))


def test_preview_no_card_mutation_and_no_sensitive_return(seeded):
    flow, card_id, p = prepared()
    assert p['estimated_days'] == 15 and p['total_fee_yuan'] == '0.00'
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'normal'
    assert not any(secret in str(p) for secret in ['123456', '6222000000000001'])
    assert not submit(flow, p, session='other')['ok']
    assert not submit(flow, p, branch='tampered')['ok']


def test_countdown_restart_idempotency_card_only(seeded, monkeypatch):
    from tools import card_lost
    flow, card_id, p = prepared()
    before = raw(seeded, 'SELECT * FROM account')
    result = submit(flow, p)
    assert result['ok'] and result['data']['status'] == 'pending'
    assert submit(flow, p)['data']['application_id'] == result['data']['application_id']
    assert not flow.preview(card_id, 'lost')['ok']
    importlib.reload(card_lost)
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    items = flow.records('lost')['data']['items']
    assert items[0]['status'] == 'success' and items[0]['replacement_status'] == 'pending_review'
    assert items[0]['to_human'] is True
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'lost'
    assert raw(seeded, 'SELECT * FROM account') == before
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE intent='card_report_lost' AND result='success'")) == 1


def test_cancel_preserves_locked_origin(seeded, monkeypatch):
    flow, card_id, p = prepared()
    dao.update_card(card_id, status='locked')
    p = flow.preview(card_id, 'lost')['data']
    app = submit(flow, p)['data']
    query.set_current_user('other')
    assert not flow.cancel(app['application_id'], 'other')['ok']
    query.set_current_user(None)
    assert flow.cancel(app['application_id'], 'lost')['data']['status'] == 'cancelled'
    assert not submit(flow, p)['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'locked'


@pytest.mark.parametrize('credential', ['pin', 'otp'])
def test_verification_failure_and_persistent_limit(seeded, credential):
    from tools import card_lost
    flow, card_id, p = prepared()
    for _ in range(5):
        assert not submit(flow, p, **{credential: 'bad'})['ok']
    importlib.reload(card_lost)
    assert not submit(flow, p)['ok']
    assert flow.records('lost')['data']['items'] == []
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'normal'


@pytest.mark.parametrize('state', ['frozen', 'lost'])
def test_unsupported_state(seeded, state):
    flow, card_id, p = prepared()
    dao.update_card(card_id, status=state)
    assert not flow.preview(card_id, 'lost')['ok']
    assert not submit(flow, p)['ok']


def test_foreign_unbound_credit_refused(foreign):
    from agent import card_lost_flow as flow
    assert not flow.preview('card_x_0001', 'lost')['ok']
    assert not flow.preview('card_savings_0001', 'lost')['ok']
    card_id = bound()
    dao.connection().execute("UPDATE card SET type='credit' WHERE id=?", (card_id,))
    assert not flow.preview(card_id, 'lost')['ok']


def test_state_change_and_ownership_between_confirmation_execution(seeded, monkeypatch):
    from tools import card_lost
    flow, card_id, p = prepared()
    submit(flow, p)
    dao.update_card(card_id, status='frozen')
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    assert flow.records('lost')['data']['items'][0]['status'] == 'failed'
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'frozen'


def test_audit_failure_rolls_back_state_and_application(seeded, monkeypatch):
    from tools import card_lost
    flow, card_id, p = prepared()
    submit(flow, p)
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('audit unavailable')
    with monkeypatch.context() as m:
        m.setattr(dao, 'insert_audit', fail)
        assert not flow.records('lost')['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'normal'
    assert flow.records('lost')['data']['items'][0]['status'] == 'success'


def test_concurrent_submit_and_no_secrets_in_storage(seeded):
    flow, card_id, p = prepared()
    def execute(_):
        try:
            return submit(flow, p)
        finally:
            dao.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert all(r['ok'] for r in results)
    assert results[0]['data']['application_id'] == results[1]['data']['application_id']
    rows = raw(seeded, 'SELECT * FROM agent_card_lost_request')
    assert len([r for r in rows if r['status'] == 'pending']) == 1
    assert not any(x in str(rows) for x in ['123456', '6222000000000001'])


def test_expired_preview_and_late_cancel(seeded, monkeypatch):
    from tools import card_lost
    flow, card_id, p = prepared()
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=301))
    assert not submit(flow, p)['ok']
    p = flow.preview(card_id, 'lost')['data']
    app = submit(flow, p)['data']
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=362))
    assert not flow.cancel(app['application_id'], 'lost')['ok']
    assert flow.records('lost')['data']['items'][0]['status'] == 'success'


def test_record_isolation_and_unbound_during_delay(seeded, monkeypatch):
    from tools import card_lost
    flow, card_id, p = prepared()
    submit(flow, p)
    query.set_current_user('other')
    assert flow.records('other')['data']['items'] == []
    query.set_current_user(None)
    dao.connection().execute('DELETE FROM agent_card_binding WHERE card_id=?', (card_id,))
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    assert flow.records('lost')['data']['items'][0]['status'] == 'failed'


def test_lost_from_locked_cannot_use_old_self_service_unlock(seeded, monkeypatch):
    from tools import card_lost, card_lock, card_unlock
    flow, card_id, p = prepared()
    lock_token = card_lock.preview(card_id, 'lost').data['token']
    assert card_lock.submit(lock_token, '123456', 'lost').ok
    unlock_token = card_unlock.preview(card_id, 'lost').data['token']
    p = flow.preview(card_id, 'lost')['data']
    submit(flow, p)
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    assert flow.records('lost')['data']['items'][0]['status'] == 'success'
    assert not card_unlock.submit(unlock_token, '123456', 'lost').ok
    assert dao.get_card(card_id)['status'] == 'lost'


def test_cancel_rechecks_current_card_ownership(seeded):
    flow, card_id, p = prepared()
    app = submit(flow, p)['data']
    dao.connection().execute("UPDATE card SET user_id='other' WHERE id=?", (card_id,))
    assert not flow.cancel(app['application_id'], 'lost')['ok']


def test_cancel_execute_race_has_single_terminal_result(seeded, monkeypatch):
    from tools import card_lost
    flow, card_id, p = prepared()
    app = submit(flow, p)['data']
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    def execute(action):
        try:
            return flow.cancel(app['application_id'], 'lost') if action else flow.records('lost')
        finally:
            dao.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, [True, False]))
    assert flow.records('lost')['data']['items'][0]['status'] == 'success'
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE intent='card_report_lost' AND result='success'")) == 1


def test_chat_entry_audited_and_profile_comes_from_user(seeded):
    from agent import card_lost_flow as flow
    turn = flow.chat_entry('lost', 'choice')
    assert turn.ask == 'card_lost_choice' and turn.trace_id
    assert raw(seeded, "SELECT * FROM audit_log WHERE trace_id=?", (turn.trace_id,))
    card_id = bound()
    dao.connection().execute("UPDATE user SET phone='139****6789',name='合成客户' WHERE id=(SELECT user_id FROM card WHERE id=?)", (card_id,))
    data = flow.preview(card_id, 'lost')['data']
    assert data['phone_mask'] == '139****6789' and data['customer_name_mask'] == '合**'


def test_revoke_restored_pending_request_in_new_session(seeded):
    flow, card_id, p = prepared()
    app = submit(flow, p)['data']
    assert flow.cancel(app['application_id'], 'after-browser-restart')['ok']


@pytest.mark.parametrize('case', ['foreign', 'state', 'session', 'branch', 'expired', 'changed'])
def test_rejected_requests_leave_safe_audit(seeded, monkeypatch, case):
    from tools import card_lost
    flow, card_id, p = prepared()
    before = len(raw(seeded, "SELECT * FROM audit_log WHERE intent='card_report_lost' AND result='rejected'"))
    if case == 'foreign':
        result = flow.preview('foreign-secret-card', 'lost')
    elif case == 'state':
        dao.update_card(card_id, status='frozen')
        result = flow.preview(card_id, 'lost')
    elif case == 'session':
        result = submit(flow, p, session='different')
    elif case == 'branch':
        result = submit(flow, p, branch='tampered')
    elif case == 'expired':
        now = card_lost._now()
        monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=301))
        result = submit(flow, p)
    else:
        dao.update_card(card_id, status='locked')
        result = submit(flow, p)
    assert not result['ok']
    rejected = raw(seeded, "SELECT * FROM audit_log WHERE intent='card_report_lost' AND result='rejected'")
    assert len(rejected) == before + 1
    assert 'foreign-secret-card' not in str(rejected)
    assert not any(x in str(rejected) for x in ['123456', '6222000000000001'])


def test_application_audit_contains_application_id(seeded):
    flow, card_id, p = prepared()
    app = submit(flow, p)['data']
    flow.cancel(app['application_id'], 'lost')
    rows = raw(seeded, "SELECT params_json FROM audit_log WHERE intent='card_report_lost' AND tool='card_lost'")
    assert sum(app['application_id'] in row['params_json'] for row in rows) == 2


def test_locked_verification_attempt_is_audited(seeded):
    flow, card_id, p = prepared()
    for _ in range(5):
        assert not submit(flow, p, pin='000000')['ok']
    before = len(raw(seeded, "SELECT * FROM audit_log WHERE intent='card_report_lost' AND result='rejected'"))
    assert not submit(flow, p)['ok']
    after = raw(seeded, "SELECT * FROM audit_log WHERE intent='card_report_lost' AND result='rejected'")
    assert len(after) == before + 1
    assert 'verify_locked' in after[-1]['params_json']
