from tests.test_card_binding_ui import app, click, bind, CARD


def test_chat_bill_then_card_selection_retains_month(monkeypatch, seeded):
    from agent import llm, scene_flow
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    at.chat_input[0].set_value('查八月账单').run()
    assert not at.exception
    at.chat_input[0].set_value('看看卡片信息').run()
    assert not at.exception
    assert '关联账户' in at.session_state['messages'][-1]['text']
    mask = next(button for button in at.button if button.key == f'detail-{CARD}')
    mask.click().run()
    assert not at.exception
    assert at.session_state['cards-view']['selected'] == CARD
    assert '2026-08 关联账户交易' in at.session_state['messages'][-1]['text']
    assert not any(button.label in ('返回上次查询', '清除查询条件') for button in at.button)
    at.chat_input[0].set_value('重新来').run()
    assert not at.session_state['bound-card-query']
