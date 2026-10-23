"""F09 verified, durable pre-execution window and atomic card-only loss."""
from datetime import datetime
from contextlib import nullcontext
from functools import wraps
import hashlib
import json
import re
import secrets
import sqlite3

from data import card_binding as binding, card_lost as store, dao
from data.db import transaction
from guard import card_credentials as credentials, permission
from tools import card, card_binding
from tools import _card_lost_config as config
from tools._query_common import ToolError, _fail, _ok, current_user_id
from tools.schemas import ErrorCode, ToolResult

_now = datetime.now


def _safe(operation):
    @wraps(operation)
    def run(*args, **kwargs) -> ToolResult:
        try:
            store.ensure_schema(dao.connection())
            return operation(*args, **kwargs)
        except ToolError as exc:
            return _fail(exc.code, exc.message)
        except (sqlite3.Error, ValueError, TypeError):
            return _fail(ErrorCode.INVALID_STATE, '暂时无法处理挂失补卡，请稍后重试；请以申请记录为准。')
    return run


def _audit(session: str, card_id: str | None, outcome: str, action: str,
           application_id: str | None = None) -> None:
    dao.insert_audit(secrets.token_hex(12), session, actor='user', intent='card_report_lost',
                     tool='card_lost', params_json={'card_id': card_id, 'action': action,
                     'application_id': application_id, 'to_human': True},
                     risk_level='L3', permission_tier='L3', result=outcome)


def _reject(code: ErrorCode, message: str, session: str, action: str,
            item: dict | None = None) -> ToolResult:
    conn = dao.connection()
    # Unknown/foreign identities must never be copied into audit params.
    with nullcontext() if conn.in_transaction else transaction(conn):
        _audit(session if isinstance(session, str) and session else 'invalid-session',
               item['card_id'] if item else None, 'rejected', action,
               item.get('application_id') if item else None)
    return _fail(code, message)


def _allowed(row: dict | None) -> bool:
    return bool(row and row['type'] == 'savings' and row['status'] in ('normal', 'locked'))


def _reply(data: dict) -> ToolResult:
    return _ok(data, {'tier': 'L3', 'to_human': True}, '请查看挂失补卡申请状态；复核标记不代表已审核或已制卡。')


@_safe
def cards() -> ToolResult:
    return card_binding.list_bound_cards(audit=False)


@_safe
def preview(card_id: str, session_id: str) -> ToolResult:
    if not isinstance(card_id, str) or not isinstance(session_id, str) or not session_id:
        return _reject(ErrorCode.INVALID_ARGUMENT, '请选择本人银行卡并在有效会话办理。', session_id, 'preview')
    listing = cards()
    if not listing.ok:
        return listing
    conn = dao.connection()
    with transaction(conn):
        row = store.eligible(conn, card_id, current_user_id())
        if not _allowed(row):
            return _reject(ErrorCode.INVALID_STATE, '仅本人已绑定的正常或临时锁定借记卡支持办理；信用卡、冻结卡及已挂失卡不支持。', session_id, 'preview')
        if store.active(conn, card_id):
            return _reject(ErrorCode.INVALID_STATE, '该卡已有挂失补卡申请，请查看申请记录。', session_id, 'preview')
        data = _preview_data(row)
        token = secrets.token_urlsafe(32)
        store.create(conn, token, current_user_id(), session_id, card_id,
                     _now().timestamp() + credentials.TOKEN_SECONDS, data)
        _audit(session_id, card_id, 'pending_confirm', 'preview')
    return _reply({**data, 'token': token})


