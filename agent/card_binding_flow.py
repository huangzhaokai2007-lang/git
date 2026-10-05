"""界面专用的模拟卡片入口；凭证直接交给工具，不经过分类器。"""
from __future__ import annotations

import uuid

from agent import card_request_language, classifier
from guard import card_credentials, permission
from tools import card_binding
from tools._query_common import current_user_id
from tools.schemas import ToolResult

STATUS_LABELS = {"normal": "正常", "locked": "已锁定", "lost": "已挂失", "frozen": "已冻结"}
TYPE_LABELS = {"savings": "储蓄卡", "credit": "信用卡"}
BINDING_PROMPT = "请在绑卡表单中输入预设模拟卡号及对应的银行卡密码，验证后确认绑定。"


def owner_key() -> str:
    return current_user_id()


def preview(card_no: str, password: str, session_id: str) -> dict:
    permission.assess_write("card_binding", tier="L1")
    return card_binding.preview_binding(card_no, password, session_id=session_id).model_dump()


def confirm(token: str, session_id: str) -> dict:
    permission.assess_write("card_binding", tier="L1")
    return card_binding.confirm_binding(token, session_id=session_id).model_dump()


def _view(result: ToolResult) -> dict:
    payload = result.model_dump()
    if not result.ok:
        return payload
    items = payload["data"].get("items", [payload["data"]])
    for item in items:
        item["status_cn"] = STATUS_LABELS.get(item["status"], item["status"])
        item["type_cn"] = TYPE_LABELS.get(item["type"], item["type"])
    return payload


def list_cards(status: str | None = None) -> dict:
    return _view(card_binding.list_bound_cards(status))


def detail(card_id: str) -> dict:
    result = card_binding.card_detail(card_id)
    payload = _view(result)
    if result.ok:
        data = payload["data"]
        lines = [f"银行卡详情：{data['card_no_mask']}",
                 f"卡类型：{data['type_cn']} · 状态：{data['status_cn']}"]
        for key, label in (("credit_limit", "授信额度"), ("single_limit", "单笔限额"), ("daily_limit", "日限额")):
            amount = result.facts.get(f"{key}_yuan")
            if amount is not None:
                lines.append(f"{label}：{amount} 元")
        payload["summary"] = "\n\n".join(lines)
    return payload


def reveal(card_id: str, password: str, session_id: str) -> dict:
    return card_binding.reveal_card_number(card_id, password, session_id=session_id).model_dump()


def handle(text: str, *, history: list[str] | None = None, clarify_round: int = 0,
           session_id: str | None = None) -> "Turn":
    from agent import orchestrator

    if safe_chat_text(text) != text:
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        result = card_binding.reject_inline_credentials(session_id=session_id or "binding", trace_id=trace_id)
        return orchestrator.Turn(trace_id=trace_id, intent="card_query",
                                 states=["IDLE", "CLARIFY", "REPLY", "AUDIT"], reply=result.message,
                                 ask=result.message)
    history = [safe_chat_text(turn) for turn in history] if history else None
    turn = orchestrator.handle(text, history=history, clarify_round=clarify_round, session_id=session_id,
                               card_reader=lambda slots: card_binding.list_bound_cards(slots.get("status"), audit=False),
                               intent_resolver=_resolve_intent)
    kind = card_request_language.recognize(text)
    if turn.intent == "card_query" and turn.error_code is None and kind in ("bind", "apply"):
        turn.ask = BINDING_PROMPT
        turn.reply = BINDING_PROMPT if kind == "bind" else (
            "当前模拟项目不支持新信用卡申请或审批，只能绑定已经预设的模拟银行卡。" + BINDING_PROMPT)
    return turn


def _resolve_intent(text: str, history: list[str] | None) -> classifier.IntentOut:
    """仅完整匹配明确查询指令；含写操作或其它条件的表达仍交由原分类器。"""
    if card_request_language.recognize(text):
        return classifier.IntentOut(intent="card_query", confidence=1.0, slots={})
    return classifier.classify(text, history)


def safe_chat_text(text: str) -> str:
    return card_credentials.redact_chat_credentials(text)
