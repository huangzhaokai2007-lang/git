"""聊天区内的用户操作记录；技术审计仍保留在折叠区域。"""
import streamlit as st
from agent.templates import INTENT_CN
import components as ui


def _close() -> None:
    st.session_state['operation-records-open'] = False


def render(records: list[dict]) -> None:
    if not st.session_state.get('operation-records-open'):
        return
    with st.container(border=True, key='operation-records'):
        st.markdown('### 操作记录')
        st.caption('本次聊天中的请求与办理反馈；可通过聊天查询额度调整、锁卡或挂失补卡记录。')
        if not records:
            st.info('暂无操作记录，可以直接聊天或选择快捷场景开始。')
        for record in reversed(records):
            turn = record.get('turn') or {}
            label = INTENT_CN.get(turn.get('intent'), '请求处理')
            st.write(f"**{record.get('ts', '')} · {label}**")
            st.caption(record.get('text', ''))
            st.markdown(turn.get('reply') or '请查看聊天中的办理反馈。')
        with st.expander('技术审计详情', expanded=False):
            ui.timeline(records)
        st.button('收起操作记录', key='operation-records-close', on_click=_close)
