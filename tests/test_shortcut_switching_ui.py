"""Shortcuts start a fresh workspace without carrying old panels or write drafts."""
import pytest

from agent import classifier, confirm_card, llm, scene_context
from tests.conftest import raw
from tests.test_card_binding_ui import CARD, app, bind, click


def _is_pick(widget) -> bool:
    """选卡按钮：新版是「选择这张卡」，收款人卡列表里的是「银行名 + 6222 掩码」。"""
    label = str(getattr(widget, "label", ""))
    return "6222" in label or "选择这张卡" in label
BILL_RANGE = "bill-range"


def offline(monkeypatch):
    routes = {'我买了哪些理财': ('wealth_holdings', {}),
              '我有哪些订阅': ('subscription_list', {}),
              '推荐理财产品': ('wealth_recommend', {}),
              '我的收款人有哪些': ('payee_list', {}),
              '给王五转 100 元': ('transfer_single', {'payee': '王五', 'amount': '100'}),
              # 账单场景 2026-10-29 起先选时间段再出报告，界面发的是「YYYY年M月的账单报告」。
              '的账单报告': ('bill_report', {'period': '2026-08', 'kind': 'monthly'}),
              '出一份上个月的账单报告': ('bill_report', {'period': '2026-08', 'kind': 'monthly'})}
    def resolve(text, history=None):
        intent, slots = next((route for prompt, route in routes.items() if prompt in text), ('out_of_scope', {}))
        return classifier.IntentOut(intent=intent, confidence=1, slots=slots)
    monkeypatch.setattr(classifier, 'classify', resolve)
    monkeypatch.setattr(llm, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))


def pies(at):
    return [item for item in at.get('bidi_component')
            if item.proto.component_name.endswith('spending_pie')]


def click_shortcut(at, label):
    """侧边栏快捷场景按钮（key=`scene-<index>`）。

    2026-10-29：管卡页内 / 卡详情里也各有一个 label 叫「调整额度」的按钮，
    按 label 找按钮会点到页内入口（只开面板、不切场景），所以入口必须按 key 钉住。
    """
    next(button for button in at.button
         if (button.key or '').startswith('scene-') and button.label == label).click().run()
    assert not at.exception


def pick_bill_range(at, start="2026-08-01", end="2026-08-31"):
    """账单场景的新前提：先选时间段再点「生成账单报告」，报告正文/图表才会出现。

    直接预置 `bill-range` 表示「用户已经在时间段小框里选过一轮」——否则页面只显示
    时间段选择区块，报告正文被有意藏起来（legacy_bill_ui 2026-10-29）。
    """
    at.session_state[BILL_RANGE] = (start, end)


@pytest.mark.parametrize(('label', 'intent'), [
    ('我的理财', 'wealth_holdings'), ('管订阅', 'subscription_list'),
    ('推荐理财', 'wealth_recommend'), ('收款人', 'payee_list'),
    ('看账单', 'bill_report'),
    ('调整额度', 'card_limit_adjust'), ('挂失补卡', 'card_report_lost')])
