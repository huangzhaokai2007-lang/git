"""L2 self-service reversal of proven temporary locks; generic T12 stays frozen."""
from datetime import datetime, timedelta
import hashlib
import re
import secrets
import sqlite3

from data import card_binding as binding, card_unlock as store, dao
from data._dao_core import connection
from data.db import transaction
from guard import card_credentials, permission
from tools import card_lock
from tools._query_common import _fail, _ok, current_user_id
from tools.schemas import ErrorCode, ToolResult

_now = datetime.now
_REQUESTS: dict[str, dict] = {}


@card_lock._safe
def chat_entry(session_id: str) -> ToolResult:
    result = cards()
    if result.ok:
        trace = f'trace-{secrets.token_hex(6)}'
        with transaction(connection()):
            dao.insert_audit(trace, session_id, actor='user', intent='card_unlock',
                             tool='card_unlock_entry', params_json={}, risk_level='L0',
                             permission_tier='L0', result='success')
        result.facts['trace_id'] = trace
    return result


def _audit(session: str, card_id: str, outcome: str, action: str) -> None:
    dao.insert_audit(secrets.token_hex(12), session, actor='user', intent='card_unlock',
                     tool='self_service_card_unlock', params_json={'card_id': card_id, 'action': action,
                     'status_before': 'locked', 'mode': 'temporary_self_service'},
                     risk_level='L2', permission_tier='L2', result=outcome)


@card_lock._safe
def cards() -> ToolResult:
    result = card_lock.cards()
    if result.ok:
        for item in result.data['items']:
            item['can_unlock'] = store.eligible(connection(), item['card_id'], current_user_id()) is not None
    return result


@card_lock._safe
def preview(card_id: str, session_id: str) -> ToolResult:
    if not isinstance(card_id, str) or not isinstance(session_id, str) or not session_id:
        return _fail(ErrorCode.INVALID_ARGUMENT, '请选择银行卡。')
    if not card_lock.cards().ok:
        return _fail(ErrorCode.INVALID_STATE, '暂时无法查询银行卡。')
    with transaction(connection()):
        row = store.eligible(connection(), card_id, current_user_id())
        if not row:
            return _fail(ErrorCode.INVALID_STATE, '仅本人已绑定且由临时锁卡流程锁定的卡可以自助解锁；挂失或风控冻结请另行处理。')
        token = secrets.token_urlsafe(32)
        for stale in list(_REQUESTS):
            if _REQUESTS[stale]['expires'] < _now():
                del _REQUESTS[stale]
        _REQUESTS[token] = {'user': current_user_id(), 'session': session_id, 'card_id': card_id,
                            'origin': row['lock_trace'], 'expires': _now() + timedelta(seconds=300), 'result': None}
        _audit(session_id, card_id, 'pending_confirm', 'preview')
        data = {'token': token, 'card_id': card_id, 'card_no_mask': row['card_no_mask'],
                'tier': 'L2', 'to_human': False, 'impact': '验证并确认后解除本人临时锁定；余额、授信和欠款不变。'}
        return _ok(data, {key: value for key, value in data.items() if key != 'token'}, '请核对卡片并验证身份后解锁。')


def _request(token: str, session: str) -> dict | None:
    item = _REQUESTS.get(token) if isinstance(token, str) else None
    return item if item and item['user'] == current_user_id() and item['session'] == session and item['expires'] >= _now() else None


def _verified(conn: sqlite3.Connection, item: dict, otp: str, session: str) -> bool:
    key = hashlib.sha256(f'unlock:{current_user_id()}'.encode()).hexdigest()
    attempt = binding.attempt(conn, key)
    if attempt['locked_until'] > _now().timestamp():
        return False
    valid = otp == card_lock.card.OTP_CODE
    if valid:
        binding.record_attempt(conn, key, 0, 0)
    else:
        count = attempt['failures'] + 1 if not attempt['locked_until'] else 1
        deadline = _now().timestamp() + card_credentials.LOCK_SECONDS if count >= card_credentials.MAX_FAILURES else 0
        binding.record_attempt(conn, key, count, deadline)
        _audit(session, item['card_id'], 'rejected', 'verify')
    return valid


@card_lock._safe
def submit(token: str, otp: str, session_id: str) -> ToolResult:
    if not isinstance(otp, str) or not re.fullmatch(r'[0-9]{6}', otp):
        return _fail(ErrorCode.INVALID_ARGUMENT, '请输入六位模拟短信验证码。')
    item = _request(token, session_id)
    if not item:
        return _fail(ErrorCode.FORBIDDEN, '确认已过期或不属于当前会话，请重新选择银行卡。')
    verdict = permission.assess_write('card_temporary_unlock', tier='L2')
    if verdict.to_human or not verdict.requires_otp:
        return _fail(ErrorCode.FORBIDDEN, '自助解锁验证策略不匹配。')
    with transaction(connection()):
        owned = card_lock.store.eligible_card(connection(), item['card_id'], current_user_id())
        if not owned:
            return _fail(ErrorCode.FORBIDDEN, '该卡未绑定或不属于本人。')
        if not _verified(connection(), item, otp, session_id):
            return _fail(ErrorCode.FORBIDDEN, '验证码不正确，或验证尝试已暂时锁定。')
        if item['result'] is not None:
            return item['result']
        result = _execute(item, session_id)
    if result.ok:
        item['result'] = result
    return result


def _execute(item: dict, session: str) -> ToolResult:
    row = store.eligible(connection(), item['card_id'], current_user_id())
    if not row or row['lock_trace'] != item['origin']:
        return _fail(ErrorCode.INVALID_STATE, '卡片状态或锁定申请已变化，请重新选择。')
    updated = dao.update_card(item['card_id'], status='normal')
    _audit(session, item['card_id'], 'success', 'unlock')
    data = {'card_id': item['card_id'], 'card_no_mask': updated['card_no_mask'], 'status': 'normal'}
    return _ok(data, {**data, 'tier': 'L2', 'to_human': False}, f"解锁成功：{data['card_no_mask']} 已恢复正常。")


@card_lock._safe
def cancel(token: str, session_id: str) -> ToolResult:
    item = _request(token, session_id)
    if not item:
        return _fail(ErrorCode.FORBIDDEN, '确认已过期或不属于当前会话。')
    if item['result'] is not None:
        return _fail(ErrorCode.INVALID_STATE, '解锁已经执行，不能撤销。')
    with transaction(connection()):
        _audit(session_id, item['card_id'], 'rejected', 'cancel')
    del _REQUESTS[token]
    return _ok({}, {}, '已取消解锁，卡片状态未改变。')
