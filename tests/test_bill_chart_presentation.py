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
    """账单总结只留「正文分类明细」：不再画「支出 / 收入」对比柱图，也不再出那张指标卡。

    2026-10-29：先按 Comment 5 删掉卡片里那张只有两根柱子的「支出 / 收入」对比图；
    随后按 Comment 9（「看账单里的异常提醒下方的收入支出以及柱状图…这一整块都删了」）
    把那张洞察卡**整块**删掉 —— 三格指标、接着问、导出都不再出现。新契约钉在**页面**上：

    1. 三格指标（支出合计 / 收入合计 / 净额）**不再上屏**；
    2. **账单总结这张卡自己不再画图**：问完后页面只多出账单面板那张「月度趋势」折线
       （单账期一个点），全页找不到一根把「支出 / 收入」当横坐标的柱子；
    3. 分类明细仍只在正文里以动画饼图出现一次（原表格被它替换），不重复成 `st.dataframe`，
       而回执原文照旧保留（审计要能回看原始文本）。
    """
    at = _app(monkeypatch)
    #: Comment 8 起的口径：账单场景先选时间段才展示正文 —— 预置 = 「用户已经选过 2026-08」。
    at.session_state['bill-range'] = ('2026-08-01', '2026-08-31')
    baseline = len(at.get('vega_lite_chart'))        # 还没问之前页面上的图，用来量「新增了几张」
    at.chat_input[0].set_value('上个月的账单总结').run()
    assert not at.exception
    page = _text(at)
    assert '| 项目 | 金额（元） | 占比 |' not in page
    assert '总览' in page and '订阅（进行中）' in page and '异常提示' in page
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1
    payload = json.loads(pies[0].proto.json)
    turn = at.session_state['messages'][-1]['turn']
    facts = turn['facts']
    assert turn['intent'] == 'bill_report'
    assert payload['groups'] == facts['groups']
    assert '| 项目 | 金额（元） | 占比 |' in turn['reply']  # Audit/original reply remains intact.
    assert not at.dataframe                                # 分类明细只以饼图出现，不再重复一张表
    # ① Comment 9：报告那张洞察卡整块删了 —— 页面上不该再有 支出合计/收入合计/净额 三格指标
    metrics = {str(metric.label): str(metric.value) for metric in at.metric}
    assert '支出合计（元）' not in metrics and '收入合计（元）' not in metrics
    assert '净额（元）' not in metrics
    # 独立趋势面板已移除，报告中的收支饼图仍由上面的事实包断言覆盖。
    assert len(at.get('vega_lite_chart')) == baseline
    assert not any('这份账单的图表' in element.label for element in at.expander)


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
    #: Comment 8 起的口径：账单场景先选时间段才展示正文 —— 预置 = 「用户已经选过 2026-08」，
    #: 否则正文（含那张饼图）压根不上屏，选中态也就无处落。
    at.session_state['bill-range'] = ('2026-08-01', '2026-08-31')
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
