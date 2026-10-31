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
    return (wick + body + ma5 + ma20).properties(height=260).interactive()


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
        #: **年化做主视觉**（1.55rem 加粗）—— 理财卡第一眼要看的就是它；
        #: 原来五段挤一行、同字号，用户得读完整行才知道收益多少，窄屏还会折得难看。
        #: 风险等级做成中性描边徽章：**不用红绿**，否则和「红涨绿跌」打架。
        st.markdown(
            "<div style='display:flex;align-items:baseline;gap:.45rem;margin:.1rem 0 .15rem;'>"
            f"<span style='font-size:1.55rem;font-weight:700;line-height:1;'>"
            f"{html.escape(str(item.get('yield')))}</span>"
            "<span style='font-size:.75rem;color:#94a3b8;'>预期年化</span></div>"
            "<div style='font-size:.8rem;color:#94a3b8;'>"
            "<span style='display:inline-block;padding:.02rem .4rem;margin-right:.4rem;"
            "border:1px solid rgba(148,163,184,.55);border-radius:999px;font-size:.72rem;'>"
            f"{html.escape(str(item.get('risk_label') or '风险待核实'))}</span>"
            f"{html.escape(str(item.get('kind_label') or '未分类'))} · "
            f"{html.escape(str(item.get('term')))} 天 · "
            f"起购 {html.escape(str(item.get('min_amount_yuan')))} 元</div>",
            unsafe_allow_html=True)

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
        #: **区间刻度条**：把「下沿 / 近一年 / 上沿」画在同一条上 —— 光给两个端点数字，
        #: 用户不知道当前站在哪。三个数都来自 facts（年度口径，见 `_current_pct` 的注释），
        #: 这里只把百分数映射成像素（和 K 线把价格映射成纵坐标是一回事，不做业务换算）。
        lo = _num(str(item.get("max_loss_pct", "0")).replace("+", ""))
        hi = _num(str(item.get("max_gain_pct", "0")).replace("+", ""))
        cur = _num(str(item.get("current_pct", "+0")).replace("+", ""))
        pos = 0.0 if hi <= lo else max(0.0, min(1.0, (cur - lo) / (hi - lo))) * 100
        label_pos = max(12.0, min(88.0, pos))
        st.markdown(
            "<div style='position:relative;height:30px;margin:.45rem 0 .1rem;'>"
            "<div style='position:absolute;left:0;right:0;top:5px;height:8px;border-radius:4px;"
            "background:linear-gradient(90deg,rgba(34,197,94,.45),rgba(148,163,184,.25),rgba(239,68,68,.45));'></div>"
            f"<div style='position:absolute;left:{pos:.1f}%;top:1px;width:2px;height:16px;"
            "background:#64748b;transform:translateX(-1px);'></div>"
            f"<div style='position:absolute;left:{label_pos:.1f}%;top:17px;transform:translateX(-50%);"
            "font-size:.7rem;color:#64748b;white-space:nowrap;'>"
            f"近一年 {html.escape(str(item.get('current_pct')))}%</div>"
            f"<div style='position:absolute;left:0;top:17px;font-size:.7rem;color:#94a3b8;'>"
            f"参考下沿 {html.escape(str(item.get('max_loss_pct')))}%</div>"
            f"<div style='position:absolute;right:0;top:17px;font-size:.7rem;color:#94a3b8;'>"
            f"参考上沿 {html.escape(str(item.get('max_gain_pct')))}%</div>"
            "</div>", unsafe_allow_html=True)

        amount = st.number_input(
            "金额（元）", min_value=floor, value=floor, step=100,
            key=f"{key_prefix}-amt-{pid}", label_visibility="collapsed", disabled=readonly)
        #: 收益标尺：**每万元年收益**（工具层算好的，界面只排版）——
        #: 光给「8.20%」用户没体感，给个「一万块一年约 820 元」才直观。
        if item.get("per_10k_year_yuan"):
            st.markdown(
                f"<div style='font-size:.72rem;color:#94a3b8;margin:-.45rem 0 .35rem;'>"
                f"每万元年收益约 {html.escape(str(item['per_10k_year_yuan']))} 元（模拟）</div>",
                unsafe_allow_html=True)

        #: 买不起就别给可点的按钮：`affordable` 由工具层按「起购额 ≤ 储蓄余额」判定。
        #: 老 facts 没有这个键时按"可买"处理，保持向后兼容。
        if item.get("affordable") is False:
            st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch", disabled=True)
            st.markdown("起购金额高于储蓄余额，暂时买不了。")
        elif st.button("订购", key=f"{key_prefix}-buy-{pid}", width="stretch", disabled=readonly):
            ask(f"买 {item.get('name')} {int(amount)} 元")


def _profit_color(value: object) -> str:
    """A 股口径：赚了红、亏了绿。零值按灰色处理，避免「+0.00% 却是红的」。"""
    text = str(value or "").replace(",", "").replace("+", "")
    try:
        number = float(text.rstrip("%"))
    except ValueError:
        return "#94a3b8"
    if number > 0:
        return "#c0392b"
    if number < 0:
        return "#2e8b57"
    return "#94a3b8"


