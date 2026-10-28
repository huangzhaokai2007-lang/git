"""Bill replies show facts-backed pies once, with readable horizontal chart labels."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import pyarrow as pa

from tests.test_web_insight_card import _app, _text


def test_pie_animation_lifecycle_and_safe_labels():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/js/spending_pie_runtime.cjs')],
                            cwd=root, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_report_replaces_only_category_table_and_has_no_duplicate_breakdown(monkeypatch):
    at = _app(monkeypatch)
    at.chat_input[0].set_value('上个月的账单总结').run()
    assert not at.exception
    page = _text(at)
    assert '| 项目 | 金额（元） | 占比 |' not in page
    assert '总览' in page and '订阅（进行中）' in page and '异常提示' in page
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1
    payload = json.loads(pies[0].proto.json)
    turn = at.session_state['messages'][-1]['turn']
    assert payload['groups'] == turn['facts']['groups']
    assert '| 项目 | 金额（元） | 占比 |' in turn['reply']  # Audit/original reply remains intact.
    assert not at.dataframe
    charts = at.get('vega_lite_chart')
    assert len(charts) == 2  # income/expense comparison and monthly trend
    for chart in charts:
        spec = json.loads(chart.proto.spec)
        assert spec['encoding']['x']['axis']['labelAngle'] == 0
        rows = pa.ipc.open_stream(chart.proto.data.data).read_all().to_pylist()
        assert rows and all('label' in row and 'amount' in row for row in rows)
    assert pa.ipc.open_stream(charts[0].proto.data.data).read_all().to_pylist() == [
        {'label': '支出', 'amount': 9152.0}, {'label': '收入', 'amount': 11046.0}]
    assert pa.ipc.open_stream(charts[1].proto.data.data).read_all().to_pylist() == [
        {'label': '2026-08', 'amount': 9152.0}]


def test_pie_is_retained_in_chat_history_without_repeating_in_bill_panel(monkeypatch):
    at = _app(monkeypatch)
    at.chat_input[0].set_value('上个月花了多少').run()
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1
    identity = json.loads(pies[0].proto.json)['animation_id']
    at.chat_input[0].set_value('上个月有没有异常交易').run()
    assert not at.exception
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1
    assert json.loads(pies[0].proto.json)['animation_id'] == identity
    assert '| 分类 | 金额（元） | 占比 |' not in _text(at)


def test_pie_selection_loads_details_without_appending_chat_and_caches_redraw(monkeypatch):
    from agent import bill_detail_flow
    at = _app(monkeypatch)
    at.chat_input[0].set_value('上个月的账单总结').run()
    messages = list(at.session_state['messages'])
    turn = messages[-1]['turn']
    key = f"pie-{len(messages) - 1}:{turn['trace_id']}"
    calls = []
    original = bill_detail_flow.load
    def load(session, source, category):
        calls.append((source['facts']['period'], category))
        return original(session, source, category)
    monkeypatch.setattr(bill_detail_flow, 'load', load)
    at.session_state[key] = {'selected': '电商'}
    at.run()
    assert not at.exception
    pie = next(e for e in at.get('bidi_component') if e.proto.component_name.endswith('spending_pie'))
    payload = json.loads(pie.proto.json)
    assert payload['selected'] == '电商'
    assert payload['detail']['ok'] and payload['detail']['items']
    assert all(item['category'] == '电商' and item['amount'] < 0 for item in payload['detail']['items'])
    assert calls == [('2026-08', '电商')]
    at.run()
    assert calls == [('2026-08', '电商')]
    assert at.session_state['messages'] == messages
    at.session_state[key] = {'selected': {'category': '餐饮', 'focus_target': 'ledger', 'focus_id': 'kbd-1'}}
    at.run()
    pie = next(e for e in at.get('bidi_component') if e.proto.component_name.endswith('spending_pie'))
    payload = json.loads(pie.proto.json)
    assert payload['selected'] == '餐饮' and payload['focus_target'] == 'ledger'
    assert payload['focus_id'] == 'kbd-1'
