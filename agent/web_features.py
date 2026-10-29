"""Product features removed from the banking demo's web entry."""
from agent import classifier, confirm_card, pending_slots, shortcut_flow


def clear_removed_drafts(session: str) -> None:
    item = pending_slots.peek(session)
    if item and item.get('intent') == 'gift_plan':
        pending_slots.clear(session)
    current = confirm_card.current(session)
    if current and current.intent == 'gift_plan':
        shortcut_flow.begin(session)


def available(verdict):
    if verdict.intent == 'gift_plan':
        return classifier.IntentOut(intent='out_of_scope', confidence=1, slots={})
    return verdict
