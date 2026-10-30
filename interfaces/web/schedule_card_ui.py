"""预约卡面 v2（2026-10-30 重构，用户图六要求）：

- 顶部**筛选胶囊**：全部 / 单笔 / 循环
- 每笔一行：时间 · 收款人 · 金额 · 循环徽章 + **立即执行 / 修改 / 取消**（都在这行里）
- 点「修改」→ **该行下面**拉开编辑区（日历 + 时刻，必须晚于现在）
- 不再有页面底部那块独立的「改某笔预约的时间」大表单
"""

from __future__ import annotations

import calendar
import datetime
import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

import premium_ui
from agent import confirm_card, schedule_flow, write_flow

FILTERS = ("全部", "单笔", "循环")


def _record(step: write_flow.Step, summary: str) -> None:
    state = st.session_state
    text = step.reply or step.ask or ""
    state["messages"].append({"role": "assistant", "text": text, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})


def _start(executor: ThreadPoolExecutor, session_id: str, intent: str,
           slots: dict, display: str) -> None:
    confirm_card.clear(session_id)
    _record(executor.submit(write_flow.start, intent, slots, session_id).result(), display)


def is_too_early(day: datetime.date, clock: datetime.time,
                 now: datetime.datetime | None = None) -> bool:
    """目标时刻必须严格晚于现在（纯函数，便于单测）。"""
    return datetime.datetime.combine(day, clock) <= (now or datetime.datetime.now())


def _cycle_badge(item: dict) -> str:
    """循环徽章：循环的**一眼可见**（用户反馈「循环表现不够突出」）。"""
    cycle = str(item.get("repeat_cycle") or "")
    if not cycle:
        return "· 一次性"
    label = {"daily": "🔁 每日", "weekly": "🔁 每周", "monthly": "🔁 每月"}.get(cycle, f"🔁 {cycle}")
    nxt = str(item.get("next_run_at") or "").replace("T", " ")
    until = str(item.get("repeat_until") or "")
    detail = " · ".join(x for x in (f"至 {until}" if until else "", f"下次 {nxt}" if nxt else "") if x)
    return f"{label}（{detail}）" if detail else label


def _row(executor: ThreadPoolExecutor, session_id: str, item: dict) -> None:
    """一行 = 一条预约；三个操作都在行内；「修改」在该行下方拉开编辑区。"""
    schedule_id = str(item["schedule_id"])
    when = str(item["execute_at"]).replace("T", " ")
    opened = st.session_state.get("sched-edit") == schedule_id
    left, run_col, edit_col, cancel_col = st.columns([4.6, 1, 1, 1], vertical_alignment="center")
    left.markdown(f"**{when}** · {item['payee_name']} · **{item['amount_yuan']} 元**"
                  f"　`{_cycle_badge(item)}`")
    if run_col.button("立即执行", key=f"run-{schedule_id}", width="stretch",
                      help="现在就从账户扣款转账，不再等到预约时间"):
        _start(executor, session_id, "scheduled_run", {"schedule_id": schedule_id},
               f"（按钮）立即执行预约 {when}")
        st.rerun()
    if edit_col.button("收起修改" if opened else "修改", key=f"edit-{schedule_id}",
                       width="stretch", help="改这笔预约的时间"):
        st.session_state["sched-edit"] = None if opened else schedule_id
        st.rerun()
    if cancel_col.button("取消", key=f"cancel-{schedule_id}", width="stretch",
                         help="取消这笔预约（不扣款）"):
        _start(executor, session_id, "scheduled_cancel", {"schedule_id": schedule_id},
               f"（按钮）取消预约 {when}")
        st.rerun()
    if opened:
        _editor(executor, session_id, item)


def _editor(executor: ThreadPoolExecutor, session_id: str, item: dict) -> None:
    """行内编辑：日期（日历）+ 时刻，必须晚于现在；确认后回到 write_flow 改期。"""
    schedule_id = str(item["schedule_id"])
    now = datetime.datetime.now()
    with st.container(border=True, key=f"sched-edit-box-{schedule_id}"):
        st.caption(f"修改这一笔：{str(item['execute_at']).replace('T', ' ')} · {item['payee_name']}"
                   f"　（必须晚于现在）")
        day_col, time_col = st.columns(2, vertical_alignment="bottom")
        picked_day = day_col.date_input("新日期", value=now.date() + datetime.timedelta(days=1),
                                        min_value=now.date(), key=f"sched-day-{schedule_id}",
                                        format="YYYY-MM-DD")
        picked_time = time_col.time_input("新时刻", value=datetime.time(9, 0),
                                          key=f"sched-clock-{schedule_id}", step=300)
        too_early = is_too_early(picked_day, picked_time, now)
        if too_early:
            st.caption("⚠️ 这个时间已经过去了，请选一个晚于现在的时刻。")
        elif picked_day.day > calendar.monthrange(picked_day.year, picked_day.month)[1]:
            st.caption("⚠️ 这一天在这个月里不存在，请重新选。")
        if st.button("保存新时间", key=f"sched-save-{schedule_id}", type="primary",
                     disabled=too_early, width="stretch"):
            iso = datetime.datetime.combine(picked_day, picked_time).isoformat(timespec="minutes")
            _start(executor, session_id, "scheduled_modify",
                   {"schedule_id": schedule_id, "schedule": iso},
                   f"（按钮）改预约时间 → {iso}")
            st.session_state["sched-edit"] = None
            st.rerun()


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """筛选 + 行内操作 + 行内编辑；没有待执行预约就不画。"""
    items = executor.submit(schedule_flow.pending_schedules).result()
    if not items:
        return
    recurring = sum(1 for item in items if item.get("repeat_cycle"))
    premium_ui.card_head(f"⏰ 待执行的预约（{len(items)} 笔）",
                         f"到点才扣款 · 其中循环 {recurring} 笔；每行可直接执行 / 修改 / 取消",
                         tone="warn")
    picked = st.pills("筛选", FILTERS, default="全部", key="sched-filter",
                      label_visibility="collapsed") or "全部"
    shown = [item for item in items
             if picked == "全部"
             or (picked == "循环" and item.get("repeat_cycle"))
             or (picked == "单笔" and not item.get("repeat_cycle"))]
    if not shown:
        st.caption("这个筛选下没有预约。")
        return
    for item in shown:
        _row(executor, session_id, item)
