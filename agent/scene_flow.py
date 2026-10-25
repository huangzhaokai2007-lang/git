"""UI bridge for validated selection and read-only contextual card activity."""
from agent import card_binding_flow, scene_context, session_memory


def context(session: str) -> dict:
    return scene_context.snapshot(session)


def select_card(session: str, card_id: str) -> dict:
    payload = card_binding_flow.detail(card_id)
    if not payload['ok']:
        return payload
    scene_context.update(session, card_id=card_id, intent='card_query')
    period = scene_context.snapshot(session).get('period')
    if period:
        from agent import classifier, orchestrator
        activity = orchestrator.handle('查看关联账户交易', session_id=session,
                    intent_resolver=lambda *_: classifier.IntentOut(intent='bill_analysis', confidence=1, slots={'period': period}))
        if not activity.error_code:
            payload['summary'] = f"{period} 关联账户交易\n\n{activity.reply}\n\n" + payload['summary']
        scene_context.update(session, intent='card_query', card_id=card_id)
    return payload


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
