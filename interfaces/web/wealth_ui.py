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

from functools import lru_cache
from typing import Any, Callable, Mapping
import html

import streamlit as st

#: 分组展示的唯一口径在编排层（工具层常量经 `agent/render_wealth` 翻译）：
#: 界面只负责"一组一栏"，不自己分类、不自己定顺序（铁律 1）。
from agent.render_wealth import category_label, wealth_groups

#: 网格列数：2 列 → 4 只产品正好排成四象限；超过 4 只继续往下排。
_COLUMNS = 2
#: 默认展示的产品数：14 只全铺开会刷满好几屏，先给 4 张（正好两行），其余折叠。
_PREVIEW_COUNT = 4


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


def _kline_spec(candles: list[Mapping[str, Any]]):
    """构建月 K 的 Altair 图表对象（不渲染）—— 拆出来是为了能单测它的结构。"""
    return _kline_spec_cached(_candle_signature(candles))


def _candle_signature(candles: list[Mapping[str, Any]] | list[list]) -> tuple:
    """把 K 线压成可哈希签名（缓存键）。兼容两种形状：

    - 月 K 来自推荐 facts：`{"period": ..., "open": ...}`（可读优先）
    - 日 K 来自 `product_daily`：`[日期, 开, 高, 低, 收]`（省 45% 体积）
    """
    signature = []
    for row in candles:
        if isinstance(row, (list, tuple)):
            period, open_, high, low, close = row
        else:
            period = row["period"]
            open_, high, low, close = row["open"], row["high"], row["low"], row["close"]
        signature.append((str(period), int(open_), int(high), int(low), int(close)))
    return tuple(signature)


@lru_cache(maxsize=128)
def _kline_spec_cached(signature: tuple):
    """Altair 建图每张约 100ms；14 张展开后每次 rerun 都要重算，所以按签名缓存。

    缓存键是**数据本身**，不是产品 id —— 换产品/换参数自然失效，不会拿到旧图。
    """
    import altair as alt
    import pandas as pd

    candles = [{"period": p, "open": o, "high": h, "low": low, "close": c}
               for p, o, h, low, c in signature]
    #: 周期可能是 `YYYY-MM`（月 K）或 `YYYY-MM-DD`（日 K），补齐成完整日期再解析。
    dates = [p if len(p) == 10 else f"{p}-01" for p in (row["period"] for row in candles)]
    #: 周期是不是具体日期（日/周是 `YYYY-MM-DD`，月是 `YYYY-MM`）。
    #: 注意：**不能用它决定横轴格式** —— 周 K 也是具体日期，按它判断会把周 K 也标成 `%m-%d`，
    #: 16 年跨度下全是「03-30」没有年份（用户 2026-10-30 报的就是这个）。
    is_date = len(signature[0][0]) == 10
    frame = pd.DataFrame({
        "date": pd.to_datetime(dates, format="%Y-%m-%d"),
        "o": [int(row["open"]) / 100 for row in candles],
        "h": [int(row["high"]) / 100 for row in candles],
        "l": [int(row["low"]) / 100 for row in candles],
        "c": [int(row["close"]) / 100 for row in candles],
    })
    #: 均线：**收盘价的滚动均值**，纯展示指标（不参与任何业务口径）。
    #: 点数不足窗口时前几根是 NaN，Altair 自动跳过 —— 不用特殊处理。
    frame["ma5"] = frame["c"].rolling(5).mean()
    frame["ma20"] = frame["c"].rolling(20).mean()
    #: 横轴**不写死 format**，交给 Vega 按刻度间隔自适应：
    #: 16 年跨度显示年份、放大到几个月自动变成月日。写死 `%m-%d` 会丢年份（用户报的 bug），
    #: 写死 `%Y` 又会让放大后每一格都显示同一个年份 —— 两种都不行。
    base = alt.Chart(frame).encode(
        x=alt.X("date:T", title=None,
                axis=alt.Axis(labelAngle=-45, tickCount=6)))
    wick = base.mark_rule(color="#94a3b8", strokeWidth=1).encode(
        y=alt.Y("l:Q", title="净值", scale=alt.Scale(zero=False)), y2="h:Q")
    #: 日 K 一根只有几个像素，柱体要细；月 K 可以粗一些。
    body = base.mark_bar(size=3 if is_date else 7).encode(
        y="o:Q", y2="c:Q",
        #: **A 股口径：红涨绿跌**（用户 2026-10-30 明确要求；原来按国际惯例绿涨红跌）。
        color=alt.condition("datum.c >= datum.o", alt.value("#ef4444"), alt.value("#22c55e")),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d" if is_date else "%Y-%m", title="日期"),
                 alt.Tooltip("o:Q", title="开"), alt.Tooltip("h:Q", title="高"),
                 alt.Tooltip("l:Q", title="低"), alt.Tooltip("c:Q", title="收")])
    #: 均线两条：MA5（橙）看短期、MA20（蓝）看中期。比单看裸 K 更容易读出趋势。
    ma5 = base.mark_line(color="#f59e0b", strokeWidth=1.4).encode(y="ma5:Q")
    ma20 = base.mark_line(color="#3b82f6", strokeWidth=1.4).encode(y="ma20:Q")
    #: `interactive()` = 滚轮缩放 + 拖拽平移；用户明确要求「可以放大」。
    return (wick + body + ma5 + ma20).properties(height=200).interactive()


