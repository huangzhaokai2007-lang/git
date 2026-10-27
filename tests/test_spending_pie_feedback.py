"""Pie feedback stays local, retries failed reads and notifies current context once."""
from types import SimpleNamespace

from interfaces.web import spending_pie


TEXT = '| 分类 | 金额（元） | 占比 |\n|---|---|---|\n| 电商 | 10.00 | 100 |\n'


def harness(monkeypatch):
    state, payloads = {}, []
    monkeypatch.setattr(spending_pie, 'st', SimpleNamespace(session_state=state, markdown=lambda _: None))
    monkeypatch.setattr(spending_pie.components, 'component', lambda *a, **kw: lambda **kw: payloads.append(kw))
    turn = {'intent': 'bill_analysis', 'facts': {'period': '2026-08', 'groups': [
        {'key': '电商', 'amount_yuan': '10.00', 'pct': 100},
        {'key': '餐饮', 'amount_yuan': '2.00', 'pct': 20}]}}
    return state, payloads, turn


def test_retry_reloads_only_failed_detail_and_notifies_success_once(monkeypatch):
    state, payloads, turn = harness(monkeypatch)
    state['pie-current'] = {'selected': '电商'}
    calls, selected = [], []
    def loader(source, category):
        calls.append(category)
        return {'ok': len(calls) > 1, 'items': []}
    def render():
        return spending_pie.render_reply(TEXT, turn, key='current', animate=False,
            detail_loader=loader, on_category=lambda source, category: selected.append((source, category)))
    assert render()
    assert calls == ['电商'] and selected == []
    render()
    assert calls == ['电商'], 'ordinary rerun must retain a failed result for explicit retry'
    identity = payloads[-1]['data']['animation_id']
    state['pie-current'] = {'selected': {'category': '电商', 'retry_id': 'retry-1'}}
    render()
    assert calls == ['电商', '电商'] and selected == [(turn, '电商')]
    render()
    assert calls == ['电商', '电商'] and len(selected) == 1
    assert payloads[-1]['data']['animation_id'] == identity
    state['pie-current'] = {'selected': {'category': '电商', 'retry_id': 'retry-2'}}
    render()
    assert calls == ['电商', '电商'], 'retry cannot reissue a successful read'
    state['pie-current'] = {'selected': '餐饮'}
    render()
    assert [category for _, category in selected] == ['电商', '餐饮']


def test_default_chart_selection_is_read_only_and_invalid_category_never_loads(monkeypatch):
    state, payloads, turn = harness(monkeypatch)
    calls = []
    state['pie-history'] = {'selected': '不存在'}
    spending_pie.render_reply(TEXT, turn, key='history', animate=False,
        detail_loader=lambda source, category: calls.append(category))
    assert calls == [] and payloads[-1]['data']['selected'] is None
    state['pie-history'] = {'selected': '电商'}
    spending_pie.render_reply(TEXT, turn, key='history', animate=False,
        detail_loader=lambda source, category: {'ok': True, 'items': []})
    assert payloads[-1]['data']['detail']['ok']


def test_failed_context_update_keeps_retry_available_and_never_marks_success(monkeypatch):
    state, payloads, turn = harness(monkeypatch)
    state['pie-current'] = {'selected': '电商'}
    promotions = []
    def promote(source, category):
        promotions.append(category)
        return {'ok': len(promotions) > 1, 'message': '查询范围暂时无法更新，请重试。'}
    def render():
        spending_pie.render_reply(TEXT, turn, key='current', animate=False,
            detail_loader=lambda *a: {'ok': True, 'items': []}, on_category=promote)
    render()
    assert not payloads[-1]['data']['detail']['ok']
    assert 'pie-current-notified' not in state
    render()
    assert promotions == ['电商'], 'ordinary reruns must wait for explicit retry'
    state['pie-current'] = {'selected': {'category': '电商', 'retry_id': 'retry-context'}}
    render()
    assert payloads[-1]['data']['detail']['ok']
    assert promotions == ['电商', '电商']
    assert state['pie-current-notified'] == '电商'


def test_retry_exception_is_cached_by_event_until_user_retries_again(monkeypatch):
    state, payloads, turn = harness(monkeypatch)
    calls = []
    def loader(source, category):
        calls.append(category)
        raise RuntimeError('unavailable')
    state['pie-current'] = {'selected': '电商'}
    def render():
        spending_pie.render_reply(TEXT, turn, key='current', animate=False, detail_loader=loader)
    render()
    assert payloads[-1]['data']['detail'] == {'ok': False, 'message': '暂时无法读取明细，请重试。'}
    state['pie-current'] = {'selected': {'category': '电商', 'retry_id': 'retry-1'}}
    render()
    render()
    assert calls == ['电商', '电商'], 'one retained retry event must not cause a read on each rerun'
    state['pie-current'] = {'selected': {'category': '电商', 'retry_id': 'retry-2'}}
    render()
    assert calls == ['电商', '电商', '电商']


def test_compact_pie_omits_surrounding_report_and_preserves_exact_groups(monkeypatch):
    state, payloads, turn = harness(monkeypatch)
    before, after = [], []
    monkeypatch.setattr(spending_pie.reply_animation, 'render', lambda text, *args, **kw: before.append(text))
    monkeypatch.setattr(spending_pie.st, 'markdown', after.append)
    text = '总览原文\n\n' + TEXT + '\n订阅与异常原文'
    assert spending_pie.render_reply(text, turn, key='compact', animate=False, compact=True)
    assert before == [] and after == []
    assert len(payloads) == 1 and payloads[0]['data']['groups'] == turn['facts']['groups']
    assert spending_pie.render_reply(text, turn, key='default', animate=False)
    assert before == ['总览原文'] and after == ['订阅与异常原文']
