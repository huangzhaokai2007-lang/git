"""预约**查询卡面**（web）：列出待执行预约，每行给「立即执行 / 改时间 / 取消」。

与收款人卡同一性质：**按钮确定性直达写流水线**（`write_flow.start`，意图由按钮给定），
不赌 LLM 分类；在途确认卡先作废。

分层：只调 `agent/`（`schedule_flow` 只读列表 + `write_flow` + `confirm_card`），
不碰 `tools/`/`data/`、不写业务。
"""

from __future__ import annotations

import calendar
import datetime

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
import premium_ui  # noqa: E402 —— 统一卡头观感

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


def is_too_early(day: datetime.date, clock: datetime.time,
                 now: datetime.datetime | None = None) -> bool:
    """「改到这个时间」是否该被禁用：目标时刻必须**严格晚于现在**。

    纯函数（不碰 Streamlit），单测直接喂三个时间点即可。
    """
    moment = datetime.datetime.combine(day, clock)
    return moment <= (now or datetime.datetime.now())


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染待执行预约卡；没有待执行预约就不画。"""
    items = executor.submit(schedule_flow.pending_schedules).result()
    if not items:
        return
    premium_ui.card_head(f"⏰ 待执行的预约（{len(items)} 笔）",
                     "到点才会扣款；可在卡片上执行 / 改时间 / 取消", tone="warn")
    for item in items:
        schedule_id = str(item["schedule_id"])
        when = str(item["execute_at"]).replace("T", " ")
        left, run_btn, edit_btn, cancel_btn = st.columns([3.4, 0.9, 0.9, 0.9],
                                                         vertical_alignment="center")
        left.markdown(f"**{when}** · {item['payee_name']} · {item['amount_yuan']} 元")
        with left.expander("详情", expanded=False):
            st.caption(f"预约编号 {schedule_id} · 权限档 {item.get('tier') or '—'}"
                       f" · 循环 {item.get('repeat_cycle') or '一次性'}"
                       f" · 循环至 {item.get('repeat_until') or '—'}")
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
    #: 「改时间」：**日历 + 时间选择器**（用户 2026-10-29 要求）
    #: 原来只有一个文本框，用户得自己敲 `2026-11-05 09:00`，还会输错月份/日。
    #: 现在：日期用 `st.date_input`（点开就是日历，年月日分别选，非法日期选不出来），
    #: 时刻用 `st.time_input`，并强制 **必须晚于此刻**。
    with st.container(border=True):
        premium_ui.card_head("✏️ 改某笔预约的时间", "选日期与时刻；必须晚于现在", tone="warn")
        options = {f"{str(it['execute_at']).replace('T', ' ')} · {it['payee_name']}": it["schedule_id"]
                   for it in items}
        picked = st.selectbox("改哪一笔", list(options), key="sched-edit-pick")
        day_col, time_col = st.columns(2, vertical_alignment="bottom")
        now = datetime.datetime.now()
        picked_day = day_col.date_input("新日期", value=now.date() + datetime.timedelta(days=1),
                                        min_value=now.date(), key="sched-edit-day",
                                        format="YYYY-MM-DD")
        picked_time = time_col.time_input("新时刻", value=datetime.time(9, 0),
                                          key="sched-edit-clock", step=300)
        when = datetime.datetime.combine(picked_day, picked_time)
        too_early = is_too_early(picked_day, picked_time, now)
        if too_early:
            st.caption("⚠️ 这个时间已经过去了 —— 请选一个**晚于现在**的时刻。")
        elif picked_day.day > calendar.monthrange(picked_day.year, picked_day.month)[1]:
            st.caption("⚠️ 这一天在这个月里不存在，请重新选。")
        if st.button("改到这个时间", key="sched-edit-ok", type="primary",
                     disabled=too_early, width="stretch"):
            iso = when.isoformat(timespec="minutes")
            _start(executor, session_id, "scheduled_modify",
                   {"schedule_id": options[picked], "schedule": iso},
                   f"（按钮）改预约时间 → {iso}")
            st.rerun()
