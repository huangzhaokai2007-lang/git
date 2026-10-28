"""Original main bill/ledger presentation, retained independently of other scenes."""
import streamlit as st
from agent import bill_detail_flow
from interfaces.web import components, insight_card_ui


def render(messages, executor, session: str, ask) -> None:
    components.transcript(messages, compact=True, legacy_bills=True,
        insight_card=lambda turn, key: insight_card_ui.render(turn, key=key, on_ask=ask),
        bill_details=lambda source, category: executor.submit(
            bill_detail_flow.load, session, source, category).result())
    latest = components.latest_bill(messages[st.session_state.get('workspace-start', 0):])
    if latest:
        with st.expander('📊 这份账单的图表', expanded=True):
            st.caption('图表数据全部取自编排层回执的事实包，与对话框里的数字同一份来源。')
            st.button('⬇️ 拉取近 3 期账单（逐期走编排层）', type='primary',
                      on_click=_pull, args=(ask,))
            components.bill_panel(components.bill_series(messages), latest)


def _pull(ask) -> None:
    for period in ('本月', '上个月', '上上个月'):
        ask(f'出一份{period}的账单报告', rerun=False)
    st.rerun()
