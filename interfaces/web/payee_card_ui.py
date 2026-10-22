"""网页端收款人卡（S6）：`payee_list` 的列表 + 每行操作按钮。

**为什么单独一个文件**：与 `card_binding_ui.py` 同一性质 —— 「编排层判出某意图 → 界面渲染
可交互的卡」。`app.py` 已经 280 行（红线 300），收款人卡再塞进去就超了。

**分层**：本模块属交互层，**只 import `agent/`**（`orchestrator` / `payee_resolve`），
不 import `tools` / `data` / `guard`、不写 SQL、不算业务数字。

**按钮语义**：点按钮 = 替用户说一句完整的话，走**同一条** `orchestrator.handle` 流水线 ——
不是绕过流水线直接改库。所以「转账」会先出确认卡 / 追问金额，「加白」走 L2 + 验证码，
与聊天里说一遍**完全等价**。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import orchestrator, payee_resolve

#: 三类人员的中文标签（面向用户只说大白话；与 `render_lists` 的措辞同口径）
KIND_CN = {"whitelist": "白名单", "blacklist": "黑名单", "normal": "普通"}


def _dispatch(executor: ThreadPoolExecutor, session_id: str, display: str, text: str) -> None:
    """替用户发一句话，并把回执记进会话（与 `app._ask` 同口径，不写业务）。"""
    state = st.session_state
    state["messages"].append({"role": "user", "text": display})
    turn = executor.submit(orchestrator.handle, text, session_id=session_id).result()
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": turn.model_dump()})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display, "turn": turn.model_dump()})
    state["clarify_round"] = state.get("clarify_round", 0) + (1 if "CLARIFY" in turn.states else 0)


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染收款人列表卡；每行给「转账」「加白」两个按钮（加黑意图尚未接线，先不放）。

    列表来自 `agent.payee_resolve.list_people()`（只读当前 user；黑白名单、标签一并带回）。
    """
    people = executor.submit(payee_resolve.list_people).result()
    if not people:
        return
    st.markdown("##### 👥 收款人")
    for person in people:
        kind = ("whitelist" if person["is_whitelist"]
                else "blacklist" if person["blacklist"] else "normal")
        tags = f" · 标签 {', '.join(person['tags'])}" if person.get("tags") else ""
        left, transfer_btn, whitelist_btn, _ = st.columns([4, 1, 1, 1],
                                                          vertical_alignment="center")
        left.markdown(f"**{person['name']}** · `{person['masked_phone']}`"
                      f" · {KIND_CN[kind]}{tags}")
        if transfer_btn.button("转账", key=f"tr-{person['payee_id']}",
                               help=f"给 {person['name']} 转账"):
            _dispatch(executor, session_id, f"（按钮）给{person['name']}转账",
                      f"给{person['name']}转账")
            st.rerun()
        if not person["is_whitelist"] and not person["blacklist"]:
            if whitelist_btn.button("加白", key=f"wl-{person['payee_id']}",
                                    help=f"把 {person['name']} 加入白名单（需验证码）"):
                _dispatch(executor, session_id, f"（按钮）把{person['name']}加入白名单",
                          f"把{person['name']}加入白名单")
                st.rerun()
