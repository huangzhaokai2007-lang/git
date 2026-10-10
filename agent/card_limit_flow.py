"""F06 界面编排：解析输入、权限定档、调用工具；不直接访问数据库。"""
from __future__ import annotations

import re

from agent.write_intents import yuan_to_cents
from guard import permission
from tools import card_limit

KINDS = {"固定授信额度": "fixed", "临时授信额度": "temporary", "单日交易限额": "daily"}


def recognize(text: str) -> str | None:
    clean = re.sub(r"[\s，。！？?!]", "", text)
    if re.search(r"不要|不用|不想|不需要|不打算|别|取消|申请.{0,4}信用卡", clean):
        return None
    if re.search(r"(额度|限额).*(记录|历史|进度|结果)|(记录|历史|进度|结果).*?(额度|限额)", clean):
        return "records"
    if re.search(r"(调整|修改|提高|提升|降低|调高|调低|更改|申请|增加|减少|改|调).{0,12}(额度|限额)|(额度|限额).{0,12}(调整|提高|降低|改|调)", clean):
        return "adjust"
    return None


def requested_kind(text: str) -> str | None:
    if "临时" in text:
        return "temporary"
    if "单日" in text or "每天" in text or "每日" in text or "日限额" in text:
        return "daily"
    if "固定" in text or "永久" in text:
        return "fixed"
    return None


def cards(kind: str) -> dict:
    return card_limit.cards(kind).model_dump()


def preview(card_id: str, kind: str, amount: str, days: int | None, session_id: str) -> dict:
    cents = yuan_to_cents(amount) if isinstance(amount, str) and len(amount) <= 32 else None
    result = card_limit.preview(card_id, kind, cents, days, session_id)
    if result.ok:
        verdict = permission.assess_write("card_limit_adjust", tier=result.facts["tier"])
        if not verdict.requires_otp or verdict.tier != "L2":
            raise ValueError("额度调整必须经过确认和验证码验证")
    return result.model_dump()


def submit(token: str, otp: str, session_id: str) -> dict:
    permission.assess_write("card_limit_adjust", tier="L2")
    return card_limit.submit(token, otp, session_id).model_dump()


def records() -> dict:
    return card_limit.records().model_dump()


def chat_entry(text: str, session_id: str, kind: str):
    import uuid
    from agent.orchestrator import Turn

    result = card_limit.chat_entry(session_id, kind)
    return Turn(trace_id=result.facts.get("trace_id", f"trace-{uuid.uuid4().hex[:12]}"), intent="card_limit_adjust", confidence=1.0,
                states=["IDLE", "CLASSIFY", "CLARIFY", "REPLY", "AUDIT"], tier="L0",
                reply=result.message, error_code=result.error_code,
                ask=("limit_records" if kind == "records" else "limit_adjust") if result.ok else None)