def _preview_data(row: dict) -> dict:
    return {'card_id': row['id'], 'card_no_mask': row['card_no_mask'], 'status': row['status'],
            'type': row['type'], **store.profile(dao.connection(), current_user_id()),
            'old_product': '模拟银行借记卡', 'new_product': '模拟银行借记卡',
            'loss_fee_yuan': config.LOSS_FEE_YUAN, 'replacement_fee_yuan': config.REPLACEMENT_FEE_YUAN,
            'total_fee_yuan': config.TOTAL_FEE_YUAN, 'estimated_days': config.ESTIMATED_DAYS,
            'branches': [dict(branch) for branch in config.BRANCHES],
            'default_branch_id': config.BRANCHES[0]['id'], 'pickup_method': '模拟网点领取',
            'impact': config.IMPACT, 'delay_seconds': card.L3_DELAY_SECONDS, 'to_human': True,
            'configuration_note': '费用、预计时间及领取网点均为演示配置，不产生真实费用。'}


def _request(token: str, session: str, field: str = 'token') -> dict | None:
    item = store.get(dao.connection(), field, token) if isinstance(token, str) else None
    if not item or item['user_id'] != current_user_id() or item['session_id'] != session:
        return None
    return item


def _verified(item: dict, pin: str, otp: str) -> bool:
    conn = dao.connection()
    key = hashlib.sha256(f"lost:{current_user_id()}".encode()).hexdigest()
    attempt = binding.attempt(conn, key)
    now = _now().timestamp()
    if attempt['locked_until'] > now:
        _audit(item['session_id'], item['card_id'], 'rejected', 'verify_locked')
        return False
    secret = binding.secret_for_card(conn, item['card_id'])
    valid_format = isinstance(pin, str) and re.fullmatch(r'[0-9]{6}', pin)
    valid_pin = bool(valid_format and secret and credentials.matches_pin(pin, secret['salt'], secret['pin_hash']))
    valid = valid_pin and isinstance(otp, str) and otp == card.OTP_CODE
    if valid:
        binding.record_attempt(conn, key, 0, 0)
        return True
    count = attempt['failures'] + 1 if not attempt['locked_until'] else 1
    until = now + credentials.LOCK_SECONDS if count >= credentials.MAX_FAILURES else 0
    binding.record_attempt(conn, key, count, until)
    _audit(item['session_id'], item['card_id'], 'rejected', 'verify')
    return False


@_safe
def submit(token: str, pin: str, otp: str, branch_id: str, session_id: str) -> ToolResult:
    verdict = permission.assess_write('card_report_lost', tier='L3')
    if not verdict.requires_otp or not verdict.to_human:
        return _reject(ErrorCode.FORBIDDEN, '挂失验证策略不匹配。', session_id, 'submit')
    with transaction(dao.connection()):
        item = _request(token, session_id)
        if not item:
            return _reject(ErrorCode.FORBIDDEN, '确认不属于当前用户或会话，请重新选择。', session_id, 'submit')
        if item['status'] in ('pending', 'success'):
            return _reply(json.loads(item['snapshot']))
        if item['status'] != 'preview' or item['expires_at'] < _now().timestamp():
            return _reject(ErrorCode.TOKEN_EXPIRED, '确认已过期、已撤销或失效，请重新办理。', session_id, 'submit', item)
        data = json.loads(item['snapshot'])
        branches = {branch['id']: branch for branch in data['branches']}
        if not isinstance(branch_id, str) or branch_id not in branches:
            return _reject(ErrorCode.INVALID_ARGUMENT, '请选择有效的模拟领取网点。', session_id, 'submit', item)
        row = store.eligible(dao.connection(), item['card_id'], current_user_id())
        if not _allowed(row) or row['status'] != data['status'] or store.active(dao.connection(), item['card_id']):
            return _reject(ErrorCode.INVALID_STATE, '卡片状态、归属、绑定或申请已变化，请重新办理。', session_id, 'submit', item)
        if not _verified(item, pin, otp):
            return _fail(ErrorCode.FORBIDDEN, '模拟卡密码或验证码不正确，或验证尝试已暂时锁定。')
        now = _now().timestamp()
        app_id = 'lost_' + secrets.token_hex(10)
        data.update(application_id=app_id, status='pending', status_before=row['status'],
                    replacement_status='not_submitted', execute_at=now + card.L3_DELAY_SECONDS,
                    submitted_at=now, branch=branches[branch_id], branch_id=branch_id,
                    submitted_at_text=datetime.fromtimestamp(now).isoformat(timespec='seconds'),
                    execute_at_text=datetime.fromtimestamp(now + card.L3_DELAY_SECONDS).isoformat(timespec='seconds'))
        store.update(dao.connection(), token, 'pending', data, app_id, data['execute_at'], now)
        _audit(session_id, item['card_id'], 'pending_confirm', 'schedule', app_id)
        return _reply(data)