def test_shortcut_closes_card_workspace_and_opens_selected_scene(monkeypatch, seeded, label, intent):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click_shortcut(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    messages = list(at.session_state['messages'])

    click_shortcut(at, label)

    assert not at.session_state['bound-card-query']
    assert not any(button.key == f'detail-{CARD}' for button in at.button)
    assert not any(button.label in ('绑定银行卡', '关闭详情') for button in at.button)
    assert at.session_state['messages'][:len(messages)] == messages
    if label == '看账单':
        assert at.session_state['workspace-result']['turn']['intent'] == intent
        assert any(button.key == 'bill-range-go' for button in at.button)
        assert not pies(at)
        return
    assert at.session_state['messages'][-1]['turn']['intent'] == intent
    if label == '推荐理财':
        assert at.session_state['messages'][-1]['turn']['error_code'] == 'INVALID_STATE'
        assert '风险测评' in at.session_state['messages'][-1]['text']
    else:
        assert not at.session_state['messages'][-1]['turn']['error_code']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == 'normal'
    if label == '挂失补卡':
        assert any(button.key == f'lost-card-{CARD}' or button.label == '确认挂失并补卡' for button in at.button)


def test_bill_workspace_does_not_follow_another_shortcut(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    pick_bill_range(at)
    click_shortcut(at, '看账单')
    assert not pies(at)
    click(at, '生成账单报告')
    assert len(pies(at)) == 1
    # 时间段选择区块照样在（报告是按已选区间渲染的），只是不再重问一遍。
    assert any(button.key == 'bill-range-go' for button in at.button)
    assert not any('选好时间段后' in item.value for item in at.caption)
    messages = list(at.session_state['messages'])
    click_shortcut(at, '管卡')
    assert any('我的银行卡' in item.value for item in at.markdown)
    assert not pies(at)
    click_shortcut(at, '我的理财')
    assert not any(button.label == '绑定银行卡' for button in at.button)
    assert not pies(at)
    assert at.session_state['messages'][:len(messages)] == messages


def test_session_reset_restores_a_fresh_chat_workspace(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click_shortcut(at, '管卡')
    click_shortcut(at, '我的理财')
    click(at, '重置会话')
    at.chat_input[0].set_value('出一份上个月的账单报告').run()
    assert not at.exception
    # 新行为：报告先不露面，只给时间段选择；点「生成账单报告」后正文与图表才出现。
    assert not pies(at)
    assert any('选好时间段后' in item.value for item in at.caption)
    click(at, '生成账单报告')
    assert not at.exception
    # Comment 10：收支明细并排两张饼（左支出、右收入），不再是单张支出饼。
    assert len(pies(at)) == 1


def test_switching_from_transfer_to_holdings_cancels_only_unsubmitted_draft(monkeypatch, seeded):
    from tools import transfer

    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    session = at.session_state['session_id']
    before = raw(seeded, 'SELECT id,balance FROM account ORDER BY id')
    at.chat_input[0].set_value('给王五转 100 元').run()
    _cards = [button for button in at.button if _is_pick(button)]
    if _cards:                      # 2026-10-31：转账先出「选择银行卡」卡面
        _cards[0].click().run()
    _cards = [button for button in at.button if _is_pick(button)]
    if _cards:                      # 2026-10-31：转账先出「选择银行卡」卡面
        _cards[0].click().run()
    draft = confirm_card.current(session)
    assert draft and draft.intent == 'transfer_single', at.session_state['messages'][-1]

    click_shortcut(at, '我的理财')

    assert confirm_card.current(session) is None
    assert at.session_state['messages'][-1]['turn']['intent'] == 'wealth_holdings'
    assert '需要再确认' not in at.session_state['messages'][-1]['text']
    transfer.set_session_id(session)
    assert not transfer.execute_transfer(draft.credential, '123456').ok
    assert raw(seeded, 'SELECT id,balance FROM account ORDER BY id') == before


def test_switching_away_cancels_lock_preview_and_clears_selected_card_context(monkeypatch, seeded):
    from agent import card_lock_flow

    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    click_shortcut(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    next(button for button in at.button if button.key == f'lock-toggle-{CARD}').click().run()
    token = at.session_state['lock-view']['preview']['token']
    session = at.session_state['session_id']

    click_shortcut(at, '我的理财')

    assert not any(item.key == f'lock-inline-otp-{CARD}' for item in at.text_input)
    assert not scene_context.snapshot(session).get('card_id')
    assert not card_lock_flow.submit(token, '123456', session)['ok']
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == 'normal'


def test_transfer_shortcut_is_removed(monkeypatch, seeded):
    offline(monkeypatch)
    at = app(monkeypatch, seeded)
    assert not any('转账' == button.label for button in at.sidebar.button)
    assert any(button.label == '收款人' for button in at.sidebar.button)
