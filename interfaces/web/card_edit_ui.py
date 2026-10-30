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

import premium_ui
import time_inputs
from agent import card_draft, confirm_card, write_flow

#: 这些槽位是「时间」，必须用日历选（用户 2026-10-30 反复强调）
_TIME_FIELDS = {"schedule": "时刻", "date": "日期", "expire": "日期"}


def _record(step: write_flow.Step, summary: str) -> None:
    """把编辑后的新卡记进会话（与 app._ask 同口径：回执来自编排层，本层不写业务）。"""
    state = st.session_state
    state["messages"].append({"role": "assistant", "text": step.reply, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染可编辑面板：有在途确认卡且该意图有编辑字段时才出现。"""
    draft = executor.submit(card_draft.draft_of, session_id).result()
    if draft is None or draft.status != "confirm":
        st.session_state["card-edit-open"] = False      # 卡没了就复位，别留个空展开态
        return
    #: 2026-10-30（用户要求）：默认**收起**，点确认卡上的「✏️ 修改」才拉出来。
    if not st.session_state.get("card-edit-open"):
        return
    #: 2026-10-28（用户要求）：这张「每人收款金额」卡**不再出现** ——
    #: 群收款已由卡面的「按人设置金额」负责，页面上不要出现两块同样的表单。
    if draft.kind == "aa_collect":                      # 群收款由卡面的「按人设置金额」负责
        return
    if not draft.fields:
        return
    with st.container(border=True, key="card-edit-box"):
        premium_ui.card_head("✏️ 修改这张确认卡", "改完点下面按钮会重新预检（不是直接改卡）", tone="warn")
        changed: dict[str, str] = {}
        for field in draft.fields:
            new = _field(field)
            if new and new != field.value:
                changed[field.name] = new
        col_ok, col_tip = st.columns([1, 3], vertical_alignment="center")
        if col_ok.button("按修改重新预检", key=f"recheck-{draft.kind}", type="primary",
                         disabled=not changed, width="stretch"):
            step = executor.submit(write_flow.edit, session_id, changed).result()
            _record(step, f"（卡片编辑：{', '.join(changed)}）")
            st.session_state["card-edit-open"] = False      # 用户要求：重新预检后收起
            st.rerun()
        col_tip.caption("改完自动重走收款人解析与风控；确认/验证码按卡片提示回复。")


def _field(field) -> str:
    """一个字段的输入控件；**时间类字段一律用日历**（schedule 带时刻，date/expire 只选日期）。"""
    key = f"edit-{field.kind}-{field.name}"
    if field.name == "schedule":
        value, too_early = time_inputs.datetime_field(field.label, key)
        return "" if too_early else value
    if field.name in _TIME_FIELDS:
        return time_inputs.date_field(field.label, key)
    return str(st.text_input(field.label, value=field.value, key=key) or "").strip()
