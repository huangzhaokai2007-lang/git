"""统一的时间输入控件：所有「填日期 / 改时间」的地方都用**日历**，不再手敲字符串。

用户 2026-10-29：「所有需要填写时间和修改时间的都需要修改成日历」。

- `date_field`：只选日期（`st.date_input`，下限今天）→ 返回 `YYYY-MM-DD`
- `datetime_field`：日期 + 时刻（`st.date_input` + `st.time_input`）→ 返回 `YYYY-MM-DDTHH:MM`
- 过去的时刻会给出提示，并把 `invalid=True` 交给调用方决定是否禁用提交
"""

from __future__ import annotations

import datetime

import streamlit as st


def date_field(label: str, key: str, *, min_days: int = 0,
               help_text: str = "") -> str:
    """纯日期（`YYYY-MM-DD`）；默认不允许选过去。"""
    today = datetime.date.today()
    picked = st.date_input(label, value=today + datetime.timedelta(days=max(min_days, 1)),
                           min_value=today, key=key, format="YYYY-MM-DD", help=help_text or None)
    return picked.isoformat() if isinstance(picked, datetime.date) else ""


def datetime_field(label: str, key: str, *,
                   default_hour: int = 9) -> tuple[str, bool]:
    """日期 + 时刻：返回 `(ISO, 是否已过去)`；过去的时刻不该被提交。"""
    now = datetime.datetime.now()
    col_day, col_time = st.columns(2, vertical_alignment="bottom")
    picked_day = col_day.date_input(label, value=now.date() + datetime.timedelta(days=1),
                                    min_value=now.date(), key=f"{key}-day",
                                    format="YYYY-MM-DD")
    picked_time = col_time.time_input("时刻", value=datetime.time(default_hour, 0),
                                      key=f"{key}-clock", step=300,
                                      label_visibility="collapsed")
    moment = datetime.datetime.combine(picked_day, picked_time)
    too_early = moment <= now
    if too_early:
        st.caption("⚠️ 这个时间已经过去了，请选一个晚于现在的时刻。")
    return moment.isoformat(timespec="minutes"), too_early
