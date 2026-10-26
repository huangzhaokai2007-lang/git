"""Pie drill-down stays in the original bill scope and never resumes writes."""
import pytest

from agent import bill_detail_flow, confirm_card, scene_context
from tests.conftest import FOREIGN_TXN, raw
from tools import query


def source(period='2026-08', category='电商', group_by='category'):
    return {'intent': 'bill_report', 'trace_id': 'bill-origin', 'facts': {
        'period': period, 'group_by': group_by, 'groups': [{'key': category}]}}


def test_detail_matches_original_period_category_expenses_and_is_audited(foreign):
    result = bill_detail_flow.load('pie-session', source(category='餐饮'), '餐饮')
    expected = raw(foreign, "SELECT t.id,t.amount FROM txn t JOIN account a ON a.id=t.account_id "
                   "WHERE a.user_id='u_zhangsan_0001' AND t.ts>='2026-08-01' AND t.ts<'2026-09-01' "
                   "AND t.category='餐饮' AND t.amount<0 ORDER BY t.ts DESC,t.id DESC")
    assert result['ok'] and expected
    assert [r['id'] for r in result['items']] == [r['id'] for r in expected]
    assert FOREIGN_TXN not in [r['id'] for r in result['items']]
    assert result['count'] == len(expected)
    assert result['amount'] == -sum(r['amount'] for r in expected)
    audit = raw(foreign, "SELECT session_id,tool,params_json FROM audit_log WHERE trace_id=?", (result['trace_id'],))
    assert audit[0]['session_id'] == 'pie-session'
    assert audit[0]['tool'] == 'bill_detail_read'
    assert 'bill-origin' in audit[0]['params_json']


def test_all_details_are_scrollable_without_fifty_row_truncation(seeded):
    result = bill_detail_flow.load('year', source('2026', '餐饮'), '餐饮')
    assert result['ok'] and result['count'] > 50
    assert len(result['items']) == result['count']


@pytest.mark.parametrize('period', ['2026-02', '2026-08'])
def test_empty_or_other_period_does_not_fall_back_to_current_month(seeded, period):
    result = bill_detail_flow.load('period', source(period, '不存在的分类'), '不存在的分类')
    assert result['ok'] and result['items'] == [] and result['count'] == 0


def test_unlisted_category_is_rejected_without_querying(seeded, monkeypatch):
    from tools import _bill_details
    monkeypatch.setattr(_bill_details, 'read', lambda *_: pytest.fail('invalid category reached tool'))
    result = bill_detail_flow.load('bad', source(), '其他人的分类')
    assert not result['ok']


def test_drilldown_keeps_current_scene_and_pending_confirmation(seeded):
    from agent import classifier, orchestrator
    turn = orchestrator.handle('给王五转100元', session_id='draft', intent_resolver=lambda *_:
        classifier.IntentOut(intent='transfer_single', confidence=1, slots={'payee': '王五', 'amount': '100'}))
    assert 'CONFIRM_CARD' in turn.states
    current = confirm_card.current('draft')
    before = scene_context.snapshot('draft')
    balances = raw(seeded, 'SELECT id,balance FROM account ORDER BY id')
    assert bill_detail_flow.load('draft', source(), '电商')['ok']
    assert confirm_card.current('draft').credential == current.credential
    assert scene_context.snapshot('draft') == before
    assert raw(seeded, 'SELECT id,balance FROM account ORDER BY id') == balances


def test_channel_grouping_uses_channel_and_excludes_income(seeded):
    group = query.analyze_spending('2026-08', 'channel').facts['groups'][0]['key']
    result = bill_detail_flow.load('channel', source(category=group, group_by='channel'), group)
    assert result['ok'] and result['items']
    assert all(r['channel'] == group and r['amount'] < 0 for r in result['items'])


def test_month_end_is_included_and_next_month_is_excluded(seeded):
    from data import dao
    #: `ref_no` 是 NOT NULL 且无默认值，手写裸 SQL 漏它会直接 `IntegrityError`（看着像被测代码的锅）。
    for index, (suffix, ts) in enumerate([('last', '2026-08-31T23:59:59'), ('next', '2026-09-01T00:00:00')]):
        digits = ''.join(char for char in ts if char.isdigit())
        dao.connection().execute('INSERT INTO txn (id,account_id,ts,amount,direction,category,'
                                 'balance_after,ref_no) VALUES (?,?,?,?,?,?,?,?)',
                                 ('boundary-' + suffix, 'acc_savings_0001', ts, -100, 'out', '边界', 0,
                                  f'{digits}{index:03d}'))
    dao.connection().commit()
    result = bill_detail_flow.load('boundary', source(category='边界'), '边界')
    assert result['ok'] and [r['id'] for r in result['items']] == ['boundary-last']


@pytest.mark.parametrize('period', [None, '2026-13', '2026-08 OR 1=1'])
def test_invalid_period_is_rejected(seeded, period):
    assert not bill_detail_flow.load('bad-period', source(period), '电商')['ok']
