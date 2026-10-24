"""审计回放小面板（web）：输入 trace_id → 展示完整链路。

分层：只调 `agent.audit_flow`（L0 只读）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import audit_flow


def render(executor: ThreadPoolExecutor) -> None:
    """侧边栏折叠面板：不影响主流程，评审可自己玩。"""
    with st.sidebar:
        with st.expander("🔍 审计回放（输入 trace_id）", expanded=False):
            st.caption("证明「金额与档位来自工具层」：这里只有审计记录，没有模型输出。")
            trace_id = st.text_input("trace_id", key="audit-trace", placeholder="trace-xxxxxxxxxxxx")
            if st.button("回放", key="audit-replay", type="secondary") and trace_id.strip():
                lines, factors, data = executor.submit(
                    audit_flow.replay, trace_id.strip()).result()
                if not lines:
                    st.warning(data.get("error") or "没有找到该 trace_id 的记录")
                    return
                st.markdown(f"**共 {len(lines)} 步**")
                for line in lines:
                    st.markdown(f"- {line}")
                if factors:
                    st.markdown("**风险因子 / 处置**")
                    for item in factors:
                        st.markdown(f"- {item}")
