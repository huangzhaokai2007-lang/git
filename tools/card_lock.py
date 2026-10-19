"""F07 私有表单包装，复用冻结 T12；内存预览重启失效，审计持久化。"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from functools import wraps
from typing import Callable
from datetime import datetime, timedelta

from data import card_binding as binding, card_lock as store, dao
from data._dao_core import connection
from data.db import transaction
from guard import card_credentials
from tools import card, card_binding, subscription
from tools._query_common import ToolError, _fail, _ok, current_user_id, set_session_id
from tools.schemas import ErrorCode, ToolResult

_now = datetime.now
_LOCK = threading.RLock()
_REQUESTS: dict[str, dict] = {}
TTL_SECONDS = 300
IMPACT = '锁定后该卡不能调整额度；不会取消已有订阅，不会冻结关联账户。'


def _safe(operation: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
    @wraps(operation)
    def run(*args, **kwargs) -> ToolResult:
        try:
            with _LOCK:
                return operation(*args, **kwargs)
        except ToolError as exc:
            return _fail(exc.code, exc.message)
        except sqlite3.Error:
            return _fail(ErrorCode.INVALID_STATE, '暂时无法处理锁卡请求，请稍后重试。')
    return run


def _audit(session: str, card_id: str | None, result: str, action: str, trace: str | None = None) -> None:
    dao.insert_audit(trace or secrets.token_hex(12), session, actor='user', intent='card_lock',
                     tool=action, params_json={'card_id': card_id}, risk_level='L2',
                     permission_tier='L2', result=result)


@_safe
def cards() -> ToolResult:
    return card_binding.list_bound_cards(audit=False)


@_safe
def preview(card_id: str, session_id: str) -> ToolResult:
    if not isinstance(card_id, str) or not isinstance(session_id, str) or not session_id:
        return _fail(ErrorCode.INVALID_ARGUMENT, '请选择银行卡。')
    with _LOCK:
        listing = cards()
        if not listing.ok:
            return listing
        row = store.eligible_card(connection(), card_id, current_user_id())
        if not row or row['status'] != 'normal':
            return _fail(ErrorCode.INVALID_STATE, '仅本人已绑定的正常银行卡可以锁定。')
        set_session_id(session_id)
        ref = subscription.issue_confirm_ref(card.MANAGE_ACTION, card_id)
        token = secrets.token_urlsafe(32)
        _REQUESTS[token] = {'user': current_user_id(), 'session': session_id, 'card_id': card_id,
                            'ref': ref, 'expires': _now() + timedelta(seconds=TTL_SECONDS)}
        for stale in list(_REQUESTS):
            if _REQUESTS[stale]['expires'] < _now():
                del _REQUESTS[stale]
        with transaction(connection()):
            record = subscription.check_confirm_ref(ref, card.MANAGE_ACTION, card_id)
            _audit(session_id, card_id, 'pending_confirm', 'preview_card_lock', record['trace_id'])
        data = {'token': token, 'card_id': card_id, 'card_no_mask': row['card_no_mask'],
                'status': 'normal', 'impact': IMPACT, 'tier': 'L2'}
        return _ok(data, {k: v for k, v in data.items() if k != 'token'}, '请核对卡片并确认锁定。')


def _request(token: str, session_id: str) -> dict | None:
    item = _REQUESTS.get(token) if isinstance(token, str) else None
    if not item or item['user'] != current_user_id() or item['session'] != session_id:
        return None
    return item if item['expires'] >= _now() else None


@_safe
def submit(token: str, otp: str, session_id: str) -> ToolResult:
    if not isinstance(otp, str) or not re.fullmatch(r'[0-9]{6}', otp):
        return _fail(ErrorCode.INVALID_ARGUMENT, '请输入有效的模拟验证码。')
    with _LOCK:
        item = _request(token, session_id)
        if not item:
            return _fail(ErrorCode.FORBIDDEN, '确认已过期或不属于当前会话，请重新选择银行卡。')
        return _execute(item, otp, session_id)


def _execute(item: dict, otp: str, session: str) -> ToolResult:
    conn = connection()
    key = hashlib.sha256(f"lock:{current_user_id()}".encode()).hexdigest()
    with transaction(conn):
        attempts = binding.attempt(conn, key)
        if attempts['locked_until'] > _now().timestamp():
            return _fail(ErrorCode.FORBIDDEN, '验证码尝试已锁定，请稍后重试。')
        if otp != card.OTP_CODE:
            count = attempts['failures'] + 1 if not attempts['locked_until'] else 1
            deadline = (_now().timestamp() + card_credentials.LOCK_SECONDS
                        if count >= card_credentials.MAX_FAILURES else 0)
            binding.record_attempt(conn, key, count, deadline)
            _audit(session, item['card_id'], 'rejected', 'submit_card_lock')
            return _fail(ErrorCode.FORBIDDEN, '验证码不正确，请重试。')
        binding.record_attempt(conn, key, 0, 0)
    row = store.eligible_card(conn, item['card_id'], current_user_id())
    if not row:
        return _fail(ErrorCode.FORBIDDEN, '该卡不属于本人已绑定银行卡。')
    set_session_id(session)
    result = card.manage_card(item['card_id'], 'lock', confirm_ref=item['ref'], otp=otp)
    if result.ok:
        result.message = f"锁卡成功：{result.data['card_no_mask']} 已锁定。"
    return result


@_safe
def cancel(token: str, session_id: str) -> ToolResult:
    with _LOCK:
        item = _request(token, session_id)
        if item:
            ref = subscription.check_confirm_ref(item['ref'], card.MANAGE_ACTION, item['card_id'])
            if subscription.confirmed_result(ref) is not None:
                return _fail(ErrorCode.INVALID_STATE, '锁卡已执行，不能撤销；恢复使用需另行申请解锁。')
            _REQUESTS.pop(token, None)
            subscription._CONFIRM_REFS.pop(item['ref'], None)
            with transaction(connection()):
                _audit(session_id, item['card_id'], 'rejected', 'cancel_card_lock')
    return _ok({}, {}, '锁卡申请已关闭；未执行的申请已取消。')


@_safe
def records() -> ToolResult:
    return _ok({'items': store.records(connection(), current_user_id())}, {}, '最近的锁卡记录。')


@_safe
def chat_entry(session_id: str) -> ToolResult:
    result = cards()
    if result.ok:
        trace = f'trace-{secrets.token_hex(6)}'
        with transaction(connection()):
            dao.insert_audit(trace, session_id, actor='user', intent='card_lock',
                             tool='card_lock_entry', params_json={}, risk_level='L0',
                             permission_tier='L0', result='success')
        result.facts['trace_id'] = trace
    return result
