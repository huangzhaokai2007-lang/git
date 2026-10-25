"""Owner/session-scoped conversational selection; never stores credentials or free-form memos."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from time import monotonic

from tools._query_common import current_user_id

_STORE: OrderedDict = OrderedDict()
_LOCK = RLock()
_TTL = 1800
_CAP = 256
_SAFE = {'id', 'payee_id', 'masked_phone', 'card_id', 'card_no_mask', 'type', 'status', 'name', 'phone', 'bank',
         'merchant', 'sub_id', 'product_id', 'product_name', 'counterparty', 'category',
         'ts', 'amount', 'amount_yuan', 'direction', 'cycle', 'next_charge_date'}
_KINDS = {'card_query': 'card', 'txn_query': 'txn', 'payee_list': 'payee',
          'subscription_list': 'subscription', 'wealth_holdings': 'product',
          'wealth_all_products': 'product', 'wealth_recommend': 'product'}


def _key(session: str) -> tuple:
    return current_user_id(), str(session)


def snapshot(session: str) -> dict:
    with _LOCK:
        entry = _STORE.get(_key(session))
        if not entry or monotonic() - entry[0] > _TTL:
            _STORE.pop(_key(session), None)
            return {'candidates': {}}
        return deepcopy(entry[1])


def update(session: str, **values) -> dict:
    with _LOCK:
        state = snapshot(session)
        state.update(deepcopy(values))
        _STORE[_key(session)] = monotonic(), state
        _STORE.move_to_end(_key(session))
        while len(_STORE) > _CAP:
            _STORE.popitem(last=False)
        return deepcopy(state)


def remember_result(session: str, intent: str, slots: dict, result, request: dict | None = None) -> None:
    if not result.ok:
        return
    request, data = request or {}, result.data or {}
    state = snapshot(session)
    previous = {k: state[k] for k in ('intent', 'period', 'card_id', 'txn_id', 'slots', 'read_request') if k in state}
    values = {'intent': intent, 'slots': dict(slots), 'previous': previous, 'read_request': dict(request)}
    if previous:
        values['views'] = (state.get('views', []) + [previous])[-8:]
    if intent not in ('card_query', 'txn_query', 'bill_report', 'bill_analysis', 'anomaly_check'):
        values.update(card_id=None, txn_id=None, category=None, direction=None, counterparty=None)
    period = request.get('period') or slots.get('period')
    if request.get('date_from') and request.get('date_to'):
        period = request['date_from'][:7] if request['date_from'][:7] == request['date_to'][:7] else None
        values['period'] = period
    if period:
        values['period'] = period
    if request.get('all_cards'):
        values['card_id'] = None
    for field in ('card_id', 'txn_id', 'sub_id', 'merchant', 'product', 'payee', 'counterparty', 'direction', 'category'):
        if request.get(field):
            values[field] = request[field]
    kind = _KINDS.get(intent)
    rows = data.get('items', data.get('cards', data.get('products', data.get('holdings', []))))
    if kind == 'card' and data.get('cards') is not None:
        rows = data['cards']
    if kind and isinstance(rows, list) and any(k in data for k in ('items', 'cards', 'holdings', 'products')):
        candidates = state.get('candidates', {})
        candidates[kind] = [{k: v for k, v in item.items() if k in _SAFE} for item in rows if isinstance(item, dict)]
        values['candidates'] = candidates
    update(session, **values)


def remember_write(session: str, intent: str, slots: dict, *, status: str, reply: str) -> None:
    safe = {k: v for k, v in slots.items() if k in
            ('payee', 'amount', 'schedule', 'sub_id', 'merchant', 'product', 'card_id')}
    update(session, last_write={'intent': intent, 'slots': safe, 'status': status, 'reply': reply})


def remember_tool_write(session: str, intent: str, result) -> dict:
    data = result.data or {}
    if result.ok:
        remember_write(session, intent, {}, status=data.get('status', 'success'), reply=result.message)
        update(session, intent=intent)
    return result.model_dump()


def reset(session: str | None = None) -> None:
    with _LOCK:
        if session is None:
            _STORE.clear()
        else:
            _STORE.pop(_key(session), None)
