"""Balance refresh replaces the sidebar value without adding chat messages."""
import sqlite3
from types import SimpleNamespace

from agent import balance_flow, llm, scene_context
from data.seed import SAVINGS_ID
from tests.conftest import raw
from tests.test_card_binding_ui import app, click


def test_refresh_replaces_balance_and_preserves_chat_context(monkeypatch, seeded):
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    at = app(monkeypatch, seeded)
    session = at.session_state['session_id']
    scene_context.update(session, intent='bill_report', period='2026-08')
    messages = list(at.session_state['messages'])
    turns = list(at.session_state['turns'])
    before = len(raw(seeded, "SELECT * FROM audit_log WHERE session_id=? AND intent='balance_query'",
                     (session + ':sidebar-balance',)))
    click(at, '刷新余额')
    with sqlite3.connect(seeded) as conn:
        conn.execute('UPDATE account SET balance=?,available=? WHERE id=?', (9995000, 9995000, SAVINGS_ID))
    click(at, '刷新余额')
    visible = '\n'.join(str(item.value) for item in (*at.sidebar.markdown, *at.sidebar.caption))
    assert visible.count('您的储蓄账户余额为：') == 1
    assert visible.count('99,950.00 RMB') == 1
    assert '可用余额' not in visible and '截至' not in visible and 'trace-' not in visible
    assert len(at.session_state['balance']) == 1
    assert at.session_state['messages'] == messages and at.session_state['turns'] == turns
    assert scene_context.snapshot(session)['period'] == '2026-08'
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE session_id=? AND intent='balance_query'",
                   (session + ':sidebar-balance',))) == before + 2


def test_open_and_reset_refresh_once_without_chat_messages(monkeypatch, seeded):
    calls = []
    original = balance_flow.refresh

    def refresh(session):
        calls.append(session)
        return original(session)

    monkeypatch.setattr(balance_flow, 'refresh', refresh)
    at = app(monkeypatch, seeded)
    assert len(calls) == 1
    assert len(at.session_state['balance']) == 1
    assert len(at.session_state['messages']) == 1
    assert at.session_state['turns'] == []
    at.run()
    assert len(calls) == 1
    click(at, '重置会话')
    assert not at.exception
    assert len(calls) == 2
    assert len(at.session_state['balance']) == 1
    assert len(at.session_state['messages']) == 1
    assert at.session_state['turns'] == []
    at.run()
    assert len(calls) == 2


def test_failed_auto_refresh_does_not_retry_on_rerun(monkeypatch, seeded):
    calls = []

    def unavailable(session):
        calls.append(session)
        return SimpleNamespace(error_code='UNAVAILABLE', reply='余额暂时不可用')

    monkeypatch.setattr(balance_flow, 'refresh', unavailable)
    at = app(monkeypatch, seeded)
    assert len(calls) == 1
    assert any(item.value == '余额暂时不可用' for item in at.error)
    assert at.session_state['balance'] == []
    at.run()
    assert len(calls) == 1
    click(at, '刷新余额')
    assert len(calls) == 2
    assert not at.exception
    assert len(at.session_state['messages']) == 1