def _kline(candles: list[Mapping[str, Any]]) -> None:
    """K 线图：**红涨绿跌**（A 股口径），滚轮缩放 + 拖拽平移。

    `candles` 来自 facts（整数基点，10000 = 净值 1.0000），这里只除以 100 变成「元」。
    用 Altair 而不是 plotly：altair 是 Streamlit 自带依赖，不新增第三方库。
    """
    st.altair_chart(_kline_spec(candles), width="stretch")


#: 结构化产品条款行的展示顺序（**键来自 facts**，标签只是排版文字）
_STRUCTURE_ROWS = (("挂钩标的", "underlying"), ("观察频率", "observe"),
                   ("敲出条款", "knock_out"), ("敲入条款", "knock_in"),
                   ("票息/收益", "coupon"), ("本金保护", "protect"))


def _structure(item: Mapping[str, Any]) -> None:
    """结构化产品的条款区（普通产品没有条款 → 什么都不画）。

    为什么必须展示：雪球、鲨鱼鳍的风险不在"波动多大"而在**条款本身** ——
    敲入之后要承担指数跌幅、收益有上限、部分结构非保本。不给条款，
    用户只看得见那个高票息。数字与文案全部来自 facts（工具层按条款原文下发），
    界面不解析、不算。
    """
    if not item.get("structure"):
        return
    st.markdown(f"🧩 {item['structure']}")
    with st.expander("条款细则（模拟）", expanded=False):
        st.markdown("| 条款 | 内容 |\n|---|---|\n" + "\n".join(
            f"| {label} | {item.get(key) or '—'} |"
            for label, key in _STRUCTURE_ROWS if item.get(key)))
        if item.get("structure_note"):
            st.markdown(item["structure_note"])
        st.markdown("以上为**模拟条款**，仅用于演示结构化产品的收益与风险机制，"
                    "不构成任何产品的合同内容或收益承诺。")


def _card(item: Mapping[str, Any], *, ask: Callable[[str], None], key_prefix: str,
          daily_loader: Callable[[str], Mapping[str, Any]] | None = None,
          readonly: bool = False) -> None:
    """一只产品一张卡：名称 → 类型/条款 → 月 K → 区间数字 → 金额 + 订购。"""
    pid = str(item.get("product_id"))
    floor = max(int(_num(item.get("min_amount_yuan"))), 1)

    with st.container(border=True):
        st.markdown(f"**{item.get('name')}**")
        #: 聊天气泡的全局样式会隐藏 st.caption；关键产品信息必须用普通 Markdown。
        #: 起购额必须露出来 —— 否则用户点「订购」才知道买不起（演示实测）。
        #: 类型标签（如「结构化」「货币型」）：货架上有 25 只产品，不给类型标签
        #: 用户只能从名字猜它是什么 —— 结构化产品尤其看不出来（名字里没有"结构化"三个字）。
        st.markdown(f"{item.get('risk_label') or '风险等级待核实'} · "
                    f"{item.get('kind_label') or '未分类'} · "
                    f"预期年化 {item.get('yield')} · 期限 {item.get('term')} 天 · "
                    f"起购 {item.get('min_amount_yuan')} 元")

        _structure(item)
        #: **周期切换**（用户要求：图表上方一排按钮，点哪个看哪个）：
        #: 月 K 直接用推荐 facts 里的 5 年数据（零请求）；日 K / 周 K 按需拉取并缓存在会话里。
        candles = item.get("candles") or []
        granular = st.radio(
            "K 线周期", ["日K", "周K", "月K"], index=2, horizontal=True,
            key=f"{key_prefix}-gran-{pid}", label_visibility="collapsed")
        if granular == "月K":
            if candles:
                _kline(candles)
        elif daily_loader is not None:
            cache_key = f"{key_prefix}-k-{pid}-{granular}"
            loaded = st.session_state.get(cache_key)
            if not loaded:
                result = daily_loader(pid, "daily" if granular == "日K" else "weekly")
                loaded = result if result and result.get("ok") else {}
                st.session_state[cache_key] = loaded
            if loaded.get("candles"):
                _kline(loaded["candles"])

        #: 均线图例 + 操作提示（颜色与 `_kline_spec` 里的两条线一致）。
        st.markdown(
            "<div style='font-size:.75rem;color:#94a3b8;'>"
            "<span style='color:#f59e0b;'>━</span> MA5 "
            "<span style='color:#3b82f6;'>━</span> MA20 · 滚轮缩放 · 拖拽平移</div>",
            unsafe_allow_html=True)
        #: 区间文字保留：K 线看的是走势，这两个数字说清「最坏 / 最好能到什么程度」。
        st.markdown(
            "<div style='display:flex;justify-content:space-between;font-size:.8rem;'>"
            f"<span style='color:#22c55e;'>▼ 参考下沿 {html.escape(str(item.get('max_loss_pct')))}%</span>"
            f"<span style='color:#ef4444;'>▲ 参考上沿 {html.escape(str(item.get('max_gain_pct')))}%</span>"
            "</div>", unsafe_allow_html=True)

        amount = st.number_input(
            "金额（元）", min_value=floor, value=floor, step=100,
            key=f"{key_prefix}-amt-{pid}", label_visibility="collapsed", disabled=readonly)
        #: 买不起就别给可点的按钮：`affordable` 由工具层按「起购额 ≤ 储蓄余额」判定。
        #: 老 facts 没有这个键时按"可买"处理，保持向后兼容。
        if item.get("affordable") is False:
            st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch", disabled=True)
            st.markdown("起购金额高于储蓄余额，暂时买不了。")
        elif st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch", disabled=readonly):
            ask(f"买 {item.get('name')} {int(amount)} 元")


