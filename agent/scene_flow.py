"""UI bridge for validated selection and read-only contextual card activity."""
from agent import card_binding_flow, scene_context, session_memory


def context(session: str) -> dict:
    return scene_context.snapshot(session)


def select_card(session: str, card_id: str) -> dict:
    payload = card_binding_flow.detail(card_id)
    if not payload['ok']:
        return payload
    clear_category(session)
    scene_context.update(session, card_id=card_id, intent='card_query')
    query_scope = scene_context.read_scope(scene_context.snapshot(session))
    period = query_scope.get('period')
    dates = {key: query_scope[key] for key in ('date_from', 'date_to') if query_scope.get(key)}
    if period or dates:
        from agent import classifier, orchestrator
        intent = 'txn_query' if dates else 'bill_analysis'
        slots = dates or {'period': period}
        activity = orchestrator.handle('查看关联账户交易', session_id=session,
                    intent_resolver=lambda *_: classifier.IntentOut(intent=intent, confidence=1, slots=slots))
        if not activity.error_code:
            label = ' 至 '.join(dates.values()) if dates else period
            payload['summary'] = f"{label} 关联账户交易\n\n{activity.reply}\n\n" + payload['summary']
        scene_context.update(session, intent='card_query', card_id=card_id)
    return payload


def select_bill_category(session: str, source: dict, category: str) -> dict:
    """Promote only the current owned chart selection into conversational scope."""
    import uuid
    from data import dao
    from tools import _scene_reads
    state = context(session)
    facts = source.get('facts') or {} if isinstance(source, dict) else {}
    if (not isinstance(source, dict) or source.get('error_code')
            or source.get('intent') not in ('bill_report', 'bill_analysis')
            or state.get('intent') not in ('bill_report', 'bill_analysis', 'txn_query')
            or not source.get('trace_id') or source['trace_id'] != state.get('bill_source_trace_id')
            or facts != state.get('bill_source_facts') or facts.get('period') != state.get('period')
            or scene_context.read_scope(state) != state.get('bill_source_scope')
            or state.get('card_id') != state.get('bill_source_card_id')
            or not isinstance(category, str) or facts.get('group_by', 'category') != 'category'
            or category not in [g.get('key') for g in facts.get('groups', [])]):
        return {'ok': False, 'message': '该账单已不是当前查询，请在当前账单中选择支出分类。'}
    request = {'kind': 'transactions', **state['bill_source_scope'], 'category': category, 'direction': 'out'}
    if state.get('card_id'):
        request['card_id'] = state['card_id']
    result = _scene_reads.read('txn_query', request)
    trace = f'trace-{uuid.uuid4().hex[:12]}'
    dao.insert_audit(trace, session, actor='user', intent='txn_query', tool='scene_read',
                     params_json={'scene': request, 'source_trace_id': source['trace_id']},
                     permission_tier='L0', risk_level='L0', result='success' if result.ok else 'rejected',
                     error_code=result.error_code)
    if result.ok:
        scene_context.remember_result(session, 'txn_query', {}, result, request)
        scene_context.update(session, txn_id=None)
    return {'ok': result.ok, 'message': result.message, 'error_code': result.error_code,
            'trace_id': trace, 'data': result.data, **result.facts}


def clear_category(session: str) -> None:
    state = context(session)
    scene_context.update(session, category=None, txn_id=None, direction=None, counterparty=None,
                         candidates={**state.get('candidates', {}), 'txn': []})


def clear(session: str) -> None:
    session_memory.reset(session)


def select_entity(session: str, domain: str, identifier: str) -> bool:
    state = context(session)
    row = next((r for r in state.get('candidates', {}).get(domain, []) if identifier in
               (r.get('id'), r.get('card_id'), r.get('product_id'), r.get('name'))), None)
    if row is None:
        return False
    key = {'txn': 'txn_id', 'card': 'card_id', 'payee': 'payee', 'subscription': 'sub_id', 'product': 'product'}[domain]
    scene_context.update(session, **{key: identifier})
    return True
