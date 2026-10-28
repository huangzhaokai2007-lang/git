"""Read extensions retain their origin and return without replaying a query."""
from agent import classifier, llm, scene_context
from tests.test_card_binding_ui import app


def click(at, label):
    next(b for b in at.button if b.label == label and not b.disabled).click().run()
    assert not at.exception


def offline(monkeypatch):
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    def resolve(text, *a, **kw):
        intent = 'balance_query' if '余额' in text else 'txn_query' if '流水' in text else 'anomaly_check'
        slots = {} if intent == 'balance_query' else {'date_from': '2026-08-01', 'date_to': '2026-08-31'} if intent == 'txn_query' else {'period': '2026-08'}
        return classifier.IntentOut(intent=intent, confidence=1, slots=slots)
    monkeypatch.setattr(classifier, 'classify', resolve)


def test_anomaly_balance_nested_ledger_returns_without_requery(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('八月异常交易').run()
    original = at.session_state['workspace-result']['turn']['trace_id']
    context = scene_context.snapshot(at.session_state['session_id'])
    click(at, '💰 查一下余额')
    assert any(b.label == '返回异常检测' for b in at.button)
    assert any('原页面 · 异常检测' in e.label for e in at.expander)
    assert at.session_state['workspace-result']['turn']['intent'] == 'balance_query'
    history = list(at.session_state['messages'])
    click(at, '返回异常检测')
    assert at.session_state['workspace-result']['turn']['trace_id'] == original
    assert at.session_state['messages'] == history
    restored = scene_context.snapshot(at.session_state['session_id'])
    assert restored['period'] == context['period']
    assert restored['intent'] == context['intent']
    assert not any(b.label == '返回异常检测' for b in at.button)


def test_two_levels_return_one_at_a_time_and_shortcut_starts_new_root(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('八月异常交易').run()
    click(at, '📄 看流水明细')
    ledger = at.session_state['workspace-result']['turn']['trace_id']
    click(at, '🔍 查异常交易')
    assert any(b.label == '返回流水明细' for b in at.button)
    click(at, '返回流水明细')
    assert at.session_state['workspace-result']['turn']['trace_id'] == ledger
    assert any(b.label == '返回异常检测' for b in at.button)
    click(at, '管卡')
    assert 'workspace-nav-stack' not in at.session_state
    assert not any(b.label.startswith('返回') for b in at.button)


def test_failed_extension_keeps_origin_and_can_return(monkeypatch, seeded):
    from agent import card_binding_flow
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('八月异常交易').run()
    original = at.session_state['workspace-result']['turn']['trace_id']
    monkeypatch.setattr(card_binding_flow, 'handle', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    click(at, '💰 查一下余额')
    assert any('原页面 · 异常检测' in e.label for e in at.expander)
    assert len(at.error) == 1
    click(at, '返回异常检测')
    assert not at.error
    assert at.session_state['workspace-result']['turn']['trace_id'] == original


def test_back_from_wealth_order_draft_restores_source_without_execution(monkeypatch, seeded):
    from agent import confirm_card
    from tests.conftest import raw
    offline(monkeypatch)
    def resolve(text, *a, **kw):
        return classifier.IntentOut(intent='wealth_recommend', confidence=1, slots={'risk_level': 'R1'})
    monkeypatch.setattr(classifier, 'classify', resolve)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('推荐低风险理财').run()
    original = at.session_state['workspace-result']['turn']['trace_id']
    balances = raw(seeded, 'SELECT id,balance FROM account ORDER BY id')
    click(at, '订购')
    assert any(b.label == '返回理财推荐' for b in at.button)
    click(at, '返回理财推荐')
    assert confirm_card.current(at.session_state['session_id']) is None
    assert at.session_state['workspace-result']['turn']['trace_id'] == original
    assert raw(seeded, 'SELECT id,balance FROM account ORDER BY id') == balances


def test_retry_keeps_one_return_level(monkeypatch, seeded):
    from types import SimpleNamespace
    from agent import card_binding_flow
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('八月异常交易').run()
    handle = card_binding_flow.handle
    failed = {'intent': 'balance_query', 'error_code': 'SERVICE_UNAVAILABLE',
              'reply': '查询失败，请重试。', 'trace_id': 'failed-read', 'states': ['REPLY']}
    monkeypatch.setattr(card_binding_flow, 'handle', lambda *a, **kw: SimpleNamespace(
        error_code=failed['error_code'], reply=failed['reply'], model_dump=lambda: failed))
    click(at, '💰 查一下余额')
    assert len(at.session_state['workspace-nav-stack']) == 1
    assert len([b for b in at.button if b.label == '重试查询']) == 1
    monkeypatch.setattr(card_binding_flow, 'handle', handle)
    click(at, '重试查询')
    assert len(at.session_state['workspace-nav-stack']) == 1
    assert at.session_state['workspace-result']['turn']['intent'] == 'balance_query'
    click(at, '返回异常检测')
    assert not any(b.label == '返回异常检测' for b in at.button)


def test_return_preserves_submitted_pending_application(seeded):
    from types import SimpleNamespace
    from agent import confirm_card, view_navigation
    scene_context.update('pending-return', intent='txn_query', period='2026-08')
    token = view_navigation.capture('pending-return')
    item = SimpleNamespace(pending_id='submitted-read-return', session_id='pending-return',
                           revoked=False, released=False)
    confirm_card.remember_pending(item)
    scene_context.update('pending-return', pending_id=item.pending_id)
    assert view_navigation.restore('pending-return', token)
    assert scene_context.snapshot('pending-return')['pending_id'] == item.pending_id
    assert not item.revoked
