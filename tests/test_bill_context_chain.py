"""Current bill selections remain scoped through transaction and card followups."""
from copy import deepcopy
import pytest

from agent import orchestrator, scene_context, scene_flow
from tests.test_scene_integration import linked
from tests.conftest import raw


def select_august(session='chain'):
    source = orchestrator.handle('查八月账单', session_id=session).model_dump()
    result = scene_flow.select_bill_category(session, source, '电商')
    assert result['ok'], result
    return source


def test_bill_category_largest_associated_card_chain(linked):
    before = raw(linked, 'SELECT id,balance FROM account ORDER BY id')
    select_august()
    candidates = scene_context.snapshot('chain')['candidates']['txn']
    expected = max(candidates, key=lambda row: abs(row['amount']))
    turn = orchestrator.handle('最大的一笔', session_id='chain')
    assert not turn.error_code, turn.reply
    assert scene_context.snapshot('chain')['txn_id'] == expected['id']
    turn = orchestrator.handle('用的是哪张卡', session_id='chain')
    assert not turn.error_code, turn.reply
    assert '无法确认实际付款卡' in turn.reply
    state = scene_context.snapshot('chain')
    assert state['candidates']['card']
    assert all(r['card_id'] == 'card_savings_0001' for r in state['candidates']['card'])
    detail = orchestrator.handle('这张卡的详情', session_id='chain')
    assert not detail.error_code and '银行卡' in detail.reply
    assert raw(linked, 'SELECT id,balance FROM account ORDER BY id') == before


def test_stale_bill_source_cannot_replace_month_selection(linked):
    source = select_august()
    turn = orchestrator.handle('九月呢', session_id='chain')
    assert not turn.error_code, turn.reply
    state = scene_context.snapshot('chain')
    assert state.get('txn_id') is None
    assert state.get('category') is None
    assert not scene_flow.select_bill_category('chain', source, '电商')['ok']
    assert scene_context.snapshot('chain') == state


def test_bill_source_is_session_and_owner_bound(linked):
    from tools import query
    source = select_august('one')
    assert not scene_flow.select_bill_category('two', source, '电商')['ok']
    query.set_current_user('another-user')
    assert not scene_flow.select_bill_category('one', source, '电商')['ok']
    query.set_current_user(None)
    changed = deepcopy(source)
    changed['trace_id'] = 'forged'
    assert not scene_flow.select_bill_category('one', changed, '电商')['ok']


def test_no_results_clears_selection_and_never_guesses_card(linked):
    select_august()
    orchestrator.handle('最大的一笔', session_id='chain')
    turn = orchestrator.handle('2020年一月账单', session_id='chain')
    assert not turn.error_code, turn.reply
    assert scene_context.snapshot('chain').get('txn_id') is None
    largest = orchestrator.handle('最大的一笔', session_id='chain')
    assert not largest.error_code and '没有符合条件' in largest.reply
    assert not scene_context.snapshot('chain').get('txn_id')
    card = orchestrator.handle('用的是哪张卡', session_id='chain')
    assert card.ask and '选择' in card.reply


def test_category_selection_audited_and_clear_preserves_period(linked):
    select_august()
    assert raw(linked, "SELECT id FROM audit_log WHERE session_id='chain' AND tool='scene_read'")
    scene_flow.clear_category('chain')
    state = scene_context.snapshot('chain')
    assert state['period'] == '2026-08'
    assert not state.get('category') and not state.get('txn_id')
    assert state['candidates'].get('txn') == []


def test_card_scope_change_clears_selected_transaction(linked):
    select_august()
    orchestrator.handle('最大的一笔', session_id='chain')
    assert scene_context.snapshot('chain')['txn_id']
    assert scene_flow.select_card('chain', 'card_savings_0001')['ok']
    assert not scene_context.snapshot('chain').get('txn_id')


def test_associated_card_read_rechecks_transaction_ownership(linked):
    from tools import _scene_reads
    result = _scene_reads.read('card_query', {'kind': 'txn_cards', 'txn_id': 'foreign', 'period': '2026-08'})
    assert not result.ok and str(result.error_code).endswith('FORBIDDEN')


def test_category_change_reuses_chart_and_clears_transaction(linked):
    source = select_august()
    orchestrator.handle('最大的一笔', session_id='chain')
    assert scene_flow.select_bill_category('chain', source, '餐饮')['ok']
    state = scene_context.snapshot('chain')
    assert not state.get('txn_id') and state['category'] == '餐饮'
    orchestrator.handle('最大的一笔', session_id='chain')
    turn = orchestrator.handle('只看收入', session_id='chain')
    assert not turn.error_code
    assert not scene_context.snapshot('chain').get('txn_id')


def test_failed_category_read_preserves_last_good_scope(linked, monkeypatch):
    from tools import _scene_reads
    from tools.schemas import ToolResult
    source = select_august()
    before = scene_context.snapshot('chain')
    monkeypatch.setattr(_scene_reads, 'read', lambda *args: ToolResult(ok=False, error_code='INVALID_STATE', message='retry'))
    assert not scene_flow.select_bill_category('chain', source, '餐饮')['ok']
    assert scene_context.snapshot('chain') == before


