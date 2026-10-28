"""风险程度卡片：查完「我的风险等级」之后，如果还没测评，给一个入口按钮。

为什么单独一个模块：`wealth_ui` 管的是产品卡，这里是「人」的卡片，职责不同；
而且它只依赖 `intent` + `facts.assessed`，不该被塞进理财模块里。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import streamlit as st


def render_turn(turn: Mapping[str, Any], *, ask: Callable[[str], None],
                key_prefix: str = "risk") -> None:
    """只在「查了等级但还没测评」时画按钮；查到了等级就不画（回执里已经说清楚）。"""
    if turn.get('intent') != 'risk_assess':
        return
    facts = turn.get('facts') or {}
    if facts.get('assessed') is not False:
        return
    st.markdown('#### 先做一次风险测评')
    st.markdown('一共 9 道题、大约 1 分钟，做完我就能按您的承受能力推荐产品。')
    if st.button('开始风险测评', key=f'{key_prefix}-assess', type='primary', width='stretch'):
        ask('做风险测评')
