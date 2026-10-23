"""预约**查询卡面**（web）：列出待执行预约，每行给「立即执行 / 改时间 / 取消」。

与收款人卡同一性质：**按钮确定性直达写流水线**（`write_flow.start`，意图由按钮给定），
不赌 LLM 分类；在途确认卡先作废。

分层：只调 `agent/`（`schedule_flow` 只读列表 + `write_flow` + `confirm_card`），
不碰 `tools/`/`data/`、不写业务。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import confirm_card, schedule_flow, write_flow


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
    """渲染待执行预约卡；没有待执行预约就不画。"""
    items = executor.submit(schedule_flow.pending_schedules).result()
    if not items:
        return
    st.markdown(f"##### ⏰ 待执行的预约（{len(items)} 笔）")
    for item in items:
        schedule_id = str(item["schedule_id"])
        when = str(item["execute_at"]).replace("T", " ")
        left, run_btn, edit_btn, cancel_btn = st.columns([3.4, 0.9, 0.9, 0.9],
                                                         vertical_alignment="center")
        left.markdown(f"**{when}** · {item['payee_name']} · {item['amount_yuan']} 元")
        if run_btn.button("立即执行", key=f"run-{schedule_id}", width="content",
                          help="现在就从账户扣款转账（不再等到预约时间）"):
            _start(executor, session_id, "scheduled_run", {"schedule_id": schedule_id},
                   f"（按钮）立即执行预约 {when}")
            st.rerun()
        if cancel_btn.button("取消", key=f"cancel-{schedule_id}", width="content",
                             help="取消这笔预约（不扣款）"):
            _start(executor, session_id, "scheduled_cancel", {"schedule_id": schedule_id},
                   f"（按钮）取消预约 {when}")
            st.rerun()
    #: 「改时间」要填新时间 → 单独一个表单（避免逐行塞输入框挤成一团）
    with st.container(border=True):
        st.markdown("###### ✏️ 改某笔预约的时间")
        options = {f"{str(it['execute_at']).replace('T', ' ')} · {it['payee_name']}": it["schedule_id"]
                   for it in items}
        picked = st.selectbox("改哪一笔", list(options), key="sched-edit-pick")
        new_time = st.text_input("新时间", value="", key="sched-edit-time",
                                 placeholder="例如 2026-11-05 09:00")
        if st.button("改到这个时间", key="sched-edit-ok", type="primary",
                     disabled=not new_time.strip()):
            _start(executor, session_id, "scheduled_modify",
                   {"schedule_id": options[picked], "schedule": new_time.strip()},
                   f"（按钮）改预约时间 → {new_time.strip()}")
            st.rerun()
