"""理财推荐卡片（F11）：每只产品旁边一个「订购」按钮。

用户需求：「推荐理财产品之后，在理财产品的旁边直接出现一个按钮可以直接订购。」

**按钮只做一件事**：把「买 X 元 Y」这句话交给编排层（`ask`）。
申购的槽位校验、风险等级匹配、余额校验、确认卡、扣款全部在编排层与工具层完成 ——
**界面不碰业务、不算钱、不绕过确认**。所以这里的按钮等价于用户自己打了一句话。

为什么从 `turns` 里找而不是新加状态：推荐回执的 `facts` 已经含全部产品字段，
再存一份会漂移（facts 是唯一事实来源）。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import streamlit as st


def latest_recommendation(turns: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """最近一次**成功且有产品**的理财推荐 turn；没有就返回 None。

    只认 `wealth_recommend` 且 `error_code` 为空 —— 失败的推荐不该出现订购按钮。
    """
    for entry in reversed(turns or []):
        turn = entry.get("turn") or {}
        if turn.get("intent") != "wealth_recommend" or turn.get("error_code"):
            continue
        if ((turn.get("facts") or {}).get("items")) or []:
            return turn
        return None                      # 最近一次是空结果 → 不再往回翻
    return None


def render(turns: list[Mapping[str, Any]], *, ask: Callable[[str], None],
           key_prefix: str = "wealth") -> None:
    """渲染「直接订购」区：一行一只产品（名称+参考区间 / 金额 / 订购按钮）。

    金额默认取**起购金额**，用户可改；点「订购」等价于自己说「买 X 元 Y」。
    """
    turn = latest_recommendation(turns)
    if turn is None:
        return
    items = (turn.get("facts") or {}).get("items") or []

    head = st.columns([3, 1.4], vertical_alignment="center")
    with head[0]:
        st.markdown("#### 直接订购")
        st.caption("点「订购」会走正常申购流程：风险等级校验 → 余额校验 → 确认卡。")
    with head[1]:
        #: 用户需求「按了之后可以直接展示出所有产品」：等价于自己说「看看全部产品」。
        if st.button("查看全部产品", key=f"{key_prefix}-all", width="stretch"):
            ask("看看全部理财产品")
    for item in items:
        pid = str(item.get("product_id"))
        #: `min_amount_yuan` 是格式化过的字符串（如 `100,000.00`），去掉千分位再取整。
        floor = max(int(float(str(item.get("min_amount_yuan") or "1").replace(",", ""))), 1)
        cols = st.columns([3.2, 1.2, 0.9], vertical_alignment="center")
        with cols[0]:
            st.markdown(
                f"**{item.get('name')}**　"
                f"<span style='opacity:.72'>{item.get('risk_level')} · 年化 {item.get('yield')} · "
                f"↑ {item.get('max_gain_pct')}% / ↓ {item.get('max_loss_pct')}%</span>",
                unsafe_allow_html=True)
        with cols[1]:
            amount = st.number_input(
                "金额（元）", min_value=floor, value=floor, step=100,
                key=f"{key_prefix}-amt-{pid}", label_visibility="collapsed")
        with cols[2]:
            if st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch"):
                ask(f"买 {item.get('name')} {int(amount)} 元")
