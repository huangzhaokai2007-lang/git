"""F07 编排，中文入口与 L2 预检，界面不能访问工具。"""
import re
import uuid

from guard import permission, injection
from tools import card_lock


def recognize(text: str) -> str | None:
    clean = re.sub(r'\s+', '', text)
    if injection.detect(clean).blocked or re.search(r'不要|不想|不用|无需|别|解锁|挂失', clean):
        return None
    if not re.search(r'锁卡|锁定.{0,8}卡|锁住.{0,8}卡|停用.{0,8}卡', clean):
        return None
    return 'records' if re.search(r'记录|历史', clean) else 'lock'


def chat_entry(session_id: str, kind: str):
    from agent.orchestrator import Turn
    result = card_lock.chat_entry(session_id)
    return Turn(trace_id=result.facts.get('trace_id', f'trace-{uuid.uuid4().hex[:12]}'), intent='card_lock', confidence=1,
                states=['IDLE', 'CLASSIFY', 'CLARIFY', 'REPLY', 'AUDIT'], tier='L0',
                ask=('lock_records' if kind == 'records' else 'card_lock') if result.ok else None,
                error_code=result.error_code, reply=(('请查看锁卡记录。' if kind == 'records' else
                '请先选择要临时锁定的银行卡；确认后才会执行。') if result.ok else result.message))


def cards() -> dict:
    return card_lock.cards().model_dump()


def preview(card_id: str, session_id: str) -> dict:
    verdict = permission.assess_write('card_lock', tier='L2')
    if not verdict.requires_otp:
        raise ValueError('锁卡必须验证')
    return card_lock.preview(card_id, session_id).model_dump()


def submit(token: str, otp: str, session_id: str) -> dict:
    permission.assess_write('card_lock', tier='L2')
    return card_lock.submit(token, otp, session_id).model_dump()


def cancel(token: str, session_id: str) -> dict:
    return card_lock.cancel(token, session_id).model_dump()


def records() -> dict:
    return card_lock.records().model_dump()
