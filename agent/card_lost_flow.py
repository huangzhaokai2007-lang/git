"""F09 orchestration exposes private loss workflow without direct database access."""
from agent import scene_context
from guard import permission
from tools import card_lost


def cards() -> dict:
    return card_lost.cards().model_dump()


def preview(card_id: str, session_id: str) -> dict:
    permission.assess_write('card_report_lost', tier='L3')
    return card_lost.preview(card_id, session_id).model_dump()


def submit(token: str, pin: str, otp: str, branch_id: str, session_id: str) -> dict:
    permission.assess_write('card_report_lost', tier='L3')
    return scene_context.remember_tool_write(session_id, "card_report_lost", card_lost.submit(token, pin, otp, branch_id, session_id))


def cancel(application_id: str, session_id: str) -> dict:
    return card_lost.cancel(application_id, session_id).model_dump()


def records(session_id: str) -> dict:
    return card_lost.records(session_id).model_dump()


def settle() -> dict:
    return card_lost.settle().model_dump()


def start_runner() -> None:
    from tools import _card_lost_runner
    _card_lost_runner.start()


def chat_entry(session_id: str, kind: str = 'lost'):
    from agent.orchestrator import Turn
    result = card_lost.chat_entry(session_id)
    asks = {'lost': 'card_lost', 'choice': 'card_lost_choice', 'records': 'card_lost_records'}
    replies = {'lost': '请选择本人借记卡，阅读挂失补卡须知后办理。',
               'choice': '想先临时锁卡继续找，还是确认挂失并补卡？',
               'records': '请查看挂失补卡申请记录及办理进度。'}
    return Turn(trace_id=result.facts.get('trace_id', ''), intent='card_report_lost', confidence=1,
                states=['IDLE', 'CLASSIFY', 'CLARIFY', 'REPLY', 'AUDIT'], tier='L0',
                ask=asks.get(kind, 'card_lost') if result.ok else None,
                error_code=result.error_code, reply=replies.get(kind, replies['lost']) if result.ok else result.message)
