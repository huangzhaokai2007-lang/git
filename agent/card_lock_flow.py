"""F07 编排，中文入口与 L2 预检，界面不能访问工具。"""
import re
import uuid

from guard import permission, injection
from tools import card_lock, card_unlock


def recognize(text: str) -> str | None:
    clean = re.sub(r'\s+', '', text)
    if injection.detect(clean).blocked or re.search(r'不要|不想|不用|无需|别|没有丢|没丢|没有不见', clean):
        return None
    if re.search(r'记录|历史', clean) and re.search(r'锁卡|锁定|解锁', clean):
        return 'records'
    if re.search(r'解锁|解除.{0,6}锁定', clean):
        return 'unlock'
    if '挂失' in clean:
        return None
    if '卡' in clean and re.search(r'找不到|没找到|不见了|丢了|丢失|遗失|被偷', clean):
        return 'missing'
    if not re.search(r'锁卡|锁定.{0,8}卡|锁住.{0,8}卡|停用.{0,8}卡', clean):
        return None
    return 'records' if re.search(r'记录|历史', clean) else 'lock'


def chat_entry(session_id: str, kind: str):
    from agent.orchestrator import Turn
    result = (card_unlock if kind == 'unlock' else card_lock).chat_entry(session_id)
    asks = {'records': 'lock_records', 'lock': 'card_lock', 'missing': 'card_missing', 'unlock': 'card_unlock'}
    replies = {'records': '请查看锁卡与解锁记录。', 'lock': '请先选择要临时锁定的银行卡；确认后才会执行。',
               'missing': '暂时找不到卡，可以先临时锁定继续寻找；如果已确认遗失，请查看挂失说明。',
               'unlock': '请选择本人临时锁定的银行卡，验证模拟短信验证码后自助解锁。'}
    return Turn(trace_id=result.facts.get('trace_id', f'trace-{uuid.uuid4().hex[:12]}'), intent='card_unlock' if kind == 'unlock' else 'card_lock', confidence=1,
                states=['IDLE', 'CLASSIFY', 'CLARIFY', 'REPLY', 'AUDIT'], tier='L0',
                ask=asks[kind] if result.ok else None,
                error_code=result.error_code, reply=replies[kind] if result.ok else result.message)


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


def unlock_cards() -> dict:
    return card_unlock.cards().model_dump()


def unlock_preview(card_id: str, session_id: str) -> dict:
    permission.assess_write('card_temporary_unlock', tier='L2')
    return card_unlock.preview(card_id, session_id).model_dump()


def unlock_submit(token: str, otp: str, session_id: str) -> dict:
    permission.assess_write('card_temporary_unlock', tier='L2')
    return card_unlock.submit(token, otp, session_id).model_dump()


def unlock_cancel(token: str, session_id: str) -> dict:
    return card_unlock.cancel(token, session_id).model_dump()
