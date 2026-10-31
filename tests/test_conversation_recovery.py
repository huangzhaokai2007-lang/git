"""Conversation contracts: bounded understanding, scoped retry, no implicit writes."""
import pytest

from agent import classifier, confirm_card, llm, orchestrator, pending_slots, scene_context
from tools.schemas import ToolResult


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    confirm_card.reset_state()
    pending_slots.clear()
    monkeypatch.setattr(llm, 'chat_json', lambda *_: (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    yield
    confirm_card.reset_state()
    pending_slots.clear()


def verdict(intent, slots=None):
    return lambda *_: classifier.IntentOut(intent=intent, confidence=1, slots=slots or {})


@pytest.mark.parametrize('text', ['帮我瞅瞅还剩多少钱', '我卡里还有多少啊', '查下余额呗'])
def test_colloquial_balance_needs_no_model_or_extra_turn(seeded, text):
    def unavailable(*_):
        pytest.fail('Covered phrase should not need model classification')
    turn = orchestrator.handle(text, session_id=text, intent_resolver=unavailable)
    assert turn.intent == 'balance_query'
    assert turn.tool_calls == ['get_balance']
    assert not turn.ask


def test_transfer_answer_only_asks_remaining_information(seeded):
    sid = 'efficient-transfer'
    orchestrator.handle('我要转账', session_id=sid, intent_resolver=verdict('transfer_single'))
    second = orchestrator.handle('收款人是王五', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert second.missing_slots == ['amount']
    third = orchestrator.handle('金额是100块', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert 'CONFIRM_CARD' in third.states
    assert not third.executed


def test_read_followup_retains_original_intent(seeded):
    sid = 'anomaly-followup'
    first = orchestrator.handle('有没有异常交易', session_id=sid, intent_resolver=verdict('anomaly_check'))
    assert first.ask and not first.tool_calls
    second = orchestrator.handle('2026年8月', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert second.intent == 'anomaly_check'
    assert second.tool_calls == ['detect_anomalies']
    assert not second.ask


def test_read_retry_replays_exact_failed_scope_once(seeded, monkeypatch):
    calls = []
    original = orchestrator.TOOL_ROUTES['txn_query'][1]
    def read(slots):
        calls.append(dict(slots))
        if len(calls) == 1:
            return ToolResult(ok=False, error_code='INTERNAL_ERROR', message='查询暂不可用')
        return original(slots)
    monkeypatch.setitem(orchestrator.TOOL_ROUTES, 'txn_query', ('list_txn', read))
    sid = 'failed-scope'
    slots = {'date_from': '2026-08-01', 'date_to': '2026-08-15', 'category': '电商'}
    first = orchestrator.handle('查询流水', session_id=sid, intent_resolver=verdict('txn_query', slots))
    assert first.error_code
    second = orchestrator.handle('再试一下', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert second.tool_calls == ['list_txn'] and not second.error_code
    assert calls == [slots, slots]
    orchestrator.handle('重试', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert len(calls) == 2  # successful queries are not re-executed by retry


def test_out_of_scope_does_not_match_embedded_greeting(seeded):
    turn = orchestrator.handle('你好，帮我订机票', session_id='unsupported-greeting',
                               intent_resolver=verdict('out_of_scope'))
    assert '帮不上' in turn.reply
    assert not turn.tool_calls and not turn.executed


def test_cancel_clears_unfinished_slot_request(seeded):
    sid = 'cancel-slots'
    orchestrator.handle('我要转账', session_id=sid, intent_resolver=verdict('transfer_single'))
    orchestrator.handle('算了', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert pending_slots.peek(sid) is None


def test_no_retry_of_previous_read_after_starting_write(seeded):
    sid = 'read-then-write'
    scene_context.update(sid, failed_read={'intent': 'balance_query', 'slots': {}, 'read_request': None})
    orchestrator.handle('我要转账', session_id=sid, intent_resolver=verdict('transfer_single'))
    turn = orchestrator.handle('重试', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert not turn.tool_calls and not turn.executed


def test_progress_does_not_exhaust_clarification_budget(seeded):
    sid = 'progress-budget'
    orchestrator.handle('我要转账', session_id=sid, intent_resolver=verdict('transfer_single'))
    turn = orchestrator.handle('收款人是王五', session_id=sid, clarify_round=2,
                               intent_resolver=verdict('smalltalk'))
    assert turn.missing_slots == ['amount'] and not turn.to_human


def test_one_sentence_colloquial_transfer_only_previews(seeded):
    turn = orchestrator.handle('帮我给王五打100块呗', session_id='one-shot',
                               intent_resolver=verdict('smalltalk'))
    assert turn.intent == 'transfer_single' and 'CONFIRM_CARD' in turn.states
    assert not turn.executed and not turn.missing_slots


def test_read_exception_can_retry_without_leaking_internal_detail(seeded, monkeypatch):
    original = orchestrator.TOOL_ROUTES['balance_query'][1]
    def broken(_slots):
        raise RuntimeError('private connection details')
    monkeypatch.setitem(orchestrator.TOOL_ROUTES, 'balance_query', ('get_balance', broken))
    turn = orchestrator.handle('查余额', session_id='exception-retry', intent_resolver=verdict('balance_query'))
    assert turn.error_code == 'SERVICE_UNAVAILABLE' and '重试' in turn.reply
    assert 'private' not in turn.reply
    monkeypatch.setitem(orchestrator.TOOL_ROUTES, 'balance_query', ('get_balance', original))
    turn = orchestrator.handle('重试', session_id='exception-retry', intent_resolver=verdict('smalltalk'))
    assert turn.tool_calls == ['get_balance'] and not turn.error_code


def test_unfinished_query_can_take_a_relative_month(seeded):
    sid = 'txn-month'
    orchestrator.handle('查流水', session_id=sid, intent_resolver=verdict('txn_query'))
    turn = orchestrator.handle('上个月', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert turn.intent == 'txn_query' and not turn.ask


def test_ambiguous_amount_is_not_guessed(seeded):
    sid = 'ambiguous-amount'
    orchestrator.handle('给王五转账', session_id=sid,
                        intent_resolver=verdict('transfer_single', {'payee': '王五'}))
    turn = orchestrator.handle('100或者200吧', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert not turn.executed and 'CONFIRM_CARD' not in turn.states


def test_out_of_scope_preserves_query_choices_without_fake_execution(seeded):
    turn = orchestrator.handle('帮我订机票', session_id='unsupported',
                               intent_resolver=verdict('out_of_scope'))
    assert '银行业务' in turn.reply and not turn.executed and not turn.tool_calls


@pytest.mark.parametrize('month', ['八月', '2026年8月', '上个月'])
def test_short_month_answer_keeps_anomaly_intent(seeded, month):
    sid = 'anomaly-' + month
    orchestrator.handle('查异常', session_id=sid, intent_resolver=verdict('anomaly_check'))
    turn = orchestrator.handle(month, session_id=sid, intent_resolver=verdict('smalltalk'))
    assert turn.intent == 'anomaly_check' and turn.tool_calls == ['detect_anomalies']


def test_retry_during_transfer_confirmation_does_not_execute(seeded):
    sid = 'pending-confirm-retry'
    orchestrator.handle('帮我给王五打100块呗', session_id=sid,
                        intent_resolver=verdict('smalltalk'))
    turn = orchestrator.handle('重试', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert not turn.executed
    assert 'execute_transfer' not in turn.tool_calls


def test_failed_read_retry_is_session_scoped(seeded):
    scene_context.update('other-session', failed_read={'intent': 'balance_query', 'slots': {}})
    turn = orchestrator.handle('重试', session_id='unrelated-session',
                               intent_resolver=verdict('smalltalk'))
    assert not turn.tool_calls


@pytest.mark.parametrize('text', ['取消', '不办了', '先不办了'])
def test_colloquial_cancel_closes_confirmation_without_execution(seeded, text):
    sid = 'cancel-confirm-' + text
    orchestrator.handle('帮我给王五打100块呗', session_id=sid,
                        intent_resolver=verdict('smalltalk'))
    assert confirm_card.current(sid) is not None
    turn = orchestrator.handle(text, session_id=sid, intent_resolver=verdict('smalltalk'))
    assert confirm_card.current(sid) is None
    assert not turn.executed and '尚未执行' in turn.reply


def test_end_date_answer_preserves_already_supplied_start(seeded, monkeypatch):
    calls = []
    original = orchestrator.TOOL_ROUTES['txn_query'][1]
    def read(slots):
        calls.append(dict(slots))
        return original(slots)
    monkeypatch.setitem(orchestrator.TOOL_ROUTES, 'txn_query', ('list_txn', read))
    sid = 'partial-dates'
    first = orchestrator.handle('查8月1日起的流水', session_id=sid,
                                intent_resolver=verdict('txn_query', {'date_from': '2026-08-01'}))
    assert first.missing_slots == ['date_to']
    turn = orchestrator.handle('2026年8月15日', session_id=sid, intent_resolver=verdict('smalltalk'))
    assert not turn.error_code and not turn.ask
    assert calls[0]['date_from'] == '2026-08-01' and calls[0]['date_to'] == '2026-08-15'
