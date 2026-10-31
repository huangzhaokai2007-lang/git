"""Contextual understanding and safe draft navigation around the existing state machine."""
import re
import logging
from collections.abc import Callable

from agent import classifier, confirm_card, scene_context, scene_language, write_flow
from tools import _scene_reads
from tools.schemas import ToolResult


def call_read(call: Callable, slots: dict) -> ToolResult:
    """Keep unexpected read failures recoverable; never wrap or replay write calls."""
    try:
        return call(slots)
    except Exception:
        logging.getLogger(__name__).exception('Read query failed')
        return ToolResult(ok=False, error_code='SERVICE_UNAVAILABLE',
                          message='查询服务暂时不可用，请稍后重试。')


def plan(ctx, text: str):
    state = scene_context.snapshot(ctx.session_id)
    decision = scene_language.plan(text, state)
    ctx.scene_plan = decision or {}
    ctx.scene_request = (decision or {}).get('read_request')
    if ctx.scene_request:
        inherited = ('period', 'category', 'direction') if ctx.scene_request.get('all_cards') else ('period', 'card_id', 'category', 'direction')
        if ctx.scene_request.get('period') and ctx.scene_request['period'] != state.get('period'):
            inherited = tuple(k for k in inherited if k not in ('category', 'direction'))
        ctx.scene_request = {**{k: state[k] for k in inherited if state.get(k)},
                             **ctx.scene_request}
        if ctx.scene_request.get('period') is None and not (ctx.scene_request.get('date_from') and ctx.scene_request.get('date_to')):
            from agent.period import anchor_period
            ctx.scene_request['period'] = state.get('period') or anchor_period()
    if decision:
        return classifier.IntentOut(intent=decision['intent'], confidence=1, slots=decision['slots'])
    return None


def enrich(ctx, text: str) -> None:
    state = scene_context.snapshot(ctx.session_id)
    if ctx.intent == 'card_query' and state.get('period') and not ctx.scene_request:
        ctx.scene_request = {'kind': 'card_bills', 'period': state['period']}
    if ctx.intent in ('txn_query', 'bill_analysis', 'bill_report') and state.get('card_id') and not ctx.scene_request:
        ctx.scene_request = {'kind': 'transactions', 'card_id': state['card_id'],
                             'period': ctx.slots.get('period') or state.get('period'),
                             **{k: ctx.slots[k] for k in ('date_from', 'date_to', 'category', 'min_amount') if ctx.slots.get(k) is not None}}
        if ctx.slots.get('date_from') or ctx.slots.get('date_to'):
            ctx.scene_request.pop('period', None)
    if ctx.intent in ('bill_analysis', 'bill_report', 'anomaly_check') and 'period' not in ctx.slots and state.get('period'):
        ctx.slots['period'] = state['period']


def response(ctx):
    decision = getattr(ctx, 'scene_plan', {})
    action = decision.get('action')
    pending_id = scene_context.snapshot(ctx.session_id).get('pending_id')
    pending = confirm_card.pending_of(pending_id, ctx.session_id) if pending_id else None
    if pending and not pending.revoked and not pending.released and action in ('amend', 'new_draft', 'reset', 'cancel'):
        return write_flow.Step(intent=ctx.intent, states=['PENDING_REVIEW'], tier='L3', pending_id=pending_id,
                               reply='已有待复核申请。请先使用撤销入口处理原申请，再重新办理；本次不会新建或执行另一笔业务。', error_code='INVALID_STATE')
    if action == 'status':
        last = scene_context.snapshot(ctx.session_id).get('last_write')
        text = '当前会话没有可核对的办理回执，请选择具体交易或申请记录。' if not last else '最近一次办理回执：\n\n' + last['reply']
        return write_flow.Step(intent=ctx.intent, states=['PRECHECK'], reply=text)
    if action == 'loss_records':
        return write_flow.Step(intent='card_report_lost', states=['PRECHECK'], reply='请查看已提交的挂失补卡申请记录及领卡资料；未提交草稿不代表已受理。', ask='card_lost_records')
    if decision.get('ask') and not decision.get('read_request'):
        return write_flow.Step(intent=ctx.intent, states=['CLARIFY'], reply=decision['ask'], ask=decision['ask'])
    if action in ('cancel', 'reset'):
        from agent import pending_slots
        pending_slots.clear(ctx.session_id)
        scene_context.reset(ctx.session_id)
        return write_flow.Step(intent='smalltalk', states=['PRECHECK'], reply='已关闭当前草稿；已经提交的业务不受影响。', ask='scene_reset')
    if action == 'retry':
        failed = scene_context.snapshot(ctx.session_id).get('failed_read')
        if failed:
            ctx.intent, ctx.slots = failed['intent'], dict(failed['slots'])
            ctx.scene_request = failed.get('read_request')
            return None
        return write_flow.Step(intent=ctx.intent, states=['CLARIFY'], reply='请先核对上次办理结果；如需重新办理，请明确说明业务和对象，我会重新生成确认。', ask='请明确要重新办理的业务。')
    if action == 'back':
        state = scene_context.snapshot(ctx.session_id)
        views = state.get('views', [])
        previous = views[-1] if views else state.get('previous', {})
        if previous.get('intent') in ('txn_query', 'bill_report', 'bill_analysis', 'card_query', 'subscription_list', 'payee_list', 'wealth_holdings'):
            ctx.intent, ctx.slots = previous['intent'], previous.get('slots', {})
            ctx.scene_request = previous.get('read_request') or None
            scene_context.update(ctx.session_id, **{'card_id': None, 'txn_id': None, 'category': None,
                                 'direction': None, 'counterparty': None, **previous, 'views': views[:-1]})
            return None
        return write_flow.Step(intent=ctx.intent, states=['CLARIFY'], reply='暂无可恢复的查询列表，请说明要查看的内容。')
    return None


