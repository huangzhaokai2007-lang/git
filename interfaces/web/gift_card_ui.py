"""送礼**查询卡面**（web）：按状态筛选 + 每笔给「支付 / 取消」。

与收款人卡、预约卡、AA 卡同一性质：**按钮确定性直达写流水线**（意图由按钮给定），零 LLM 分类；
在途确认卡先作废。分层：只调 `agent/`，不碰 `tools/`/`data/`。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import confirm_card, gift_flow, write_flow

#: 状态筛选（人类规格「支持按状态筛选：全部 / 待支付 / 已支付 / 已完成 / 已取消」）
_FILTERS = {"全部": None, "待支付": "created", "已支付": "paid", "已取消": "cancelled"}


def _record(step: write_flow.Step, summary: str) -> None:
    state = st.session_state
    text = step.reply or step.ask or ""
    state["messages"].append({"role": "assistant", "text": text, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})


def _start(executor: ThreadPoolExecutor, session_id: str, intent: str,
           slots: dict, display: str) -> None:
    confirm_card.clear(session_id)
    step = executor.submit(write_flow.start, intent, slots, session_id).result()
    _record(step, display)


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染送礼查询卡；没有订单就不画。"""
    if not executor.submit(gift_flow.orders).result():
        return
    st.markdown("##### 🎁 送礼订单")
    picked = st.radio("筛选", list(_FILTERS), horizontal=True, key="gift-filter",
                      label_visibility="collapsed")
    items = executor.submit(gift_flow.orders, _FILTERS[picked]).result()
    if not items:
        st.caption("这一筛选下没有订单。")
        return
    for item in items:
        order_id = str(item["order_id"])
        left, pay_btn, cancel_btn = st.columns([3.6, 0.9, 0.9], vertical_alignment="center")
        left.markdown(f"**{item['contact']}** · {item['amount_yuan']} 元 · "
                      f"{item['gift_date']} · {item['status_cn']} · `{order_id}`")
        if item["status"] == "created":                     # 只有「待支付」能支付/取消
            if pay_btn.button("支付", key=f"gpay-{order_id}", width="content",
                              help="从储蓄账户真扣款（需验证码）"):
                _start(executor, session_id, "gift_order_pay", {"order_id": order_id},
                       f"（按钮）支付订单 {order_id}")
                st.rerun()
            if cancel_btn.button("取消", key=f"gcancel-{order_id}", width="content",
                                 help="取消订单并解锁冻结金额"):
                _start(executor, session_id, "gift_order_cancel", {"order_id": order_id},
                       f"（按钮）取消订单 {order_id}")
                st.rerun()
