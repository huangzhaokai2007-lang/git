"""F01 模拟绑卡与受保护详情：凭证不进入日志或普通事实包。"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
import uuid
from typing import Callable

from pydantic import ValidationError

from data import card_binding as store, card_binding_seed, dao
from data._dao_core import connection
from data.db import transaction
from guard import card_credentials as credentials
from tools._query_common import _fail, _money, _ok, current_user_id
from tools.card_binding_models import ConfirmReq, CredentialsReq
from tools.card_query import _facts, _item
from tools.schemas import CARD_STATUSES, ErrorCode, ToolResult

_now = time.time
DENIED = "卡号或密码不正确，或该卡不可绑定。"


def _run(action: str, session_id: str, tier: str,
         operation: Callable[[sqlite3.Connection], ToolResult], *, audit: bool = True,
         trace_id: str | None = None) -> ToolResult:
    try:
        conn = connection()
        with transaction(conn):
            store.ensure_schema(conn)
            card_binding_seed.provision(conn, credentials.hash_pin)
            result = operation(conn)
            if not audit:
                return result
            card_id = (result.facts or {}).get("card_id")
            dao.insert_audit(trace_id or f"trace-{uuid.uuid4().hex[:12]}", session_id or "binding",
                             actor="user", intent="card_binding", tool=action,
                             params_json={"card_id": card_id}, risk_level=tier,
                             permission_tier=tier, result="success" if result.ok else "rejected",
                             error_code=None if result.ok else result.error_code)
        return result
    except sqlite3.Error:
        return _fail(ErrorCode.INVALID_STATE, "暂时无法处理银行卡请求，请稍后重试。")


def _valid_credentials(number: object, password: object, session_id: object) -> str | None:
    try:
        if not isinstance(number, str) or not isinstance(password, str):
            return None
        normalized = number.replace(" ", "")
        CredentialsReq(card_no=normalized, password=password, session_id=session_id)
        return normalized if re.fullmatch(r"[0-9]{6}", password) else None
    except ValidationError:
        return None


def _authenticate(conn: sqlite3.Connection, number: str, password: str) -> dict | None:
    user, now = current_user_id(), _now()
    key = credentials.attempt_key(user, number)
    attempts = store.attempt(conn, key)
    if attempts["locked_until"] > now:
        return None
    secret = store.credential(conn, number)
    # 未知卡同样运行 PBKDF2，避免直接短路泄漏卡号是否存在。
    salt = secret["salt"] if secret else "00" * 16
    expected = secret["pin_hash"] if secret else "00" * 32
    matched = credentials.matches_pin(password, salt, expected)
    row = store.owned_card(conn, secret["card_id"], user) if secret else None
    if matched and row:
        store.record_attempt(conn, key, 0, 0)
        return row
    failures = (0 if attempts["locked_until"] else attempts["failures"]) + 1
    deadline = now + credentials.LOCK_SECONDS if failures >= credentials.MAX_FAILURES else 0
    store.record_attempt(conn, key, failures, deadline)
    return None


def preview_binding(card_no: str, password: str, *, session_id: str) -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        number = _valid_credentials(card_no, password, session_id)
        if number is None:
            return _fail(ErrorCode.INVALID_ARGUMENT, "请输入有效的模拟卡号与银行卡密码。")
        row = _authenticate(conn, number, password)
        if row is None:
            return _fail(ErrorCode.FORBIDDEN, DENIED)
        token = secrets.token_urlsafe(32)
        store.create_request(conn, token, current_user_id(), session_id, row["id"],
                             _now() + credentials.TOKEN_SECONDS)
        data = {"token": token, "card_id": row["id"], "card_no_mask": row["card_no_mask"],
                "already_bound": store.is_bound(conn, row["id"], current_user_id())}
        return _ok(data, {"card_id": row["id"], "card_no_mask": row["card_no_mask"]},
                   "银行卡验证成功，请确认绑定。")
    return _run("preview_binding", session_id, "L1", operation)


def confirm_binding(token: str, *, session_id: str) -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        try:
            ConfirmReq(token=token, session_id=session_id)
        except ValidationError:
            return _fail(ErrorCode.INVALID_ARGUMENT, "绑定确认信息无效。")
        request = store.get_request(conn, token)
        user = current_user_id()
        if not request or request["user_id"] != user or request["session_id"] != session_id:
            return _fail(ErrorCode.FORBIDDEN, "绑定确认无效，请重新验证银行卡。")
        if request["expires_at"] <= _now():
            return _fail(ErrorCode.TOKEN_EXPIRED, "绑定确认已过期，请重新验证银行卡。")
        row = store.owned_card(conn, request["card_id"], user)
        if row is None:
            return _fail(ErrorCode.FORBIDDEN, DENIED)
        inserted = store.confirm_request(conn, token, user, row["id"], _now())
        data = {"card_id": row["id"], "card_no_mask": row["card_no_mask"], "already_bound": not inserted}
        return _ok(data, {"card_id": row["id"], "card_no_mask": row["card_no_mask"]},
                   "该银行卡已绑定。" if not inserted else "银行卡绑定成功。")
    return _run("confirm_binding", session_id, "L1", operation)


def _display(row: dict) -> dict:
    return {**_item(row), "balance_yuan": _money(row["balance"])}


def list_bound_cards(status: str | None = None, *, audit: bool = True) -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        if status is not None and (not isinstance(status, str) or status not in CARD_STATUSES):
            return _fail(ErrorCode.INVALID_ARGUMENT, "卡片状态不合法。")
        rows = store.bound_cards(conn, current_user_id(), status)
        data = {"items": [_display(row) for row in rows], "total_count": len(rows)}
        facts = {"items": [{**_facts(row), "balance_yuan": _money(row["balance"])} for row in rows],
                 "total_count": len(rows)}
        return _ok(data, facts, "已查询绑定银行卡。" if rows else "没有符合条件的已绑定银行卡。")
    return _run("list_bound_cards", "binding", "L0", operation, audit=audit)


def card_detail(card_id: str) -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        user = current_user_id()
        row = store.owned_card(conn, card_id, user) if isinstance(card_id, str) else None
        if row is None or not store.is_bound(conn, card_id, user):
            return _fail(ErrorCode.FORBIDDEN, "该银行卡未绑定或不可查看。")
        return _ok(_display(row), {**_facts(row), "balance_yuan": _money(row["balance"])}, "已查询银行卡详情。")
    return _run("card_detail", "binding", "L0", operation)


def reveal_card_number(card_id: str, password: str, *, session_id: str) -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        user = current_user_id()
        if not isinstance(card_id, str):
            return _fail(ErrorCode.INVALID_ARGUMENT, "银行卡信息不合法。")
        secret = store.secret_for_card(conn, card_id)
        if not secret or not store.is_bound(conn, card_id, user):
            return _fail(ErrorCode.FORBIDDEN, "该银行卡未绑定或不可查看。")
        number = _valid_credentials(secret["card_no"], password, session_id)
        row = _authenticate(conn, number, password) if number else None
        if row is None:
            return _fail(ErrorCode.FORBIDDEN, "密码不正确，或暂时无法查看该卡。")
        return _ok({"card_no": number}, {"card_id": card_id}, "验证成功，请在临时区域查看卡号。")
    return _run("reveal_card_number", session_id, "L1", operation)


def reject_inline_credentials(*, session_id: str, trace_id: str) -> ToolResult:
    return _run("reject_inline_credentials", session_id, "L0",
                lambda conn: _fail(ErrorCode.INVALID_ARGUMENT, "请通过绑定银行卡表单输入卡号和密码，不要在聊天中发送凭证。"),
                trace_id=trace_id)
