"""One facts-backed pie belongs to the current workspace; history preserves receipts."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

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
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1
    payload = json.loads(pies[0].proto.json)
    turn = at.session_state['messages'][-1]['turn']
    assert payload['groups'] == turn['facts']['groups']
    facts = turn['facts']
    values = {metric.value for metric in at.metric}
    assert {facts['out_sum_yuan'], facts['in_sum_yuan'], facts['net_yuan']} <= values
    assert all(section in turn['reply'] for section in ('总览', '订阅（进行中）', '异常提示'))
    assert '| 项目 | 金额（元） | 占比 |' in turn['reply']  # Audit/original reply remains intact.
    assert not at.dataframe
    assert not at.get('vega_lite_chart'), 'the current pie must not be duplicated by expense bars or trends'
    assert at.session_state['workspace-result']['turn'] == turn
    assert all(not message.get('bidi_component') for message in at.chat_message), 'history contains no live pie'


def test_new_business_result_removes_old_pie_and_keeps_original_receipt(monkeypatch):
    at = _app(monkeypatch)
    at.chat_input[0].set_value('上个月花了多少').run()
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1
    original = dict(at.session_state['messages'][-1])
    assert all(not message.get('bidi_component') for message in at.chat_message)
    at.chat_input[0].set_value('上个月有没有异常交易').run()
    assert not at.exception
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert not pies, 'an anomaly result replaces the previous live bill pie'
    assert original in at.session_state['messages'], 'history keeps the original full receipt and facts'
    assert at.session_state['workspace-result']['turn']['intent'] == 'anomaly_check'
    assert '| 分类 | 金额（元） | 占比 |' not in _text(at)


def test_pie_selection_loads_details_without_appending_chat_and_caches_redraw(monkeypatch):
    from agent import bill_detail_flow, scene_flow
    at = _app(monkeypatch)
    at.chat_input[0].set_value('上个月的账单总结').run()
    messages = list(at.session_state['messages'])
    turn = messages[-1]['turn']
    key = f"pie-workspace-{turn['trace_id']}"
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
    context = scene_flow.context(at.session_state['session_id'])
    assert context['period'] == '2026-08' and context['category'] == '电商'
    assert context['candidates']['txn']
    identity = payload['animation_id']
    at.run()
    assert calls == [('2026-08', '电商')]
    assert at.session_state['messages'] == messages
    pie = next(e for e in at.get('bidi_component') if e.proto.component_name.endswith('spending_pie'))
    assert json.loads(pie.proto.json)['animation_id'] == identity
    assert all(not message.get('bidi_component') for message in at.chat_message)
    at.session_state[key] = {'selected': {'category': '餐饮', 'focus_target': 'ledger', 'focus_id': 'kbd-1'}}
    at.run()
    pie = next(e for e in at.get('bidi_component') if e.proto.component_name.endswith('spending_pie'))
    payload = json.loads(pie.proto.json)
    assert payload['selected'] == '餐饮' and payload['focus_target'] == 'ledger'
    assert payload['focus_id'] == 'kbd-1'
    context = scene_flow.context(at.session_state['session_id'])
    assert context['period'] == '2026-08' and context['category'] == '餐饮'
    assert at.session_state['messages'] == messages
