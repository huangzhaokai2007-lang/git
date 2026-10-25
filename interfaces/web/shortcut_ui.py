"""Explicit shortcut navigation replaces the current workspace, keeping chat history."""
import streamlit as st

from agent import shortcut_flow
import card_binding_ui
import card_limit_ui
import card_lock_ui
import card_lost_ui


def start(executor, session: str) -> None:
    """显式点快捷入口 → 替换当前工作区，但**不销毁进行中的额度调整草稿**。

    2026-10-26 修：原先这里额外 `card_limit_ui.reset()`，于是走不通一条正常路径 ——
    用户在额度调整里走到「请选择已绑定银行卡」，发现自己还没绑卡，于是点侧边栏「管卡」
    去绑一张，回来草稿已经被清了，只能从头再来。

    注意**不要**顺着这个思路也去掉 `card_binding_ui.reset()`：绑卡表单的关闭是
    `tests/test_web_single_chat.py::test_operation_history_keeps_chat_and_closes_binding_form`
    明确锁定的行为（点「操作记录」收起绑卡表单），去掉它会打破那条用例。
    真正需要作废的是**待确认的写申请**，那件事由 `shortcut_flow.begin()` 负责
    （它会 revoke 草稿并写审计），这里不要重复插手。
    """
    card_lock_ui.close(executor, session)
    executor.submit(shortcut_flow.begin, session).result()
    card_binding_ui.reset()
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
