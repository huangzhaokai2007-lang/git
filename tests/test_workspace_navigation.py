"""Visible navigation follows the same validated read context as chat."""
from tests.test_card_binding_ui import app, click


def test_original_bill_and_ledger_presentation(monkeypatch, seeded):
    from agent import llm
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('查八月账单').run()
    assert not at.exception
    assert any(b.label == '📊 按分类分析支出' for b in at.button)
    assert not any(b.label in ('最大的一笔', '返回账单', '修改范围') for b in at.button)
    assert any(e.label == '📊 这份账单的图表' for e in at.expander)
    at.chat_input[0].set_value('最近的交易').run()
    assert not at.exception
    assert any(b.label == '🧾 出一份账单总结' for b in at.button)
    assert not any(b.label == '返回流水' for b in at.button)


def test_back_restores_filters_instead_of_leaking_current_filter(monkeypatch, seeded):
    from agent import llm, orchestrator, scene_context
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    orchestrator.handle('查八月账单', session_id='filter-back')
    orchestrator.handle('只看收入', session_id='filter-back')
    assert scene_context.snapshot('filter-back')['direction'] == 'income'
    result = orchestrator.handle('返回刚才的列表', session_id='filter-back')
    assert not result.error_code
    assert scene_context.snapshot('filter-back').get('direction') is None
