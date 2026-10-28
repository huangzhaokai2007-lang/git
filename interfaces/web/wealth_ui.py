"""理财推荐卡片（F11）：2×2 网格 + 月 K 图 + 订购按钮。

用户需求：
- 「每个理财产品下方可以有一个图表来展示盈亏范围」
- 「四个理财产品的位置就像四象限」
- 「产品名称底下有盈亏范围的图标」
- 「加上一个按钮，按了之后可以直接展示出所有产品」
- 「改成股票那种 K 线的感觉，而且可以放大」（2026-10-28）

**按钮只做一件事**：把「买 X 元 Y」交给编排层（`ask`）。
申购的槽位校验、风险等级匹配、余额校验、确认卡、扣款全部在编排层与工具层完成 ——
**界面不碰业务、不算钱、不绕过确认**。

**月 K 用 Altair 画**（Streamlit 自带依赖，不新增库）：`mark_rule` 画影线、`mark_bar` 画实体，
`interactive()` 给滚轮缩放与拖拽平移。OHLC 全部来自 `facts`（整数基点，界面只除以 100 展示）。
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


def _kline(candles: list[Mapping[str, Any]]) -> None:
    """月 K 图：阳线绿、阴线红，滚轮缩放 + 拖拽平移。

    `candles` 来自 facts（整数基点，10000 = 净值 1.0000），这里只除以 100 变成「元」。
    用 Altair 而不是 plotly：altair 是 Streamlit 自带依赖，不新增第三方库。
    """
    import altair as alt
    import pandas as pd

    frame = pd.DataFrame({
        "date": pd.to_datetime([f"{row['period']}-01" for row in candles]),
        "o": [int(row["open"]) / 100 for row in candles],
        "h": [int(row["high"]) / 100 for row in candles],
        "l": [int(row["low"]) / 100 for row in candles],
        "c": [int(row["close"]) / 100 for row in candles],
    })
    base = alt.Chart(frame).encode(
        x=alt.X("date:T", title=None,
                axis=alt.Axis(format="%Y-%m", labelAngle=-45, tickCount=6)))
    wick = base.mark_rule(color="#94a3b8", strokeWidth=1).encode(
        y=alt.Y("l:Q", title="净值", scale=alt.Scale(zero=False)), y2="h:Q")
    body = base.mark_bar(size=7).encode(
        y="o:Q", y2="c:Q",
        color=alt.condition("datum.c >= datum.o", alt.value("#22c55e"), alt.value("#ef4444")),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m", title="月份"),
                 alt.Tooltip("o:Q", title="开"), alt.Tooltip("h:Q", title="高"),
                 alt.Tooltip("l:Q", title="低"), alt.Tooltip("c:Q", title="收")])
    #: `interactive()` = 滚轮缩放 + 拖拽平移；用户明确要求「可以放大」。
    st.altair_chart((wick + body).properties(height=200).interactive(), width="stretch")


def _card(item: Mapping[str, Any], *, ask: Callable[[str], None], key_prefix: str) -> None:
    """一只产品一张卡：名称 → 月 K → 区间数字 → 金额 + 订购。"""
    pid = str(item.get("product_id"))
    floor = max(int(_num(item.get("min_amount_yuan"))), 1)

    with st.container(border=True):
        st.markdown(f"**{item.get('name')}**")
        #: 聊天气泡的全局样式会隐藏 st.caption；关键产品信息必须用普通 Markdown。
        #: 起购额必须露出来 —— 否则用户点「订购」才知道买不起（演示实测）。
        st.markdown(f"{item.get('risk_label') or '风险等级待核实'} · "
                    f"预期年化 {item.get('yield')} · 期限 {item.get('term')} 天 · "
                    f"起购 {item.get('min_amount_yuan')} 元")

        #: 月 K 图（替代原来的红绿区间条）：可缩放、可拖拽。
        candles = item.get("candles") or []
        if candles:
            _kline(candles)
        #: 区间文字保留：K 线看的是走势，这两个数字说清「最坏 / 最好能到什么程度」。
        st.markdown(
            "<div style='display:flex;justify-content:space-between;font-size:.8rem;'>"
            f"<span style='color:#ef4444;'>▼ 参考下沿 {html.escape(str(item.get('max_loss_pct')))}%</span>"
            f"<span style='color:#22c55e;'>▲ 参考上沿 {html.escape(str(item.get('max_gain_pct')))}%</span>"
            "</div>", unsafe_allow_html=True)

        #: 点开图例看历年表现。数字全部来自 facts（工具层按产品 id 确定性生成，见 `_product_history`），
        #: 界面只排版；标题里必须写明「模拟」——数据集没有真实净值。
        history = item.get("history") or []
        if history:
            with st.expander("📈 历年收益与亏损（模拟）", expanded=False):
                st.markdown(
                    "| 年度 | 年度收益 | 年内最大回撤 |\n|---|---|---|\n" + "\n".join(
                        f"| {row.get('year')} | {row.get('return_text')} | "
                        f"{row.get('drawdown_text')} |" for row in history))
                st.bar_chart(
                    {"年度": [str(row.get("year")) for row in history],
                     "年度收益(%)": [int(row.get("return") or 0) / 100 for row in history],
                     "最大回撤(%)": [int(row.get("drawdown") or 0) / 100 for row in history]},
                    x="年度", y=["年度收益(%)", "最大回撤(%)"])
                st.markdown("以上为**模拟数据**，仅用于演示风险与收益的对应关系，"
                            "不代表历史真实业绩，也不构成收益承诺。")

        amount = st.number_input(
            "金额（元）", min_value=floor, value=floor, step=100,
            key=f"{key_prefix}-amt-{pid}", label_visibility="collapsed")
        #: 买不起就别给可点的按钮：`affordable` 由工具层按「起购额 ≤ 储蓄余额」判定。
        #: 老 facts 没有这个键时按"可买"处理，保持向后兼容。
        if item.get("affordable") is False:
            st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch", disabled=True)
            st.markdown("起购金额高于储蓄余额，暂时买不了。")
        elif st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch"):
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
