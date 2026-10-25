"""Start explicit navigation without resuming an unrelated unsubmitted write."""
import uuid

from agent import confirm_card, payee_flow, payee_picker, scene_context, session_memory
from data import dao
from tools._scene_drafts import revoke


def begin(session: str) -> None:
    """Cancel only the current draft; preserve OTP guards and submitted applications."""
    previous = scene_context.snapshot(session)
    current = confirm_card.current(session)
    if current is not None:
        revoke(current.credential_kind, current.credential)
        confirm_card.clear(session)
        dao.insert_audit(f'trace-{uuid.uuid4().hex[:12]}', session, actor='user',
                         intent=current.intent, tool='cancel_shortcut_draft',
                         params_json={'reason': 'shortcut_switch'}, risk_level=current.tier,
                         permission_tier=current.tier, result='rejected')
    payee_picker.clear(session)
    payee_flow.clear_pending(session)
    session_memory.reset(session)
    pending_id = previous.get('pending_id')
    pending = confirm_card.pending_of(pending_id, session) if pending_id else None
    if pending and not pending.revoked and not pending.released:
        scene_context.update(session, pending_id=pending_id)
