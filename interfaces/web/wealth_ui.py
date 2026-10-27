"""理财推荐卡片（F11）：2×2 网格 + 盈亏区间图 + 订购按钮。

用户需求：
- 「每个理财产品下方可以有一个图表来展示盈亏范围」
- 「四个理财产品的位置就像四象限」
- 「产品名称底下有盈亏范围的图标」
- 「加上一个按钮，按了之后可以直接展示出所有产品」

**按钮只做一件事**：把「买 X 元 Y」交给编排层（`ask`）。
申购的槽位校验、风险等级匹配、余额校验、确认卡、扣款全部在编排层与工具层完成 ——
**界面不碰业务、不算钱、不绕过确认**。

**盈亏区间图用 HTML/CSS 画**（不引图表库）：以 0 为轴，左侧红色 = 最大亏损，
右侧绿色 = 最高收益，两段宽度按比例分配。数字全部来自 `facts`，界面只做排版。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping
import html

import streamlit as st

#: 网格列数：2 列 → 4 只产品正好排成四象限；超过 4 只继续往下排。
_COLUMNS = 2


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


def _num(text: object) -> float:
    """`"+38.50"` / `"-30.00"` / `"100,000.00"` → float；解析不了按 0 处理。"""
    try:
        return float(str(text or "0").replace(",", "").replace("+", ""))
    except ValueError:
        return 0.0


def _range_chart(gain: float, loss: float) -> str:
    """盈亏区间条：0 为轴，左红（亏）右绿（赚），宽度按比例。

    两端都为 0（R1 货基）时画一条中性灰线，避免除零。
    """
    total = abs(gain) + abs(loss)
    if total <= 0:
        return ('<div style="height:10px;border-radius:5px;background:#cbd5e1;'
                'margin:6px 0 2px;"></div>')
    left = abs(loss) / total * 100
    right = abs(gain) / total * 100
    return (
        '<div style="display:flex;align-items:center;height:12px;margin:6px 0 2px;">'
        f'<div style="width:{left:.1f}%;height:10px;background:#ef4444;'
        'border-radius:5px 0 0 5px;"></div>'
        '<div style="width:2px;height:16px;background:#64748b;flex:none;"></div>'
        f'<div style="width:{right:.1f}%;height:10px;background:#22c55e;'
        'border-radius:0 5px 5px 0;"></div>'
        '</div>'
    )


def _card(item: Mapping[str, Any], *, ask: Callable[[str], None], key_prefix: str) -> None:
    """一只产品一张卡：名称 → 盈亏区间图 → 图标数字 → 金额 + 订购。"""
    pid = str(item.get("product_id"))
    gain = _num(item.get("max_gain_pct"))
    loss = _num(item.get("max_loss_pct"))
    floor = max(int(_num(item.get("min_amount_yuan"))), 1)

    with st.container(border=True):
        st.markdown(f"**{item.get('name')}**")
        #: 聊天气泡的全局样式会隐藏 st.caption；关键产品信息必须用普通 Markdown。
        st.markdown(f"{item.get('risk_label') or '风险等级待核实'} · "
                    f"预期年化 {item.get('yield')} · 期限 {item.get('term')} 天")

        #: 盈亏区间图 + 两端的图标数字（用户要求「名称底下有盈亏范围的图标」）
        st.markdown(_range_chart(gain, loss), unsafe_allow_html=True)
        st.markdown(
            "<div style='display:flex;justify-content:space-between;font-size:.82rem;'>"
            f"<span style='color:#ef4444;'>▼ 参考下沿 {html.escape(str(item.get('max_loss_pct')))}%</span>"
            f"<span style='color:#22c55e;'>▲ 参考上沿 {html.escape(str(item.get('max_gain_pct')))}%</span>"
            "</div>", unsafe_allow_html=True)

        amount = st.number_input(
            "金额（元）", min_value=floor, value=floor, step=100,
            key=f"{key_prefix}-amt-{pid}", label_visibility="collapsed")
        if st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch"):
            ask(f"买 {item.get('name')} {int(amount)} 元")


def render_turn(turn: Mapping[str, Any], *, ask: Callable[[str], None],
                key_prefix: str = "wealth") -> None:
    """紧贴成功的推荐回执，渲染四象限入口及每款产品的订购按钮。"""
    if turn.get('intent') != 'wealth_recommend':
        return
    if turn.get('error_code') == 'INVALID_STATE':
        #: 未测评 → 不要只留一句报错，直接给入口（点一下就进问卷）。
        st.markdown('#### 先做一次风险测评')
        st.markdown('推荐要按您的风险承受能力筛选，所以得先测评（约 1 分钟）。'
                    '结果只保存在本次会话，不会上传。')
        if st.button('开始风险测评', key=f'{key_prefix}-assess', type='primary', width='stretch'):
            ask('做风险测评')
        return
    if turn.get('error_code'):
        return
    items = (turn.get('facts') or {}).get('items') or []
    if not items:
        return
    st.markdown('#### 推荐产品 · 可直接订购')
    st.markdown('**模拟区间说明：** 红绿区间仅为演示规则的示意估算，'
                '不代表历史极值、实际最大亏损或收益承诺。订购仍需经过风险与余额校验，并在确认卡确认。')
    #: 查看全部产品必须走编排层的公开目录查询，不把超风险等级的产品伪装成推荐。
    if st.button('查看全部产品 →', key=f'{key_prefix}-all', width='stretch', type='primary'):
        ask('看看全部理财产品')
    for start in range(0, len(items), _COLUMNS):
        cols = st.columns(_COLUMNS)
        for col, item in zip(cols, items[start:start + _COLUMNS]):
            with col:
                _card(item, ask=ask, key_prefix=key_prefix)


def render(turns: list[Mapping[str, Any]], *, ask: Callable[[str], None],
           key_prefix: str = 'wealth') -> None:
    """兼容旧调用：仅渲染最新一次有明细的成功推荐。"""
    turn = latest_recommendation(turns)
    if turn is not None:
        render_turn(turn, ask=ask, key_prefix=key_prefix)
