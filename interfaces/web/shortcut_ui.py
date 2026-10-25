"""Explicit shortcut navigation replaces the current workspace, keeping chat history."""
import streamlit as st

from agent import shortcut_flow
import card_binding_ui
import card_limit_ui
import card_lock_ui
import card_lost_ui


def start(executor, session: str) -> None:
    """显式点快捷入口 → 替换当前工作区，但**不销毁进行中的表单/草稿**。

    2026-10-26 修：原先这里无差别 `reset()` 掉 `card_limit_ui` / `card_binding_ui`，
    导致一个真实走不通的路径 —— 用户在额度调整里走到「请选择已绑定银行卡」，发现自己还没绑卡，
    于是点侧边栏「管卡」去绑一张，回来草稿已经被清了，只能从头再来；「操作记录」也一样。
    这两个是**只读/导航**入口，不该动业务草稿。真正需要作废的是待确认的写申请，
    那件事由 `shortcut_flow.begin()` 负责（它会 revoke 草稿并写审计），这里不要重复插手。
    """
    card_lock_ui.close(executor, session)
    executor.submit(shortcut_flow.begin, session).result()
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
