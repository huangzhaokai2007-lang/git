"""Explicit shortcut navigation replaces the current workspace, keeping chat history."""
import streamlit as st

from agent import shortcut_flow
import card_binding_ui
import card_limit_ui
import card_lock_ui
import card_lost_ui


def start(executor, session: str) -> None:
    card_lock_ui.close(executor, session)
    executor.submit(shortcut_flow.begin, session).result()
    card_binding_ui.reset()
    card_limit_ui.reset()
    card_lost_ui.reset()
    state = st.session_state
    for domain in ('card', 'payee', 'schedule', 'aa', 'gift', 'lost'):
        state[f'bound-{domain}-query'] = False
    for key in list(state):
        if str(key).startswith(('edit-', 'aa-amt-', 'risk-', 'payee-', 'scene-candidate-')):
            del state[key]
    state['workspace-start'] = len(state['messages'])
    state['clarify_round'] = 0
    state['pending_id'] = None
    state['operation-records-open'] = False
