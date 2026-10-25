"""F02 流水查询的**详情卡**：分类汇总 + 逐笔可展开 + 按日走势。

为什么单开一个文件：`insight_card_ui.py` 已 217 行（CLAUDE.md 红线 300），而这张卡要放
指标、分类折叠、日线、大额榜、全量明细五块内容 —— 照 `interfaces/web/payee_detail_ui.py`
（卡 22）与 `interfaces/web/app_offline.py`（卡 16）的先例：详情页自成一 file，
`insight_card_ui._draw_txns` 只做一行转发，老调用点与老页面桩一个都不改。

数据纪律（与 `insight_card_ui` 同源，铁律 2）：
- 卡片上的每一个数字、每一个占比、每一笔金额都**照抄** `Turn.facts` —— 分类分组与日序
  由工具层 `tools/_txn_insight` 一次算好放进来，**界面不求和、不排序、不补数**；
- 界面唯一的动作是**按分类筛选**（纯过滤，不产生新数字）；
- facts 里没有的键 → **什么都不画**（返回 False 让上层退回纯文本），不画空图、不补零。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import streamlit as st
from interfaces.web import chart_ui

#: 分类折叠里每个分类最多列多少笔（其余明确说“还有 N 笔”，绝不静默截断）。
PER_GROUP_ROWS = 20

#: 走势图只画支出曲线：收入（工资）单笔就 9,500 元，画在一张图上会把日常支出的起伏压成一条直线，
#: 等于没画。收入另有 ROC 指标 Total卡片里已经并列给出，不靠这张图交代。
TREND_HEIGHT = 220


def _num(yuan: object) -> float:
    """展示用坐标：事实包里的「元」串（`4,370.00`）去千分位 → 数值。纯格式化，不重算金额。"""
    try:
        return float(str(yuan).replace(",", ""))
    except ValueError:
        return 0.0


def _row(item: Mapping[str, Any]) -> str:
    """一笔流水一行（Markdown 表格行）：时间 / 对手方 / 渠道 / 金额 / 这笔之后余额。"""
    moment = str(item.get("ts") or "")[:16].replace("T", " ")
    rows = [moment or "—", str(item.get("counterparty") or "—").replace("|", "/"),
            str(item.get("channel") or "—").replace("|", "/"),
            str(item.get("amount_yuan") or "—"),
            str(item.get("balance_after_yuan") or "—")]
    return "| " + " | ".join(rows) + " |"


def _table(items: Sequence[Mapping[str, Any]]) -> str:
    head = "| 时间 | 交易对手 | 渠道 | 金额（元） | 交易后余额（元） |"
    sep = "| --- | --- | --- | --- | --- |"
    return "\n".join([head, sep, *(_row(item) for item in items)])


def _metrics(facts: Mapping[str, Any]) -> None:
    """四个指标并排：支出合计 / 收入合计 / 收支净额 / 笔数（全部是 facts 原串）。"""
    columns = st.columns(4)
    columns[0].metric("支出合计（元）", str(facts.get("out_sum_yuan") or "—"))
    columns[1].metric("收入合计（元）", str(facts.get("in_sum_yuan") or "—"))
    columns[2].metric("收支净额（元）", str(facts.get("net_yuan") or "—"))
    columns[3].metric("交易笔数", str(facts.get("total_count") or "—"),
                      f"支出 {facts.get('out_count', 0)} / 收入 {facts.get('in_count', 0)}")


def _trend(facts: Mapping[str, Any]) -> None:
    """按日支出走势（`daily[{date, out_yuan}]`，工具层给好；界面只做格式化，不改数）。"""
    daily = list(facts.get("daily") or [])
    if len(daily) < 2:  # 只有一天的数据画不出走势 → 宁可不画，也不画一根孤零零的点
        return
    st.caption("**按日支出走势**（每日支出合计，取自事实包）：")
    chart_ui.trend({str(item["date"]): _num(item["out_yuan"]) for item in daily}, height=TREND_HEIGHT)


def _categories(facts: Mapping[str, Any], *, key: str) -> bool:
    """**分类折叠** —— 每个分类一块：占比条 + 「逐笔展开」折叠面板。

    这是 F02 的本体：用户查流水不是为了看一个合计数，是为了**按类别看懂钱去哪了**，
    并能在某个类别里翻到具体那几笔 —— 追问异常、要求退款都是从这里起手。
    """
    groups = list(facts.get("categories") or [])
    items = list(facts.get("items") or [])
    if not groups:
        return False
    st.caption(f"**按分类汇总**（共 {len(groups)} 类，笔数与占比由工具层算好）：")
    for index, group in enumerate(groups):
        label = group.get("key") or "其他"
        amount = str(group.get("amount_yuan") or "—")
        pct = int(group.get("pct") or 0)
        left, right = st.columns([7, 3])
        left.progress(max(0, min(pct, 100)) / 100,
                      text=f"{label}　{group.get('count', 0)} 笔　{amount} 元")
        right.markdown(f"<div style='text-align:right;font-size:13px;color:#7d8db0'>占比 {pct}%</div>",
                       unsafe_allow_html=True)
        members = [item for item in items if (item.get("category") or "") == label]
        if not members:
            continue
        with st.expander(f"展开「{label}」的 {len(members)} 笔明细", expanded=False):
            st.markdown(_table(members[:PER_GROUP_ROWS]))
            if len(members) > PER_GROUP_ROWS:
                st.caption(f"（这里列出前 {PER_GROUP_ROWS} 笔，其余 {len(members) - PER_GROUP_ROWS} 笔见下方全量明细。）")
    return True


def _largest(facts: Mapping[str, Any]) -> None:
    """金额最大的几笔（单列一块）：排查时人手一个出口。"""
    largest = list(facts.get("largest") or [])
    if not largest:
        return
    with st.expander(f"💸 金额最大的 {len(largest)} 笔", expanded=True):
        st.markdown(_table(largest))


def _all_items(facts: Mapping[str, Any]) -> None:
    """全量明细折叠：`items` 一笔不少地铺出来（回执只列一部分，这里补齐）。"""
    items = list(facts.get("items") or [])
    if len(items) <= PER_GROUP_ROWS:
        return
    with st.expander(f"📄 查看本区间全部 {len(items)} 笔明细", expanded=False):
        st.markdown(_table(items))


def render(facts: Mapping[str, Any], *, key: str) -> bool:
    """画流水详情卡；facts 不是流水形状（没有 items）→ `False`，上层退回原回执。"""
    if not list(facts.get("items") or []):
        return False
    _metrics(facts)
    _trend(facts)
    drawn = _categories(facts, key=key)
    _largest(facts)
    _all_items(facts)
    return True