def amend_inflight(text: str, session: str):
    current = confirm_card.current(session)
    if current is None:
        return None
    state = scene_context.snapshot(session)
    state.update(intent=current.intent, slots=state.get('last_write', {}).get('slots', state.get('slots', {})))
    decision = scene_language.plan(text, state)
    if decision and decision.get('action') == 'amend':
        from tools._scene_drafts import revoke
        if not revoke(current.credential_kind, current.credential):
            return write_flow.Step(intent=current.intent, states=['PRECHECK'], reply='原草稿已失效或不可修改，请重新发起办理。', error_code='INVALID_STATE')
        confirm_card.clear(session)
        step = write_flow.start(decision['intent'], decision['slots'], session)
        scene_context.remember_write(session, decision['intent'], decision['slots'], status=step.result, reply=step.reply)
        return step
    if decision and decision.get('action') in ('cancel', 'reset'):
        from tools._scene_drafts import revoke
        revoke(current.credential_kind, current.credential)
        confirm_card.clear(session)
        scene_context.reset(session)
        return write_flow.Step(intent='smalltalk', states=['PRECHECK'], reply='已取消待确认草稿，尚未执行。', ask='scene_reset')
    return None


def route(ctx):
    request = getattr(ctx, 'scene_request', None)
    if not request:
        return None
    return 'scene_read', lambda _slots: _scene_reads.read(ctx.intent, request)


def remember(ctx, status: str, reply: str) -> None:
    result = getattr(ctx, 'read_result', None)
    from agent.orchestrator import READ_INTENTS
    failed = None
    if status == 'error' and ctx.intent in READ_INTENTS and ctx.tool_calls:
        failed = {'intent': ctx.intent, 'slots': dict(ctx.slots),
                  'read_request': getattr(ctx, 'scene_request', None)}
    if failed or status == 'success' or ctx.intent in write_flow.WRITE_INTENTS:
        scene_context.update(ctx.session_id, failed_read=failed)
    if status == 'success' and result is not None:
        views = scene_context.snapshot(ctx.session_id).get('views', [])
        scene_context.remember_result(ctx.session_id, ctx.intent, ctx.slots, result, getattr(ctx, 'scene_request', None))
        if ctx.intent in ('bill_report', 'bill_analysis') and 'groups' in result.facts:
            scene_context.update(ctx.session_id, bill_source_trace_id=ctx.trace_id)
        if getattr(ctx, 'scene_plan', {}).get('action') == 'back':
            scene_context.update(ctx.session_id, views=views)
    if ctx.intent in write_flow.WRITE_INTENTS and (ctx.executed or status == 'pending_confirm'):
        slots = ctx.slots or scene_context.snapshot(ctx.session_id).get('last_write', {}).get('slots', {})
        scene_context.remember_write(ctx.session_id, ctx.intent, slots, status=status, reply=reply)
        scene_context.update(ctx.session_id, intent=ctx.intent, slots=slots)
        if ctx.pending_id:
            scene_context.update(ctx.session_id, pending_id=ctx.pending_id)
