"""F07 真实工具验证，预览不写、范围、凭证、限流与记录。"""
from datetime import timedelta

import pytest

from tests.conftest import raw
from tools import card_binding, query


def bound():
    result = card_binding.preview_binding('6222000000000001', '314159', session_id='lock')
    return card_binding.confirm_binding(result.data['token'], session_id='lock').data['card_id']


def test_lock_preview_confirm_idempotent_and_persistent_history(seeded):
    from agent import card_lock_flow as flow
    card = bound()
    before = raw(seeded, 'SELECT * FROM account')
    preview = flow.preview(card, 'lock')
    assert preview['ok'] and raw(seeded, 'SELECT status FROM card WHERE id=?', (card,))[0]['status'] == 'normal'
    assert not flow.submit(preview['data']['token'], '000000', 'lock')['ok']
    result = flow.submit(preview['data']['token'], '123456', 'lock')
    assert result['ok'] and result['data']['status'] == 'locked'
    assert flow.submit(preview['data']['token'], '123456', 'lock')['data'] == result['data']
    assert raw(seeded, 'SELECT * FROM account') == before
    assert len(flow.records()['data']['items']) == 1
    assert '123456' not in str(raw(seeded, "SELECT * FROM audit_log WHERE intent='card_lock'"))
    assert not flow.preview(card, 'lock')['ok']


def test_scope_expiry_cancel_and_status_change(seeded, monkeypatch):
    from agent import card_lock_flow as flow
    from tools import card_lock
    card = bound()
    token = flow.preview(card, 'lock')['data']['token']
    assert not flow.submit(token, '123456', 'other')['ok']
    query.set_current_user('other')
    assert not flow.submit(token, '123456', 'lock')['ok']
    query.set_current_user(None)
    flow.cancel(token, 'lock')
    assert not flow.submit(token, '123456', 'lock')['ok']
    token = flow.preview(card, 'lock')['data']['token']
    moment = card_lock._now()
    monkeypatch.setattr(card_lock, '_now', lambda: moment + timedelta(seconds=301))
    assert not flow.submit(token, '123456', 'lock')['ok']
    monkeypatch.setattr(card_lock, '_now', lambda: moment)
    token = flow.preview(card, 'lock')['data']['token']
    from data import dao
    dao.update_card(card, status='lost')
    assert not flow.submit(token, '123456', 'lock')['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card,))[0]['status'] == 'lost'


def test_unbound_card_and_otp_limit(seeded):
    from agent import card_lock_flow as flow
    assert not flow.preview('card_savings_0001', 'lock')['ok']
    card = bound()
    token = flow.preview(card, 'lock')['data']['token']
    for _ in range(5):
        assert not flow.submit(token, '000000', 'lock')['ok']
    assert not flow.submit(token, '123456', 'lock')['ok']
    other = flow.preview(card, 'new-session')['data']['token']
    assert not flow.submit(other, '123456', 'new-session')['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card,))[0]['status'] == 'normal'


def test_completed_lock_cannot_be_cancelled_and_keeps_idempotency(seeded):
    from agent import card_lock_flow as flow
    token = flow.preview(bound(), 'lock')['data']['token']
    result = flow.submit(token, '123456', 'lock')
    assert result['ok']
    assert not flow.cancel(token, 'lock')['ok']
    assert flow.submit(token, '123456', 'lock')['data'] == result['data']


def test_audit_failure_rolls_back_lock(seeded, monkeypatch):
    import sqlite3
    from agent import card_lock_flow as flow
    from data import dao
    card = bound()
    token = flow.preview(card, 'lock')['data']['token']
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('audit unavailable')
    monkeypatch.setattr(dao, 'insert_audit', fail)
    assert not flow.submit(token, '123456', 'lock')['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (card,))[0]['status'] == 'normal'


def test_concurrent_submit_writes_one_success(seeded):
    from concurrent.futures import ThreadPoolExecutor
    from agent import card_lock_flow as flow
    from data import dao
    card = bound()
    token = flow.preview(card, 'lock')['data']['token']
    def submit():
        try:
            return flow.submit(token, '123456', 'lock')
        finally:
            dao.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert all(result['ok'] for result in results)
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE intent='card_lock' AND tool='manage_card' AND result='success'")) == 1


def test_state_changed_at_write_boundary_is_not_overwritten(seeded, monkeypatch):
    from agent import card_lock_flow as flow
    from data import dao
    card = bound()
    token = flow.preview(card, 'lock')['data']['token']
    original = dao.get_card
    calls = 0
    def changed(card_id):
        nonlocal calls
        calls += 1
        row = original(card_id)
        if calls == 1:
            dao.update_card(card_id, status='lost')
        return row
    monkeypatch.setattr(dao, 'get_card', changed)
    assert not flow.submit(token, '123456', 'lock')['ok']
    assert original(card)['status'] == 'lost'


@pytest.mark.parametrize('text', ['锁卡', '暂时锁定这张卡', '停用银行卡', '锁卡有什么作用', '查看锁卡记录'])
def test_lock_language(text):
    from agent import card_lock_flow as flow
    assert flow.recognize(text) == ('records' if '记录' in text else 'lock')


@pytest.mark.parametrize('text', ['不要锁卡', '解锁银行卡', '银行卡挂失', '冻结账户', '查额度'])
def test_other_actions_do_not_open_lock(text):
    from agent import card_lock_flow as flow
    assert flow.recognize(text) not in ('lock', 'records', 'missing')