def _settle() -> int:
    count = 0
    conn = dao.connection()
    for item in store.due(conn, _now().timestamp()):
        data = json.loads(item['snapshot'])
        row = store.eligible(conn, item['card_id'], item['user_id'])
        valid = _allowed(row) and row['status'] == data['status_before'] and item['verified_at'] is not None
        if valid:
            dao.update_card(item['card_id'], status='lost')
            data.update(status='success', replacement_status='pending_review', settled_at=_now().timestamp())
        else:
            data.update(status='failed', replacement_status='not_submitted',
                        failure_reason='卡片状态、归属或绑定已变化，未执行挂失，未提交补卡。')
        store.update(conn, item['token'], data['status'], data)
        _audit(item['session_id'], item['card_id'], 'success' if valid else 'rejected',
               'report_lost' if valid else 'execute', item['application_id'])
        count += 1
    return count


@_safe
def settle() -> ToolResult:
    with transaction(dao.connection()):
        count = _settle()
    return _reply({'settled_count': count})


@_safe
def records(session_id: str) -> ToolResult:
    if not isinstance(session_id, str) or not session_id:
        return _fail(ErrorCode.INVALID_ARGUMENT, '请使用有效会话查询。')
    with transaction(dao.connection()):
        _settle()
        items = store.records(dao.connection(), current_user_id())
    return _reply({'items': items})


@_safe
def cancel(application_id: str, session_id: str) -> ToolResult:
    # Restart recovery: revoke by owning user + opaque application ID + fresh ownership,
    # rather than tying a persisted pending request to the defunct browser session.
    if not isinstance(application_id, str) or not isinstance(session_id, str) or not session_id:
        return _reject(ErrorCode.INVALID_ARGUMENT, '请提供有效申请和会话。', session_id, 'cancel')
    with transaction(dao.connection()):
        item = store.get(dao.connection(), 'application_id', application_id)
        if not item or item['user_id'] != current_user_id():
            return _reject(ErrorCode.FORBIDDEN, '申请不属于当前用户。', session_id, 'cancel')
        if not store.eligible(dao.connection(), item['card_id'], current_user_id()):
            return _reject(ErrorCode.FORBIDDEN, '该卡归属或绑定已经变化，无法撤销。', session_id, 'cancel', item)
        if item['status'] == 'cancelled':
            return _reply(json.loads(item['snapshot']))
        if item['status'] != 'pending':
            return _reject(ErrorCode.INVALID_STATE, '申请已经执行或失败，无法撤销。', session_id, 'cancel', item)
        if item['execute_at'] <= _now().timestamp():
            _settle()
            return _reject(ErrorCode.INVALID_STATE, '撤销窗口已结束，请查看申请结果。', session_id, 'cancel', item)
        data = json.loads(item['snapshot'])
        data.update(status='cancelled', replacement_status='not_submitted')
        store.update(dao.connection(), item['token'], 'cancelled', data)
        _audit(session_id, item['card_id'], 'rejected', 'cancel', item['application_id'])
        return _reply(data)


@_safe
def chat_entry(session_id: str) -> ToolResult:
    result = cards()
    if result.ok:
        trace = secrets.token_hex(12)
        with transaction(dao.connection()):
            dao.insert_audit(trace, session_id, actor='user', intent='card_report_lost',
                             tool='card_lost_entry', params_json={}, risk_level='L0',
                             permission_tier='L0', result='success')
        result.facts['trace_id'] = trace
    return result
