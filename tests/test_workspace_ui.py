"""One current workspace; history never owns live business widgets."""
import pytest
from agent import confirm_card, orchestrator
from tests.test_card_binding_ui import app, bind, click, CARD


def _is_pick(widget) -> bool:
    """选卡按钮：新版是「选择这张卡」，收款人卡列表里的是「银行名 + 6222 掩码」。"""
    label = str(getattr(widget, "label", ""))
    return "6222" in label or "选择这张卡" in label

def test_same_card_shortcut_keeps_existing_detail_and_history(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    before = list(at.session_state['messages'])
    click(at, '管卡')
    assert list(at.session_state['messages']) == before
    assert at.session_state['cards-view']['selected'] == CARD
    assert sum(button.label == '关闭详情' for button in at.button) == 1


def test_switch_from_card_details_to_transfer_has_single_business_view(monkeypatch, seeded):
    from agent import classifier
    monkeypatch.setattr(classifier, 'classify', lambda *a, **kw: classifier.IntentOut(
        intent='transfer_single', confidence=1, slots={'payee': '王五', 'amount': 100}))
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    click(at, '临时锁卡')
    at.chat_input[0].set_value('给王五转100元').run()
    _cards = [button for button in at.button if _is_pick(button)]
    if _cards:                      # 2026-10-31：转账先出「选择银行卡」卡面
        _cards[0].click().run()
    assert not at.exception
    assert not any(button.label in ('关闭详情', '查看完整卡号', '确认锁卡') for button in at.button)
    assert not any('我的银行卡' in item.value for item in at.markdown)
    current = confirm_card.current(at.session_state['session_id'])
    assert current and current.intent == 'transfer_single'
    credential = current.credential
    before = list(at.session_state['messages'])
    at.run()
    assert list(at.session_state['messages']) == before
    assert confirm_card.current(at.session_state['session_id']).credential == credential


@pytest.mark.parametrize('raises', [False, True])
def test_failed_read_keeps_previous_bill_and_retries_in_place(monkeypatch, seeded, raises):
    from agent import card_binding_flow
    responses = [orchestrator.Turn(trace_id='bill-one', intent='bill_analysis',
                 reply='2026-08 共支出 5.00 元', facts={'period':'2026-08', 'total_yuan':'5.00'}),
                 orchestrator.Turn(trace_id='bill-error', intent='bill_analysis',
                 reply='暂时读取失败', error_code='INTERNAL_ERROR'),
                 orchestrator.Turn(trace_id='bill-two', intent='bill_analysis', reply='2026-09 共支出 8.00 元',
                                  facts={'period':'2026-09', 'total_yuan':'8.00'})]
    def respond(*args, **kwargs):
        response = responses.pop(0)
        if raises and response.error_code:
            raise RuntimeError('offline')
        return response
    monkeypatch.setattr(card_binding_flow, 'handle', respond)
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('八月账单').run()
    at.chat_input[0].set_value('九月呢').run()
    assert at.session_state['workspace-result']['turn']['trace_id'] == 'bill-one'
    assert len(at.error) == 1
    if raises:
        assert not any(button.label == '重试查询' for button in at.button)
        at.chat_input[0].set_value('九月呢').run()
    else:
        click(at, '重试查询')
    assert at.session_state['workspace-result']['turn']['trace_id'] == 'bill-two'
    assert not at.error


def test_new_query_replaces_operation_records(monkeypatch, seeded):
    from agent import card_binding_flow
    monkeypatch.setattr(card_binding_flow, 'handle', lambda *a, **kw:
        orchestrator.Turn(trace_id='after-records', intent='bill_analysis', reply='八月账单',
                          facts={'period': '2026-08', 'total_yuan': '5.00'}))
    at = app(monkeypatch, seeded)
    click(at, '操作记录')
    at.chat_input[0].set_value('八月账单').run()
    assert not at.session_state['operation-records-open']
    assert any(button.label == '📄 看流水明细' for button in at.button)


def test_unknown_financial_exception_never_offers_read_retry(monkeypatch, seeded):
    from agent import card_binding_flow
    at = app(monkeypatch, seeded)
    at.session_state['workspace-result'] = {'turn': {'intent': 'bill_analysis'}, 'scene': 'bills'}
    def fail(*args, **kwargs):
        raise RuntimeError('unknown execution outcome')
    monkeypatch.setattr(card_binding_flow, 'handle', fail)
    at.chat_input[0].set_value('给王五转100元').run()
    assert at.error
    assert not any(button.label == '重试查询' for button in at.button)


def test_transaction_workspace_shows_readable_fields_without_internal_ids(monkeypatch, seeded):
    from agent import card_binding_flow, scene_context
    turn = orchestrator.Turn(trace_id='ledger-result', intent='txn_query',
        reply='当前范围共 1 笔交易。\n| 时间 | 交易对手 | 金额 |\n|---|---|---|\n| 2026-08-10 | 合成商户 | -5.00 |',
        facts={'items': [{'id': 'txn-internal-001', 'ts': '2026-08-10T12:00:00',
                         'counterparty': '合成商户', 'category': '电商', 'amount_yuan': '-5.00'}]})
    monkeypatch.setattr(card_binding_flow, 'handle', lambda *a, **kw: turn)
    at = app(monkeypatch, seeded)
    scene_context.update(at.session_state['session_id'], intent='txn_query', txn_id='txn-internal-001')
    at.chat_input[0].set_value('看看这笔交易').run()
    assert not at.exception
    assert not any(button.label == '关联银行卡' for button in at.button)
    assert not any('txn-internal-001' in item.value for item in at.markdown)
    ledger = [item.value for item in at.markdown if 'class="txn-wrap"' in item.value]
    assert len(ledger) == 1
    assert '合成商户' in ledger[0] and '-5.00' in ledger[0]
