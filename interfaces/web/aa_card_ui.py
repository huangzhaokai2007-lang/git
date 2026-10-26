"""AA 收款**卡面**（web）：列出进行中的 AA，每笔给「登记某人已付 / 撤销」。

与收款人卡、预约卡同一性质：**按钮确定性直达写流水线**（`write_flow.start`，意图由按钮给定），
不赌 LLM 分类；在途确认卡先作废。

分层：只调 `agent/`（`aa_flow` 只读 + `write_flow` + `confirm_card`），不碰 `tools/`/`data/`。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
import premium_ui  # noqa: E402 —— 统一卡头观感

from agent import aa_flow, confirm_card, write_flow


def _record(step: write_flow.Step, summary: str) -> None:
    """把回执记进会话（与 app._ask 同口径）。"""
    state = st.session_state
    text = step.reply or step.ask or ""
    state["messages"].append({"role": "assistant", "text": text, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})


def _start(executor: ThreadPoolExecutor, session_id: str, intent: str,
           slots: dict, display: str) -> None:
    """按钮直达：清在途卡 → `write_flow.start` → 记回执。"""
    confirm_card.clear(session_id)
    step = executor.submit(write_flow.start, intent, slots, session_id).result()
    _record(step, display)


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染 AA 卡；没有进行中的 AA 就不画（已完成的只看聊天里的表格）。"""
    items = executor.submit(aa_flow.active).result()
    if not items:
        return
    premium_ui.card_head(f"🧾 进行中的 AA（{len(items)} 笔）",
                     "24 小时内有效；可登记已付 / 提醒 / 撤销", tone="")
    for item in items:
        request_id = str(item["request_id"])
        left, cancel_btn = st.columns([4, 1], vertical_alignment="center")
        left.markdown(f"**{item['title']}** · {item['total_yuan']} 元 · "
                      f"已付 {item['paid_count']}/{item['share_count']} 人 · "
                      f"截止 {item['expire_at']}")
        if cancel_btn.button("撤销", key=f"aa-cancel-{request_id}", width="content",
                             help="撤销这笔 AA（已登记的付款记录保留）"):
            _start(executor, session_id, "aa_cancel", {"request_id": request_id},
                   f"（按钮）撤销 AA {item['title']}")
            st.rerun()
        pending = [s for s in (item.get("shares") or []) if s["status"] != "paid"]
        if not pending:
            st.caption("这笔 AA 已全部付清 ✅")
            continue
        names = {f"{s['payee_name']}（{s['amount_yuan']} 元）": s["payee_id"] for s in pending}
        pick_col, btn_col = st.columns([3, 1], vertical_alignment="center")
        picked = pick_col.selectbox("登记谁已付", list(names), key=f"aa-who-{request_id}",
                                    label_visibility="collapsed")
        if btn_col.button("登记已付", key=f"aa-paid-{request_id}", type="primary",
                          width="content"):
            _start(executor, session_id, "aa_mark_paid",
                   {"request_id": request_id, "payee_id": names[picked]},
                   f"（按钮）登记 {picked} 已付")
            st.rerun()