def render_holdings(turn: Mapping[str, Any], *, ask: Callable[[str], None], key_prefix: str,
                    daily_loader: Callable[[str, str], Mapping[str, Any]] | None = None) -> None:
    """持仓页：**合计 + 每只一张卡（K 线 / 还能带来多少 / 赎回）**。

    和推荐卡同一套视觉语言 —— 用户在同一条业务线里不该看到两种排版。
    与推荐卡的区别：
    - 金额是**实际持有**，不是起购额；
    - 收益给的是「**还能带来多少**」（按剩余期限算），不是每万元标尺；
    - 有赎回按钮。

    **数据来源**：金额/年化/到期/收益预估全部来自 `list_holdings` 的 facts（铁律 2），
    界面只排版；K 线走 T14b 按需拉取（持仓 facts 不带 candles，避免每次进页面都扛 25 只）。
    """
    facts = turn.get("facts") or {}
    items = facts.get("items") or []
    if not items:
        st.markdown(turn.get("reply") or "您目前没有持有中的理财产品。")
        return

    #: 合计行（数字来自 facts；回执原文不再上屏，避免同一份数据出现两遍）
    st.markdown(
        "<div style='display:flex;align-items:baseline;gap:.5rem;margin:.1rem 0 .5rem;'>"
        f"<span style='font-size:1.5rem;font-weight:700;line-height:1;'>"
        f"{html.escape(str(facts.get('total_amount_yuan', '0.00')))}</span>"
        "<span style='font-size:.78rem;color:#94a3b8;'>持有合计（元）</span>"
        f"<span style='font-size:.78rem;color:#94a3b8;margin-left:.4rem;'>"
        f"{html.escape(str(facts.get('total_count', len(items))))} 只</span></div>",
        unsafe_allow_html=True)
    #: 合计累计收益：颜色按 A 股口径（红盈绿亏），数字来自工具层，界面只上色。
    total_profit = str(facts.get("cum_profit_total_yuan") or "")
    if total_profit:
        st.markdown(
            "<div style='display:flex;align-items:baseline;gap:.4rem;margin:.1rem 0 .55rem;'>"
            f"<span style='font-size:1.15rem;font-weight:700;color:{_profit_color(total_profit)};'>"
            f"{html.escape(total_profit)} 元</span>"
            "<span style='font-size:.75rem;color:#94a3b8;'>累计收益</span></div>",
            unsafe_allow_html=True)

    cols = st.columns(min(len(items), 2))
    for index, item in enumerate(items):
        with cols[index % len(cols)]:
            _holding_card(item, ask=ask, key_prefix=key_prefix, daily_loader=daily_loader)


def _holding_card(item: Mapping[str, Any], *, ask: Callable[[str], None], key_prefix: str,
                  daily_loader: Callable[[str, str], Mapping[str, Any]] | None) -> None:
    """一只持仓一张卡：名称 → 持有金额 → 关键信息 → K 线 → 还能带来多少 → 赎回。"""
    pid = str(item.get("product_id"))
    with st.container(border=True):
        st.markdown(f"**{item.get('name')}**")
        st.markdown(
            "<div style='display:flex;align-items:baseline;gap:.45rem;margin:.1rem 0 .15rem;'>"
            f"<span style='font-size:1.55rem;font-weight:700;line-height:1;'>"
            f"{html.escape(str(item.get('amount_yuan')))}</span>"
            "<span style='font-size:.75rem;color:#94a3b8;'>持有金额（元）</span></div>"
            #: **累计收益**：用户最想看的数字，放在持有金额正下方、加粗上色。
            #: 正负号和颜色都由工具层的 `cum_is_profit` 决定，界面不判断业务含义。
            + (f"<div style='display:flex;align-items:baseline;gap:.35rem;margin:.05rem 0 .1rem;'>"
               f"<span style='font-size:1.05rem;font-weight:700;color:{_profit_color(item.get('cum_profit_yuan'))};'>"
               f"{html.escape(str(item.get('cum_profit_yuan')))} 元</span>"
               f"<span style='font-size:.75rem;color:{_profit_color(item.get('cum_profit_yuan'))};'>"
               f"{html.escape(str(item.get('cum_profit_pct')))}</span>"
               "<span style='font-size:.7rem;color:#94a3b8;'>累计收益</span></div>"
               if item.get("cum_profit_yuan") else "")
            + "<div style='font-size:.8rem;color:#94a3b8;'>"
            f"年化 {html.escape(str(item.get('yield')))} · "
            f"买入 {html.escape(str(item.get('purchase_date')))} · "
            f"{html.escape(_term_text(item))}</div>",
            unsafe_allow_html=True)

        #: K 线：持仓 facts 不带 candles，按需拉月 K 并缓存（和推荐卡同一个 loader）。
        cache_key = f"{key_prefix}-hk-{pid}"
        loaded = st.session_state.get(cache_key)
        if loaded is None and daily_loader is not None:
            result = daily_loader(pid, "monthly")
            loaded = result if result and result.get("ok") else {}
            st.session_state[cache_key] = loaded
        if loaded and loaded.get("candles"):
            _kline(loaded["candles"])

        #: 「还能带来多少」：数字来自工具层（`to_maturity_income_yuan` / `annual_income_yuan`），
        #: 界面只选一个显示，不做乘法。已到期的显示「已到期」，不显示 0.00 元。
        remaining = int(item.get("remaining_days") or 0)
        if item.get("maturity_date") and remaining == 0:
            line = "<span style='color:#94a3b8;'>已到期，随时可赎</span>"
        elif item.get("to_maturity_income_yuan"):
            line = (f"持有到期预计还能带来约 <b>{html.escape(str(item['to_maturity_income_yuan']))} 元</b>"
                    f"（剩余 {remaining} 天 · 模拟）")
        else:
            line = (f"持有一年预计带来约 <b>{html.escape(str(item.get('annual_income_yuan', '0.00')))} 元</b>"
                    "（活期 · 模拟）")
        st.markdown(f"<div style='font-size:.78rem;color:#64748b;margin:.1rem 0 .45rem;'>{line}</div>",
                    unsafe_allow_html=True)

        if st.button("赎回", key=f"{key_prefix}-redeem-{item.get('holding_id') or pid}",
                     width="stretch"):
            ask(f"赎回 {item.get('name')}")


