"""S5 可编辑卡片组件（web）：在途确认卡的关键字段可改，提交后**重走校验**。

分层：本模块只渲染 `agent/card_draft.CardDraft` / `confirm_card.Confirmation`（业务状态在
agent 层）并把编辑后的值交回 `agent/write_flow.edit()` —— **不直接改卡**（卡上数字必须来自
事实包，设计 §4）。

两种形态：
- **群收款卡**：按人金额**表格**（每人一行、金额可改，一键均摊作为初值）——
  人类需求：要像查询收款人那样列出每人多少，而不是一句话概括；
- 其余可编辑意图（转账/定时/送礼）：逐字段输入。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import card_draft, confirm_card, write_flow


def _record(step: write_flow.Step, summary: str) -> None:
    """把编辑后的新卡记进会话（与 app._ask 同口径：回执来自编排层，本层不写业务）。"""
    state = st.session_state
    state["messages"].append({"role": "assistant", "text": step.reply, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染可编辑面板：有在途确认卡且该意图有编辑字段时才出现。"""
    draft = executor.submit(card_draft.draft_of, session_id).result()
    if draft is None or draft.status != "confirm":
        return
    #: 2026-10-28（用户要求）：这张「每人收款金额」卡**不再出现** ——
    #: 群收款已由卡面的「按人设置金额」负责，页面上不要出现两块同样的表单。
    if draft.kind == "aa_collect":                      # 群收款由卡面的「按人设置金额」负责
        return
    if not draft.fields:
        return
    with st.container(border=True):
        st.markdown("##### ✏️ 修改这张确认卡")
        changed: dict[str, str] = {}
        for field in draft.fields:
            new = st.text_input(field.label, value=field.value,
                                key=f"edit-{draft.kind}-{field.name}")
            if new.strip() and new.strip() != field.value:
                changed[field.name] = new.strip()
        if st.button("按修改重新预检", key=f"recheck-{draft.kind}", disabled=not changed):
            step = executor.submit(write_flow.edit, session_id, changed).result()
            _record(step, f"（卡片编辑：{', '.join(changed)}）")
            st.rerun()
        st.caption("修改后重新走一遍收款人解析与风控（不是直接改卡）；确认/验证码请按卡片提示回复。")
