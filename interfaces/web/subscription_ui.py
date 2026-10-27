"""订阅卡（F18 补全）：列表与提醒的每一行都直接给「取消订阅」按钮。

**按钮只做一件事**：把「取消 X 订阅」交给编排层（`ask`）。
商户名 → 订阅 id 的解析、风险提示、L2 确认卡、审计全部在 `agent/` 与 `tools/` 完成 ——
界面不查库、不判断能不能取消、不绕过确认。

只对 `active` 的订阅给按钮：查已取消 / 已暂停的列表是**只读记录**，再给一个「取消」按钮
只会让人以为还能取消第二次。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import streamlit as st

#: 能取消的状态。其余状态只展示、不给按钮。
CANCELLABLE = "active"

#: 两行布局的列宽：信息左、按钮右（用户要求「产品的旁边加上取消订阅的按钮」）。
_INFO, _ACTION = (3.6, 1.1)


def _sid(item: Mapping[str, Any]) -> str:
    """订阅 id：列表用 `id`，提醒用 `subscription_id`（两套 facts 的键名不同）。"""
    return str(item.get("id") or item.get("subscription_id") or "")


def _detail(item: Mapping[str, Any], *, reminder: bool) -> str:
    """一行说明（金额 / 周期 / 扣费时间），数字全部照抄 facts，界面不做换算。"""
    amount = f"{item.get('amount_yuan')} 元"
    cycle = item.get("cycle_cn") or item.get("cycle") or ""
    head = f"{amount}／{cycle}" if cycle else amount
    when = (f"{item.get('days_cn')}（{item.get('next_charge_date')}）" if reminder
            else f"下次扣费 {item.get('next_charge_date')}")
    return f"{head} · {when}"


def _row(item: Mapping[str, Any], *, ask: Callable[[str], None], key_prefix: str,
         reminder: bool) -> None:
    """一行订阅：商户与明细在左，取消按钮在右。"""
    merchant = str(item.get("merchant") or "")
    with st.container(border=True):
        info, action = st.columns([_INFO, _ACTION], vertical_alignment="center")
        with info:
            st.markdown(f"**{merchant}**")
            #: 聊天气泡的全局样式会隐藏 `st.caption`，关键信息必须用普通 Markdown。
            st.markdown(_detail(item, reminder=reminder))
        with action:
            if st.button("取消订阅", key=f"{key_prefix}-cancel-{_sid(item)}", width="stretch"):
                ask(f"取消{merchant}订阅")


def render_turn(turn: Mapping[str, Any], *, ask: Callable[[str], None],
                key_prefix: str = "subs") -> None:
    """紧贴订阅列表 / 订阅提醒回执，逐行渲染「取消订阅」入口。"""
    intent = turn.get("intent")
    if intent not in ("subscription_list", "subscription_remind") or turn.get("error_code"):
        return
    facts = turn.get("facts") or {}
    items = facts.get("items") or []
    if not items:
        return
    if intent == "subscription_list" and str(facts.get("status") or "active") != CANCELLABLE:
        return                                   # 已取消 / 已暂停：只读记录，不给按钮
    st.markdown("#### 订阅管理 · 可直接取消")
    st.markdown("点「取消订阅」会走正常流程：确认卡 → 确认后才生效。")
    for item in items:
        _row(item, ask=ask, key_prefix=key_prefix, reminder=intent == "subscription_remind")
