"""收款人**解析卡**（web）：同名多命中时列出候选，点一位就带着 id 继续。

设计 §3 关卡 1：「命中多个 → 出解析卡」。本层只渲染 `agent/payee_picker` 的待选快照，
选完交回 `write_flow.start`（**不再二次解析** —— 人已经由用户点明）。

分层：只调 `agent/`，不碰 `tools/`/`data/`、不写业务。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

import step_record
from agent import payee_picker, write_flow


def _record(step: write_flow.Step, summary: str) -> None:
    """把选择后的回执记进会话（与 app._ask 同口径；含 L3 的 pending_id）。"""
    step_record.record_step(step, summary)


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """有同名候选就渲染解析卡；没有则什么都不画。"""
    pending = executor.submit(payee_picker.pending, session_id).result()
    if not pending:
        return
    candidates = list(pending.get("candidates") or [])
    if not candidates:
        return
    with st.container(border=True):
        #: 2026-11-01（用户实测）：只说「转账」没点名时，候选是**全部**收款人 ——
        #: 标题不能说「N 个同名」，那是同名歧义的说法，会让人以为系统认错了人。
        if str(pending.get("reason") or "same_name") == "pick":
            st.markdown("##### 👥 先选一位收款人")
            st.caption("下面列出您的收款人（黑名单不列）。选完再问金额、再选转账用哪张卡。")
        else:
            st.markdown(f"##### 🔍 找到 {len(candidates)} 个同名收款人，请选择")
            st.caption("状态：🔍 解析中（5 分钟内有效，超时自动作废）")
            st.caption("点「选这位」会带着这个人的 id 继续，不再二次解析。")
        for item in candidates:
            left, right = st.columns([3, 1], vertical_alignment="center")
            left.markdown(f"**{item['name']}** · `{item['phone']}`")
            if right.button("选这位", key=f"pick-{item['id']}", width="content"):
                picked = executor.submit(payee_picker.pick, session_id, item["id"]).result()
                if picked is not None:
                    intent, slots = picked
                    step = executor.submit(write_flow.start, intent, slots, session_id).result()
                    _record(step, f"（解析卡选择：{item['name']}）")
                st.rerun()