def _term_text(item: Mapping[str, Any]) -> str:
    """期限的中文说法（纯文案，不含业务数字）。"""
    days = int(item.get("term_days") or 0)
    if days <= 0:
        return "活期"
    if days % 365 == 0:
        return f"{days // 365} 年期"
    if days % 30 == 0:
        return f"{days // 30} 个月期"
    return f"{days} 天期"


#: 期限筛选的档位（顺序即界面顺序）
TERM_CHOICES = ("全部", "1 年内", "1–3 年", "3 年以上")
#: 排序口径：收益降序是工具层给的默认序，这里只在用户要求时换一种看法
SORT_CHOICES = ("收益 ↓", "期限 ↑")


def filter_by_term(items: list[Mapping[str, Any]], choice: str) -> list[Mapping[str, Any]]:
    """按期限档位筛「工具已返回的 items」。

    **纯视图筛选**：不重新查工具、不动风险上限（那是工具层的职责）。
    活期产品 `term=0` 归入「1 年内」—— 它确实随时能取。
    """
    def keep(term: object) -> bool:
        days = int(term or 0)
        if choice == "1 年内":
            return days <= 365
        if choice == "1–3 年":
            return 365 < days <= 1095
        if choice == "3 年以上":
            return days > 1095
        return True

    return [item for item in items if keep(item.get("term"))]


def order_items(items: list[Mapping[str, Any]], choice: str) -> list[Mapping[str, Any]]:
    """`期限 ↑` 时按天数升序；其余保持工具层给的收益降序。"""
    if choice == "期限 ↑":
        return sorted(items, key=lambda row: int(row.get("term") or 0))
    return list(items)


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
    #: **视图筛选**：只筛「工具已经返回的 items」，不重新查工具。
    #: 风险上限由工具层保证（列表里本来就不会有超过用户等级的），
    #: 所以这里只给「按期限找货 + 换个排序」——纯视图，不动业务口径。
    fcol1, fcol2 = st.columns(2)
    term_choice = fcol1.radio(
        '期限', list(TERM_CHOICES), horizontal=True,
        key=f'{key_prefix}-term', label_visibility='collapsed')
    sort_choice = fcol2.radio(
        '排序', list(SORT_CHOICES), horizontal=True,
        key=f'{key_prefix}-sort', label_visibility='collapsed')

    filtered = order_items(filter_by_term(items, term_choice), sort_choice)
    if not filtered:
        st.info('这个期限档位下暂时没有产品，换个档位看看。')
        return

    expanded_key = f'{key_prefix}-expanded'
    expanded = bool(st.session_state.get(expanded_key))
    shown = filtered if expanded else filtered[:_PREVIEW_COUNT]
    for category, group in wealth_groups(shown):
        st.markdown(f'#### {category_label(category)}')
        for start in range(0, len(group), _COLUMNS):
            cols = st.columns(_COLUMNS)
            for col, item in zip(cols, group[start:start + _COLUMNS]):
                with col:
                    _card(item, ask=ask, key_prefix=key_prefix,
                          daily_loader=daily_loader, readonly=readonly)
    if len(filtered) > _PREVIEW_COUNT:
        label = (f'收起，只看前 {_PREVIEW_COUNT} 只' if expanded
                 else f'展开其余 {len(filtered) - _PREVIEW_COUNT} 只产品')
        if st.button(label, key=f'{key_prefix}-toggle', width='stretch'):
            st.session_state[expanded_key] = not expanded
            st.rerun()


def render(turns: list[Mapping[str, Any]], *, ask: Callable[[str], None],
           key_prefix: str = 'wealth') -> None:
    """兼容旧调用：仅渲染最新一次有明细的成功推荐。"""
    turn = latest_recommendation(turns)
    if turn is not None:
        render_turn(turn, ask=ask, key_prefix=key_prefix)
