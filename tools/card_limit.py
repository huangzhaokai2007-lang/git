"""F06 私有表单工具：预览→L2→确认/OTP→事务幂等申请；不改冻结 T12。"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Callable

from pydantic import ValidationError

from data import card_binding as binding, card_binding_seed, card_limit as store, card_limit_seed, dao
from data._dao_core import connection
from data.db import transaction
from guard import card_credentials, permission
from tools import _card_limit_rules as rules
from tools._query_common import _fail, _money, _ok, current_user_id
from tools.card_limit_models import LimitPreviewReq, LimitSubmitReq
from tools.schemas import ErrorCode, ToolResult
from tools.transfer import OTP_CODE

_now = datetime.now
TTL_SECONDS = 300


def _audit(action: str, session: str, result: ToolResult, trace: str | None = None) -> None:
    dao.insert_audit(trace or f"trace-{uuid.uuid4().hex[:12]}", session, actor="agent",
                     intent="card_limit_adjust", tool=action,
                     params_json={"card_id": result.facts.get("card_id"), "mocked": True},
                     risk_level="L2", permission_tier="L2",
                     result="success" if result.ok else "rejected",
                     error_code=None if result.ok else result.error_code)


def _expire(conn: sqlite3.Connection) -> None:
    for temp in store.expired(conn, current_user_id(), _now().isoformat()):
        row = binding.owned_card(conn, temp["card_id"], current_user_id())
        if row is None:
            continue
        old = rules.effective(row, temp)
        store.adjust_available(conn, row["account_id"], (row["credit_limit"] or 0) - old)
        store.clear_temporary(conn, row["account_id"])
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", "limit-expiry", actor="system",
                         intent="card_limit_adjust", tool="expire_card_limit",
                         params_json={"card_id": row["id"], "application_id": temp["application_id"]},
                         risk_level="L0", permission_tier="L0", result="success")


def refresh_due() -> ToolResult:
    """普通余额/卡片查询也刷新到期状态；不创建新卡，不嵌套事务。"""
    try:
        conn = connection()
        if store.has_schema(conn):
            if conn.in_transaction:
                _expire(conn)
            else:
                with transaction(conn):
                    _expire(conn)
        return _ok({}, {}, "已检查临时额度有效期。")
    except sqlite3.Error:
        return _fail(ErrorCode.INVALID_STATE, "暂时无法检查临时额度有效期。")


def _run(action: str, session: str, operation: Callable, *, audit: bool = True) -> ToolResult:
    try:
        conn = connection()
        with transaction(conn):
            binding.ensure_schema(conn)
            card_binding_seed.provision(conn, card_credentials.hash_pin)
            card_limit_seed.provision(conn, card_credentials.hash_pin)
            store.ensure_schema(conn)
            _expire(conn)
            result = operation(conn)
            if audit:
                _audit(action, session, result)
        return result
    except sqlite3.Error:
        return _fail(ErrorCode.INVALID_STATE, "暂时无法处理额度请求，请稍后重试。")


def cards(kind: str) -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        if kind not in rules.KINDS:
            return _fail(ErrorCode.INVALID_ARGUMENT, "请选择额度调整类型。")
        rows = binding.bound_cards(conn, current_user_id(), None)
        items = [rules.card_view(conn, row, kind) for row in rows]
        return _ok({"items": items}, {"items": items}, "已查询可调整银行卡。")
    return _run("limit_cards", "limit-query", operation)


def preview(card_id: str, kind: str, target: int, days: int | None, session_id: str) -> ToolResult:
    try:
        req = LimitPreviewReq(card_id=card_id, kind=kind, target=target, days=days, session_id=session_id)
    except ValidationError:
        return _fail(ErrorCode.INVALID_ARGUMENT, "申请金额或有效期格式不合法。")

    def operation(conn: sqlite3.Connection) -> ToolResult:
        row = binding.owned_card(conn, card_id, current_user_id())
        if row is None or not binding.is_bound(conn, card_id, current_user_id()):
            return _fail(ErrorCode.FORBIDDEN, "该卡未绑定或不属于当前用户。")
        reason = rules.validate(conn, row, kind, target, days)
        if reason:
            return _fail(ErrorCode.INVALID_ARGUMENT, reason)
        snap = rules.snapshot(conn, row)
        token = secrets.token_urlsafe(32)
        store.save_preview(conn, token, current_user_id(), session_id, req.model_dump(),
                           (_now() + timedelta(seconds=TTL_SECONDS)).isoformat(), snap)
        view = rules.card_view(conn, row, kind)
        data = {"token": token, "card_id": card_id, "card_no_mask": row["card_no_mask"],
                "kind": kind, "kind_cn": rules.KINDS[kind], "old_limit": view["current_limit"],
                "old_limit_yuan": view["current_limit_yuan"], "target": target, "target_yuan": _money(target),
                "days": days, "tier": "L2", "requires_otp": True, "ttl_seconds": TTL_SECONDS,
                "expires_at": (_now() + timedelta(days=days)).isoformat() if days else None}
        return _ok(data, {k: v for k, v in data.items() if k != "token"}, "请核对申请信息并验证后确认提交。")
    return _run("preview_card_limit", session_id, operation)


def _otp(conn: sqlite3.Connection, token: str, value: str) -> bool:
    key = "limit-otp:" + hashlib.sha256((current_user_id() + token).encode()).hexdigest()
    attempt = binding.attempt(conn, key)
    now = _now().timestamp()
    if attempt["locked_until"] > now:
        return False
    if value == OTP_CODE:
        binding.record_attempt(conn, key, 0, 0)
        return True
    failures = (0 if attempt["locked_until"] else attempt["failures"]) + 1
    binding.record_attempt(conn, key, failures, now + 300 if failures >= 5 else 0)
    return False


def _apply(conn: sqlite3.Connection, req: dict, row: dict, app_id: str, expires: str | None) -> None:
    kind, target = req["kind"], req["target"]
    temp = store.temporary(conn, row["account_id"])
    old_effective = rules.effective(row, temp)
    if kind == "daily":
        store.set_daily(conn, row, target)
    elif kind == "fixed":
        store.set_fixed(conn, row["account_id"], target)
        new_effective = max(target, temp["total_limit"] if temp else 0)
        store.adjust_available(conn, row["account_id"], new_effective - old_effective)
    else:
        store.set_temporary(conn, row, target, expires, app_id)
        store.adjust_available(conn, row["account_id"], target - old_effective)


def _execute(conn: sqlite3.Connection, req: dict, row: dict) -> ToolResult:
    status = "approved" if req["kind"] == "daily" else store.outcome(conn, row["id"])
    reasons = {"pending": "预设模拟人工审核场景，申请已受理，额度暂未变化。",
               "approved": "模拟审批通过，额度已生效。",
               "rejected": "未满足模拟提额条件（预设拒绝场景），原额度保持不变。"}
    now = _now().isoformat()
    expires = (_now() + timedelta(days=req["days"])).isoformat() if req["kind"] == "temporary" else None
    view = rules.card_view(conn, row, req["kind"])
    item = {"id": f"limitreq_{uuid.uuid4().hex[:12]}", "user_id": current_user_id(), "card_id": row["id"],
            "card_no_mask": row["card_no_mask"], "kind": req["kind"], "old_limit": view["current_limit"],
            "target": req["target"], "approved_limit": req["target"] if status == "approved" else None,
            "status": status, "reason": reasons[status], "created_at": now,
            "effective_at": now if status == "approved" else None, "expires_at": expires,
            "trace_id": f"trace-{uuid.uuid4().hex[:12]}"}
    if status == "approved":
        _apply(conn, req, row, item["id"], expires)
    store.save_application(conn, item)
    store.finish_preview(conn, req["token"], item["id"])
    result = _application_result(item)
    _audit("submit_card_limit", req["session_id"], result, item["trace_id"])
    return result


def _application_result(item: dict) -> ToolResult:
    view = rules.application_view(item, _now().isoformat())
    return _ok(view, view, f"{view['card_no_mask']}：{view['status_cn']}。{view['reason']}")


def submit(token: str, otp: str, session_id: str) -> ToolResult:
    try:
        LimitSubmitReq(token=token, otp=otp, session_id=session_id)
    except ValidationError:
        return _fail(ErrorCode.INVALID_ARGUMENT, "请填写有效的确认信息与验证码。")

    def operation(conn: sqlite3.Connection) -> ToolResult:
        req = store.preview(conn, token)
        if not req or req["user_id"] != current_user_id() or req["session_id"] != session_id:
            result = _fail(ErrorCode.FORBIDDEN, "申请确认不属于当前用户或会话。")
        elif not _otp(conn, token, otp):
            result = _fail(ErrorCode.FORBIDDEN, "验证码不正确或验证已暂时锁定。")
        elif req["application_id"]:
            return _application_result(store.application(conn, req["application_id"], current_user_id()))
        elif req["expires_at"] <= _now().isoformat():
            result = _fail(ErrorCode.TOKEN_EXPIRED, "确认已过期，请重新填写申请。")
        else:
            return _submit_checked(conn, req)
        _audit("submit_card_limit", session_id, result)
        return result
    return _run("submit_card_limit", session_id, operation, audit=False)


def _submit_checked(conn: sqlite3.Connection, req: dict) -> ToolResult:
    row = binding.owned_card(conn, req["card_id"], current_user_id())
    valid = row and binding.is_bound(conn, row["id"], current_user_id())
    if not valid:
        result = _fail(ErrorCode.FORBIDDEN, "该卡未绑定或不属于当前用户。")
    elif rules.snapshot(conn, row) != json.loads(req["snapshot"]):
        result = _fail(ErrorCode.INVALID_STATE, "卡片额度、余额或状态发生变化，请重新确认申请。")
    elif reason := rules.validate(conn, row, req["kind"], req["target"], req["days"]):
        result = _fail(ErrorCode.INVALID_ARGUMENT, reason)
    else:
        permission.assess_write("card_limit_adjust", tier="L2")
        return _execute(conn, req, row)
    _audit("submit_card_limit", req["session_id"], result)
    return result


def records() -> ToolResult:
    def operation(conn: sqlite3.Connection) -> ToolResult:
        items = [rules.application_view(item, _now().isoformat())
                 for item in store.applications(conn, current_user_id())]
        return _ok({"items": items}, {"items": items}, "已查询额度调整记录。")
    return _run("card_limit_records", "limit-query", operation)


def chat_entry(session_id: str, kind: str) -> ToolResult:
    trace = f"trace-{uuid.uuid4().hex[:12]}"
    def operation(conn: sqlite3.Connection) -> ToolResult:
        message = "请在我的银行卡的额度调整记录中查看申请状态。" if kind == "records" else "请选择固定授信额度、临时授信额度或单日交易限额，然后选择已绑定银行卡。"
        dao.insert_audit(trace, session_id, actor="agent", intent="card_limit_adjust", tool="limit_chat_entry",
                         params_json={"view": kind}, risk_level="L0", permission_tier="L0", result="success")
        return _ok({}, {"trace_id": trace}, message)
    return _run("limit_chat_entry", session_id, operation, audit=False)
