"""Self-service unlock is limited to a proven temporary lock and verified owner."""
from datetime import timedelta

import pytest

from tests.conftest import raw
from tests.test_card_lock_flow import bound
from tools import card_lock, query


def locked():
    card = bound()
    token = card_lock.preview(card, 'unlock-test').data['token']
    assert card_lock.submit(token, '123456', 'unlock-test').ok
    return card


def test_unlock_preview_identity_and_idempotency(seeded):
    from tools import card_unlock as flow
    card = locked()
    accounts = raw(seeded, 'SELECT * FROM account')
    preview = flow.preview(card, 'unlock-test')
    assert preview.ok and preview.facts['tier'] == 'L2'
    assert not preview.facts['to_human']
    assert not flow.submit(preview.data['token'], '000000', 'unlock-test').ok
    result = flow.submit(preview.data['token'], '123456', 'unlock-test')
    assert result.ok and result.data['status'] == 'normal'
    assert flow.submit(preview.data['token'], '123456', 'unlock-test').data == result.data
    assert raw(seeded, 'SELECT * FROM account') == accounts
    audits = raw(seeded, "SELECT * FROM audit_log WHERE tool='self_service_card_unlock' AND result='success'")
    assert len(audits) == 1
    assert '123456' not in str(audits) and '123456' not in str(audits)


@pytest.mark.parametrize('status', ['normal', 'lost', 'frozen', 'locked'])
def test_non_self_service_states_rejected(seeded, status):
    from data import dao
    from tools import card_unlock as flow
    card = bound()
    dao.update_card(card, status=status)
    assert not flow.preview(card, 'unlock-test').ok


def test_unlock_scope_cancel_expiry_and_relock(seeded, monkeypatch):
    from tools import card_unlock as flow
    card = locked()
    token = flow.preview(card, 'unlock-test').data['token']
    assert not flow.submit(token, '123456', 'other').ok
    query.set_current_user('other')
    assert not flow.submit(token, '123456', 'unlock-test').ok
    query.set_current_user(None)
    assert flow.cancel(token, 'unlock-test').ok
    assert not flow.submit(token, '123456', 'unlock-test').ok
    token = flow.preview(card, 'unlock-test').data['token']
    moment = flow._now()
    monkeypatch.setattr(flow, '_now', lambda: moment + timedelta(seconds=301))
    assert not flow.submit(token, '123456', 'unlock-test').ok
    monkeypatch.setattr(flow, '_now', lambda: moment)
    stale = flow.preview(card, 'unlock-test').data['token']
    fresh = flow.preview(card, 'unlock-test').data['token']
    assert flow.submit(fresh, '123456', 'unlock-test').ok
    lock = card_lock.preview(card, 'unlock-test').data['token']
    assert card_lock.submit(lock, '123456', 'unlock-test').ok
    assert not flow.submit(stale, '123456', 'unlock-test').ok


def test_unlock_attempts_cannot_bypass_by_switching_session(seeded):
    from tools import card_unlock as flow
    card = locked()
    for i in range(5):
        token = flow.preview(card, str(i)).data['token']
        assert not flow.submit(token, '000000', str(i)).ok
    token = flow.preview(card, 'new').data['token']
    assert not flow.submit(token, '123456', 'new').ok


def test_unlock_rechecks_state_and_audit_failure_rolls_back(seeded, monkeypatch):
    import sqlite3
    from data import dao
    from tools import card_unlock as flow
    card = locked()
    token = flow.preview(card, 'unlock-test').data['token']
    monkeypatch.setattr(dao, 'insert_audit', lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError('audit')))
    assert not flow.submit(token, '123456', 'unlock-test').ok
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card,))[0]['status'] == 'locked'


def test_missing_and_unlock_language_routes():
    from agent.card_lock_flow import recognize
    assert recognize('我找不到我的信用卡了') == 'missing'
    assert recognize('银行卡不见了') == 'missing'
    assert recognize('解锁我的银行卡') == 'unlock'
    assert recognize('我的银行卡没有丢') is None
    assert recognize('不要解锁我的卡') is None


def test_cancelled_temporary_preview_cannot_authorize_generic_lock(seeded):
    from tools import card, card_unlock as flow, subscription
    from tools._query_common import set_session_id
    card_id = bound()
    token = card_lock.preview(card_id, 'unlock-test').data['token']
    assert card_lock.cancel(token, 'unlock-test').ok
    set_session_id('unlock-test')
    ref = subscription.issue_confirm_ref(card.MANAGE_ACTION, card_id)
    assert card.manage_card(card_id, 'lock', confirm_ref=ref, otp='123456').ok
    assert not flow.preview(card_id, 'unlock-test').ok


@pytest.mark.parametrize('status', ['lost', 'frozen'])
def test_unlock_rechecks_changed_card_status(seeded, status):
    from data import dao
    from tools import card_unlock as flow
    card_id = locked()
    token = flow.preview(card_id, 'unlock-test').data['token']
    dao.update_card(card_id, status=status)
    assert not flow.submit(token, '123456', 'unlock-test').ok
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == status


@pytest.mark.parametrize('change', ['unbind', 'disable_account'])
def test_unlock_rechecks_binding_and_account(seeded, change):
    from data.db import connect
    from tools import card_unlock as flow
    card_id = locked()
    token = flow.preview(card_id, 'unlock-test').data['token']
    conn = connect(seeded)
    try:
        if change == 'unbind':
            conn.execute('DELETE FROM agent_card_binding WHERE card_id=?', (card_id,))
        else:
            conn.execute("UPDATE account SET status='frozen' WHERE id=(SELECT account_id FROM card WHERE id=?)", (card_id,))
    finally:
        conn.close()
    assert not flow.submit(token, '123456', 'unlock-test').ok
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card_id,))[0]['status'] == 'locked'


def test_concurrent_unlock_only_writes_one_success(seeded):
    from concurrent.futures import ThreadPoolExecutor
    from data import dao
    from tools import card_unlock as flow
    token = flow.preview(locked(), 'unlock-test').data['token']
    def submit():
        try:
            return flow.submit(token, '123456', 'unlock-test')
        finally:
            dao.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert all(result.ok for result in results)
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE tool='self_service_card_unlock' AND result='success'")) == 1
