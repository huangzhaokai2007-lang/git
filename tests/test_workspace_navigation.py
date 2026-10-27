"""Visible navigation follows the same validated read context as chat."""
from tests.test_card_binding_ui import app, click


def test_bill_ledger_detail_returns_and_descriptive_selection(monkeypatch, seeded):
    from agent import llm, scene_context
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('查八月账单').run()
    primary = next(b for b in at.button if b.label == '查看流水')
    secondary = next(b for b in at.button if b.label == '最大的一笔')
    assert primary.proto.type == 'primary'
    assert secondary.proto.type == 'tertiary'
    click(at, '查看流水')
    assert any(b.label == '返回账单' for b in at.button)
    session = at.session_state['session_id']
    row = scene_context.snapshot(session)['candidates']['txn'][0]
    click(at, '查看所选详情')
    message = at.session_state['messages'][-2]['text']
    assert message.startswith('查看交易：') and row['counterparty'] in message
    assert row['ts'][:10] in message and str(row['amount_yuan']) in message
    assert scene_context.snapshot(session)['txn_id'] == row['id']
    click(at, '返回流水')
    assert scene_context.snapshot(session).get('txn_id') is None
    assert len(scene_context.snapshot(session)['candidates']['txn']) > 1
    click(at, '返回账单')
    assert at.session_state['workspace-result']['turn']['intent'] == 'bill_report'
    assert scene_context.snapshot(session)['period'] == '2026-08'


def test_back_restores_filters_instead_of_leaking_current_filter(monkeypatch, seeded):
    from agent import llm, orchestrator, scene_context
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    orchestrator.handle('查八月账单', session_id='filter-back')
    orchestrator.handle('只看收入', session_id='filter-back')
    assert scene_context.snapshot('filter-back')['direction'] == 'income'
    result = orchestrator.handle('返回刚才的列表', session_id='filter-back')
    assert not result.error_code
    assert scene_context.snapshot('filter-back').get('direction') is None
