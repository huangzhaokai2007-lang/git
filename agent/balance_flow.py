"""Refresh the sidebar through the normal audited pipeline in its own read context."""
from agent import classifier, orchestrator, templates


def refresh(session_id: str):
    with templates.template_only():
        return orchestrator.handle('查一下余额', session_id=session_id + ':sidebar-balance',
            intent_resolver=lambda *_: classifier.IntentOut(intent='balance_query', confidence=1,
                                                            slots={'account_type': 'savings'}))