def render_turn(turn: Mapping[str, Any], *, ask: Callable[[str], None],
                key_prefix: str = "wealth",
                daily_loader: Callable[[str], Mapping[str, Any]] | None = None, readonly: bool = False) -> None:
    """紧贴成功的推荐回执，渲染四象限入口及每款产品的订购按钮。"""
    #: `wealth_holdings` 也要接：看持仓详情同样要求先测评（T21/T26 都要），
    #: 只在报错时给入口；正常的持仓列表不该画推荐卡。
    if turn.get('intent') not in ('wealth_recommend', 'wealth_holdings'):
        return
    if turn.get('error_code') == 'INVALID_STATE':
        #: 未测评 → 不要只留一句报错，直接给入口（点一下就进问卷）。
        st.markdown('#### 先做一次风险测评')
        st.markdown('推荐要按您的风险承受能力筛选，所以得先测评（约 1 分钟）。'
                    '结果只保存在本次会话，不会上传。')
        if st.button('开始风险测评', key=f'{key_prefix}-assess', type='primary', width='stretch', disabled=readonly):
            ask('做风险测评')
        return
    if turn.get('intent') != 'wealth_recommend' or turn.get('error_code'):
        return
    items = (turn.get('facts') or {}).get('items') or []
    if not items:
        return
    st.markdown('#### 推荐产品 · 可直接订购（按产品大类分组）')
    st.markdown('**模拟区间说明：** 红绿区间仅为演示规则的示意估算，'
                '不代表历史极值、实际最大亏损或收益承诺。订购仍需经过风险与余额校验，并在确认卡确认。')
    #: 查看全部产品必须走编排层的公开目录查询，不把超风险等级的产品伪装成推荐。
    if st.button('查看全部产品 →', key=f'{key_prefix}-all', width='stretch', type='primary', disabled=readonly):
        ask('看看全部理财产品')

    #: 默认只铺前 `PREVIEW_COUNT` 张卡（25 张会刷满好几屏），其余折叠在按钮后面。
    #: **按产品大类分组铺卡**（2026-10-29 用户要求"推荐必须分类，不要毫无规律扔出来"）：
    #: 一组一个标题 + 该组内的 2 列网格；预览模式先铺前 N 只（可能截断某个组），
    #: 组序稳健 → 进取，组内保持 facts 原序（收益降序）。
    expanded_key = f'{key_prefix}-expanded'
    expanded = bool(st.session_state.get(expanded_key))
    shown = items if expanded else items[:_PREVIEW_COUNT]
    for category, group in wealth_groups(shown):
        st.markdown(f'**{category_label(category)}**')
        for start in range(0, len(group), _COLUMNS):
            cols = st.columns(_COLUMNS)
            for col, item in zip(cols, group[start:start + _COLUMNS]):
                with col:
                    _card(item, ask=ask, key_prefix=key_prefix,
                          daily_loader=daily_loader, readonly=readonly)
    if len(items) > _PREVIEW_COUNT:
        label = (f'收起，只看前 {_PREVIEW_COUNT} 只' if expanded
                 else f'展开其余 {len(items) - _PREVIEW_COUNT} 只产品')
        if st.button(label, key=f'{key_prefix}-toggle', width='stretch'):
            st.session_state[expanded_key] = not expanded
            st.rerun()


def render(turns: list[Mapping[str, Any]], *, ask: Callable[[str], None],
           key_prefix: str = 'wealth') -> None:
    """兼容旧调用：仅渲染最新一次有明细的成功推荐。"""
    turn = latest_recommendation(turns)
    if turn is not None:
        render_turn(turn, ask=ask, key_prefix=key_prefix)
