"""F11 理财推荐卡片：只覆盖本次改动，不重写现有推荐测试。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from agent import classifier, llm, orchestrator, templates
from interfaces.web import components, wealth_ui
from tests.test_wealth_recommend_flow import BALANCED
from tools import wealth


def _reply(facts, *, error_code=None, intent='wealth_recommend'):
    return {'trace_id': 'wealth-ui-test', 'intent': intent, 'error_code': error_code,
            'facts': facts, 'states': ['REPLY'], 'tier': 'L0'}


def test_four_cards_below_recommendation_and_buttons_work(seeded: Path):
    wealth.assess_risk(BALANCED)
    result = wealth.recommend_wealth('R3')
    assert result.ok and len(result.facts['items']) == 14
    actions = []

    def page(turn, actions):
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': '理财推荐', 'turn': turn}],
                              wealth_card=lambda current, key: wealth_ui.render_turn(
                                  current, ask=actions.append, key_prefix=key))

    at = AppTest.from_function(page, args=(_reply(result.facts), actions), default_timeout=30).run()
    assert not at.exception
    assert [b.label for b in at.button].count('订购') == 4
    assert [b.label for b in at.button].count('查看全部产品 →') == 1
    assert len(at.columns) >= 2
    next(b for b in at.button if b.label == '查看全部产品 →').click().run()
    assert actions == ['看看全部理财产品']
    next(b for b in at.button if b.label == '订购').click().run()
    assert actions[-1].startswith('买 ')


def test_app_renders_buy_buttons_after_recommendation(monkeypatch, seeded: Path):
    monkeypatch.setenv('DB_PATH', str(seeded))
    monkeypatch.setattr(classifier, 'classify', lambda text, history=None:
                        classifier.IntentOut(intent='wealth_recommend', confidence=1.0,
                                             slots={'risk_level': '高风险'}))
    monkeypatch.setattr(llm, 'chat_json', lambda *args, **kwargs:
                        (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    path = Path(__file__).resolve().parents[1] / 'interfaces/web/app.py'
    at = AppTest.from_file(str(path), default_timeout=60).run()
    assert not at.exception
    at.chat_input[0].set_value('推荐高风险理财').run()
    assert not at.exception
    turn = at.session_state['turns'][-1]['turn']
    assert turn['intent'] == 'wealth_recommend' and not turn['error_code']
    assert len(turn['facts']['items']) == 25
    assert sum(button.label == '订购' for button in at.button) == 4
    assert any(button.label == '查看全部产品 →' for button in at.button)
    assert any('模拟参考区间' in message['text'] for message in at.session_state['messages'])


def test_model_cannot_remove_simulation_warning(monkeypatch, seeded: Path):
    monkeypatch.setattr(templates, 'polish', lambda *_: '保证最高收益')
    result = wealth.recommend_wealth('R3')
    reply, degraded, calls = templates.compose_reply('wealth_recommend', {}, result)
    assert '模拟参考区间' in reply and '非历史极值或收益承诺' in reply
    assert not degraded and calls == 0


def test_view_all_routes_to_catalog_when_llm_offline(monkeypatch, seeded: Path):
    monkeypatch.setattr(llm, 'chat_json', lambda *args, **kwargs:
                        (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    wealth.assess_risk(BALANCED)
    first = orchestrator.handle('推荐理财', session_id='wealth-catalog-ui',
                                intent_resolver=lambda *_: classifier.IntentOut(
                                    intent='wealth_recommend', confidence=1.0, slots={}))
    assert first.intent == 'wealth_recommend' and not first.error_code
    turn = orchestrator.handle('看看全部理财产品', session_id='wealth-catalog-ui')
    assert turn.intent == 'wealth_all_products' and not turn.error_code
    assert turn.facts['total_count'] == 25


def test_buttons_absent_for_failed_or_stale_recommendation(seeded: Path):
    wealth.assess_risk(BALANCED)
    facts = wealth.recommend_wealth('R1').facts

    def page(old, new):
        from interfaces.web import components, wealth_ui
        messages = [{'role': 'assistant', 'text': '推荐', 'turn': old}]
        if new is not None:
            messages.append({'role': 'assistant', 'text': '已切换', 'turn': new})
        components.transcript(messages,
                              wealth_card=lambda turn, key: wealth_ui.render_turn(
                                  turn, ask=lambda _: None, key_prefix=key))

    failed = _reply({}, error_code='INVALID_STATE')
    at = AppTest.from_function(page, args=(failed, None), default_timeout=30).run()
    assert not at.exception
    assert not any(button.label == '订购' for button in at.button)
    later = _reply({}, intent='balance_query')
    at = AppTest.from_function(page, args=(_reply(facts), later), default_timeout=30).run()
    assert not at.exception
    assert not any(button.label in ('订购', '查看全部产品 →') for button in at.button)
