"""Owned, short-lived read-view checkpoints; restoring never replays an operation."""
from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from threading import RLock
from uuid import uuid4

from agent import confirm_card, scene_context
from tools._query_common import current_user_id

_VIEWS = OrderedDict()
_LOCK = RLock()
_READS = {'balance_query', 'txn_query', 'bill_report', 'bill_analysis', 'anomaly_check',
          'card_query', 'payee_list', 'subscription_list', 'subscription_remind',
          'wealth_holdings', 'wealth_all_products', 'wealth_recommend'}


def capture(session: str, *, allow_empty: bool = False) -> str | None:
    state = scene_context.snapshot(session)
    if (state.get('intent') not in _READS and not (allow_empty and not state.get('intent'))) or confirm_card.current(session):
        return None
    state = {key: value for key, value in state.items() if key not in ('pending_id', 'last_write')}
    token = uuid4().hex
    with _LOCK:
        _VIEWS[token] = (current_user_id(), session, monotonic(), deepcopy(state))
        while len(_VIEWS) > 256:
            _VIEWS.popitem(last=False)
    return token


def restore(session: str, token: str) -> bool:
    with _LOCK:
        value = _VIEWS.get(token)
        if (not value or value[:2] != (current_user_id(), session)
                or monotonic() - value[2] > 1800 or confirm_card.current(session)):
            return False
        _VIEWS.pop(token)
        current = scene_context.snapshot(session)
        pending_id = current.get('pending_id')
        pending = confirm_card.pending_of(pending_id, session) if pending_id else None
        scene_context.reset(session)
        scene_context.update(session, **value[3])
        if pending and not pending.revoked and not pending.released:
            scene_context.update(session, pending_id=pending_id)
        return True
