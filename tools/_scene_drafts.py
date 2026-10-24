"""Invalidate superseded draft credentials; never reverses an executed transaction."""
from tools._query_common import current_user_id


def revoke(kind: str, credential: str) -> bool:
    if kind == 'preview_token':
        from tools._transfer_token import _TOKENS_LOCK, _load_token_authoritative, _store_token, _raw_snapshot
        with _TOKENS_LOCK:
            expected = _raw_snapshot(credential)
            row = _load_token_authoritative(credential)
            if not row or row.get('user_id') != current_user_id() or row.get('state') != 'preview':
                return False
            return _store_token(credential, {**row, 'state': 'cancelled'}, expect_json=expected)
    if kind == 'confirm_ref':
        from tools.subscription import _CONFIRM_LOCK, _CONFIRM_REFS
        with _CONFIRM_LOCK:
            row = _CONFIRM_REFS.get(credential)
            if not row or row.get('user_id') != current_user_id():
                return False
            _CONFIRM_REFS.pop(credential, None)
            return True
    return False