def test_new_same_month_chart_invalidates_old_source(linked):
    source = select_august()
    # Explicitly switch to a fresh full bill chart without its category filter.
    scene_flow.clear_category('chain')
    fresh = orchestrator.handle('查八月账单总结', session_id='chain').model_dump()
    assert fresh['trace_id'] != source['trace_id']
    assert not scene_flow.select_bill_category('chain', source, '电商')['ok']
    assert scene_flow.select_bill_category('chain', fresh, '电商')['ok']


def test_largest_is_computed_over_all_rows_before_display_limit(linked):
    from data import dao
    from tools import _scene_reads
    account = raw(linked, 'SELECT account_id FROM txn ORDER BY ts LIMIT 1')[0]['account_id']
    for index in range(60):
        dao.connection().execute(
            'INSERT INTO txn (id,account_id,ts,amount,direction,counterparty,category,channel,memo,balance_after) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            (f'largest-{index:03d}', account, f'2026-01-{index % 28 + 1:02d}T10:00:00',
             -1000000 - index, 'out', 'scope merchant', '电商', '卡', '', 0))
    dao.connection().commit()
    result = _scene_reads.read('txn_query', {'kind': 'txn_largest', 'period': '2026-01', 'category': '电商'})
    assert result.ok
    assert len(result.data['items']) == 1
    assert result.data['items'][0]['id'] == 'largest-059'


def test_bill_selection_refuses_after_leaving_bill_scene(linked):
    source = select_august()
    scene_context.update('chain', intent='transfer_single')
    assert not scene_flow.select_bill_category('chain', source, '电商')['ok']


def test_workspace_action_phrases_follow_selected_entities(linked):
    select_august()
    largest = orchestrator.handle('看看最大的一笔', session_id='chain')
    assert not largest.error_code and scene_context.snapshot('chain').get('txn_id'), largest.reply
    associated = orchestrator.handle('用的是哪张卡', session_id='chain')
    assert not associated.error_code
    detail = orchestrator.handle('看这张卡详情', session_id='chain')
    assert not detail.error_code and '银行卡' in detail.reply, detail.reply


@pytest.mark.parametrize('start,end', [('2026-08-10', '2026-08-12'), ('2026-07-30', '2026-08-02')])
def test_largest_and_associated_cards_preserve_explicit_date_bounds(linked, start, end):
    from agent import classifier
    first = orchestrator.handle('查询指定日期流水', session_id='range', intent_resolver=lambda *_:
        classifier.IntentOut(intent='txn_query', confidence=1, slots={'date_from': start, 'date_to': end}))
    assert not first.error_code
    expected = raw(linked, 'SELECT id FROM txn WHERE ts>=? AND ts<? ORDER BY ABS(amount) DESC,ts,id LIMIT 1',
                   (start, end + 'T23:59:59'))[0]['id']
    largest = orchestrator.handle('最大的一笔', session_id='range')
    assert not largest.error_code, largest.reply
    assert scene_context.snapshot('range')['txn_id'] == expected
    request = scene_context.snapshot('range')['read_request']
    assert request['date_from'] == start and request['date_to'] == end
    cards = orchestrator.handle('用的是哪张卡', session_id='range')
    assert not cards.error_code
    request = scene_context.snapshot('range')['read_request']
    assert request['date_from'] == start and request['date_to'] == end


def test_old_month_chart_rejects_narrowed_same_month_date_scope(linked):
    from agent import classifier
    source = select_august()
    orchestrator.handle('查询指定日期流水', session_id='chain', intent_resolver=lambda *_:
        classifier.IntentOut(intent='txn_query', confidence=1,
                             slots={'date_from': '2026-08-10', 'date_to': '2026-08-12'}))
    assert not scene_flow.select_bill_category('chain', source, '电商')['ok']


def test_date_scope_change_clears_previous_category_and_direction(linked):
    from agent import classifier
    select_august('range')
    first = orchestrator.handle('查询指定日期流水', session_id='range', intent_resolver=lambda *_:
        classifier.IntentOut(intent='txn_query', confidence=1,
                             slots={'date_from': '2026-08-10', 'date_to': '2026-08-12'}))
    assert not first.error_code
    state = scene_context.snapshot('range')
    assert state.get('category') is None and state.get('direction') is None


def test_category_bridge_keeps_validated_chart_date_bounds(linked):
    from tools.schemas import ToolResult
    source = orchestrator.handle('查八月账单', session_id='bounded-chart').model_dump()
    scene_context.remember_result('bounded-chart', 'bill_report', {'period': '2026-08'},
        ToolResult(ok=True, data={}, facts=source['facts']),
        {'date_from': '2026-08-10', 'date_to': '2026-08-12'})
    selected = scene_flow.select_bill_category('bounded-chart', source, '电商')
    assert selected['ok'], selected
    request = scene_context.snapshot('bounded-chart')['read_request']
    assert request['date_from'] == '2026-08-10' and request['date_to'] == '2026-08-12'


def test_card_detail_preserves_exact_same_month_bounds(linked):
    from agent import classifier
    orchestrator.handle('查询指定日期流水', session_id='card-range', intent_resolver=lambda *_:
        classifier.IntentOut(intent='txn_query', confidence=1,
                             slots={'date_from': '2026-08-10', 'date_to': '2026-08-12'}))
    detail = scene_flow.select_card('card-range', 'card_savings_0001')
    assert detail['ok']
    assert scene_context.read_scope(scene_context.snapshot('card-range')) == {
        'date_from': '2026-08-10', 'date_to': '2026-08-12', 'card_id': 'card_savings_0001'}
