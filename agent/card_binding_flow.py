"""界面专用的模拟卡片入口；凭证直接交给工具，不经过分类器。"""
from __future__ import annotations

import re
import uuid

from agent import card_request_language, classifier
from guard import card_credentials, permission
from tools import card_binding
from tools._query_common import current_user_id
from tools.schemas import ToolResult

STATUS_LABELS = {"normal": "正常", "locked": "已锁定", "lost": "已挂失", "frozen": "已冻结"}
TYPE_LABELS = {"savings": "储蓄卡", "credit": "信用卡"}
BINDING_PROMPT = "请在绑卡表单中输入预设模拟卡号及对应的银行卡密码，验证后确认绑定。"
#: 「取消{商户}订阅」——订阅卡按钮的固定文案；`这个/那个` 留给场景上下文按候选解析。
_CANCEL_SUB = re.compile(r"取消(.{1,40}?)订阅")
_NOT_A_MERCHANT = frozenset({"这个", "那个", "该", "这", "那"})
#: 理财卡上「开始风险测评」按钮的固定文案 —— 直接定意图，免得模型不可用时按钮点了没反应。
_ASSESS_TEXTS = frozenset({"做风险测评", "开始风险测评", "风险测评"})


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
    from tools import card_unlock
    result = card_binding.list_bound_cards(status)
    if result.ok:
        permissions = card_unlock.cards()
        allowed = {item['card_id']: item.get('can_unlock', False)
                   for item in (permissions.data or {}).get('items', [])} if permissions.ok else {}
        for item in result.data['items']:
            item['self_unlockable'] = allowed.get(item['card_id'], False)
    return _view(result)


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
    from agent import web_features
    web_features.clear_removed_drafts(session_id or 'binding')

    if safe_chat_text(text) != text:
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        result = card_binding.reject_inline_credentials(session_id=session_id or "binding", trace_id=trace_id)
        return orchestrator.Turn(trace_id=trace_id, intent="card_query",
                                 states=["IDLE", "CLARIFY", "REPLY", "AUDIT"], reply=result.message,
                                 ask=result.message)
    history = [safe_chat_text(turn) for turn in history] if history else None
    from agent import card_limit_flow, card_lock_flow, card_lost_flow, card_lost_language

    if kind := card_lost_language.recognize(text):
        return card_lost_flow.chat_entry(session_id or 'lost', kind)
    if kind := card_lock_flow.recognize(text):
        if kind == 'missing':
            return card_lost_flow.chat_entry(session_id or 'lost', 'choice')
        return card_lock_flow.chat_entry(session_id or 'lock', kind)
    if kind := card_limit_flow.recognize(text):
        return card_limit_flow.chat_entry(text, session_id or "limit", kind)
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
    #: 订阅卡上的「取消订阅」按钮是**界面固定句式**（`interfaces/web/subscription_ui.py`）：
    #: 这里直接定意图，避免模型把商户名吞掉；商户名仍由写流程按真实订阅名匹配，认不出就追问。
    #: 放在界面入口而不是通用分类器 —— 用户自己说「取消云音乐订阅」时，仍应走模型的语义判断。
    cancel = _CANCEL_SUB.fullmatch(str(text).split("\n", 1)[0].strip())
    if cancel and cancel[1] not in _NOT_A_MERCHANT:
        return classifier.IntentOut(intent="subscription_cancel", confidence=1.0,
                                    slots={"merchant": cancel[1]})
    #: 理财卡的「开始风险测评」同理：固定文案直接定意图（L0 只读，界面据 intent 弹问卷）。
    if str(text).split("\n", 1)[0].strip() in _ASSESS_TEXTS:
        return classifier.IntentOut(intent="risk_assess", confidence=1.0, slots={})
    from agent import web_features
    return web_features.available(classifier.classify(text, history))


def safe_chat_text(text: str) -> str:
    return card_credentials.redact_chat_credentials(text)
