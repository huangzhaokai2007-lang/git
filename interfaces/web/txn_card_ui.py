"""F02 流水查询的**详情卡**：按日走势置顶 + 一张带流水号/交易状态的高级明细表。

为什么单开一个文件：`insight_card_ui.py` 已 217 行（CLAUDE.md 红线 300），而这张卡是
F02 的门面 —— 照 `interfaces/web/payee_detail_ui.py`（卡 22）的先例：详情页自成一 file，
`insight_card_ui._draw_txns` 只做一行转发，老调用点与老页面桩一个都不改。

布局（2026-10-26 二次验收口径，用户原话："曲线图放最前面，前面这些删了"）：
1. **按日支出走势**置顶 —— 打开卡片先看见这一个月的钱怎么出去的；
2. **一张高级明细表**：时间 / 流水号 / 交易对手（渠道）/ 分类 / 交易状态 / 金额 / 余额，
   一笔不截断地铺满整个查询区间。旧版的四指标、分类占比条、大额榜整块移除
   （数字仍全在 facts 里，谁要复用照抄即可）。

数据纪律（与 `insight_card_ui` 同源，铁律 2）：
- 卡片上的每一笔金额、每一个流水号、每一个状态都**照抄** `Turn.facts["items"]` ——
  由工具层 `tools/_txn_insight` 一次算好放进来，**界面不求和、不排序、不补数**；
- 界面唯一的动作是**查表改名**（`status` 机器值 → 中文状态词）与纯格式化；
- facts 里没有的键 → **什么都不画**（返回 False 让上层退回纯文本），不画空图、不补零。
"""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

import streamlit as st
from interfaces.web import chart_ui

#: 状态机器值 → 展示词 + 徽标配色（绿=完成 / 琥珀=处理中 / 红=失败 / 蓝=退款）。
_STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "done": ("已完成", "#34d399", "rgba(52,211,153,.13)"),
    "pending": ("待处理", "#fbbf24", "rgba(251,191,36,.13)"),
    "failed": ("失败", "#f87171", "rgba(248,113,113,.13)"),
    "refunded": ("退款", "#60a5fa", "rgba(96,165,250,.13)"),
}
_STATUS_FALLBACK = ("—", "#8b93a7", "rgba(139,147,167,.13)")

#: 走势图只画支出曲线：收入（工资）单笔就 9,500 元，画在一张图上会把日常支出的起伏压成一条直线。
TREND_HEIGHT = 240

#: 明细表的滚动高度：区间动辄几十笔，表格自身滚，页面不被撑爆。
TABLE_MAX_HEIGHT = 620

_STYLE = """
<style>
.txn-wrap {border:1px solid #262c3d; border-radius:14px; background:#0e1120;
           padding:4px 6px; max-height:__H__px; overflow:auto;}
.txn-tbl {width:100%; border-collapse:collapse; font-size:13.5px; line-height:1.45;}
.txn-tbl thead th {position:sticky; top:0; background:#12162a; color:#8b93a7; z-index:1;
                   font-size:11.5px; letter-spacing:.08em; text-align:left;
                   padding:10px 12px; border-bottom:1px solid #262c3d; white-space:nowrap;}
.txn-tbl tbody td {padding:10px 12px; border-bottom:1px solid #1a1f30; white-space:nowrap;
                   color:#dde3f0;}
.txn-tbl tbody tr:nth-child(even) td {background:#101426;}
.txn-tbl tbody tr:hover td {background:#1c2440;}
.txn-bar {display:flex; align-items:center; justify-content:space-between; gap:12px;
          padding:10px 12px 8px; border-bottom:1px solid #262c3d;}
.txn-bar h4 {margin:0; font-size:13px; font-weight:600; color:#e6ebf7; letter-spacing:.02em;}
.txn-bar span {font-size:11.5px; color:#7c86a2;}
.txn-ref {font-family:ui-monospace,Menlo,monospace; font-size:12px; color:#7c86a2;
          letter-spacing:.03em;}
.txn-cp {font-weight:600;}
.txn-ch {font-size:11.5px; color:#7c86a2; margin-top:2px;}
.txn-chip {display:inline-block; padding:2px 9px; border-radius:999px; font-size:11.5px;
           background:#1b2136; color:#a6b1cc; border:1px solid #262c3d;}
.txn-pill {display:inline-block; padding:2.5px 10px; border-radius:999px; font-size:11.5px;
           font-weight:600; border:1px solid transparent;}
.txn-in {color:#34d399; font-variant-numeric:tabular-nums;}
.txn-out {color:#f0f2f8; font-variant-numeric:tabular-nums;}
.txn-bal {color:#8b93a7; font-variant-numeric:tabular-nums;}
.txn-tbl td.num, .txn-tbl th.num {text-align:right;}
</style>
""".replace("__H__", str(TABLE_MAX_HEIGHT))


def _num(yuan: object) -> float:
    """展示用坐标：事实包里的「元」串（`4,370.00`）去千分位 → 数值。纯格式化，不重算金额。"""
    try:
        return float(str(yuan).replace(",", ""))
    except ValueError:
        return 0.0


def _pill(status: object) -> str:
    """交易状态 → 徽标 HTML（查表改名 + 配色；不认识的值灰色兜底，绝不编一个状态出来）。"""
    label, color, bg = _STATUS_STYLES.get(str(status or ""), _STATUS_FALLBACK)
    return (f'<span class="txn-pill" style="color:{color};background:{bg};'
            f'border-color:{color}44">{escape(label)}</span>')


def _cell(item: Mapping[str, Any], key: str) -> str:
    """文本单元格：空值占位 `—`，竖线/尖括号转义（memo 类字段不可信，铁律 7）。"""
    return escape(str(item.get(key) or "").strip()) or "—"


def _row(item: Mapping[str, Any]) -> str:
    """一笔流水一行：时间 / 流水号 / 对手方（渠道）/ 分类 / 状态 / 金额 / 余额。"""
    moment = escape(str(item.get("ts") or "")[:16].replace("T", " ")) or "—"
    amount = str(item.get("amount_yuan") or "—")
    cls = "txn-in" if not amount.startswith("-") else "txn-out"
    after = str(item.get("balance_after_yuan") or "—")
    return (
        "<tr>"
        f"<td>{moment}</td>"
        f'<td class="txn-ref">{_cell(item, "ref_no")}</td>'
        f'<td><div class="txn-cp">{_cell(item, "counterparty")}</div>'
        f'<div class="txn-ch">{escape(str(item.get("channel") or "") or "—")}</div></td>'
        f'<td><span class="txn-chip">{_cell(item, "category")}</span></td>'
        f"<td>{_pill(item.get('status'))}</td>"
        f'<td class="num {cls}">{escape(amount)}</td>'
        f'<td class="num txn-bal">{escape(after)}</td>'
        "</tr>"
    )


def _trend(facts: Mapping[str, Any]) -> None:
    """**置顶**的按日支出走势（`daily[{date, out_yuan}]`，工具层给好；界面只做格式化）。"""
    daily = list(facts.get("daily") or [])
    if len(daily) < 2:  # 只有一天的数据画不出走势 → 宁可不画，也不画一根孤零零的点
        return
    st.caption("**按日支出走势**（每日支出合计，取自事实包）：")
    chart_ui.trend({str(item["date"]): _num(item["out_yuan"]) for item in daily},
                   height=TREND_HEIGHT)


def _ledger(facts: Mapping[str, Any]) -> None:
    """高级明细表：`items` 一笔不少、一列不编 —— 流水号与交易状态是这版的主体。

    表头栏只写**笔数**（`returned_count`，facts 原值）和一个"滚动查看"的提示：
    几十笔流水全铺开，没有这句用户会以为又只列了一部分。
    """
    items = list(facts.get("items") or [])
    count = facts.get("returned_count") or len(items)
    head = ("<tr><th>时间</th><th>流水号</th><th>交易对手</th><th>分类</th>"
            "<th>交易状态</th><th class=\"num\">金额（元）</th>"
            "<th class=\"num\">交易后余额（元）</th></tr>")
    body = "".join(_row(item) for item in items)
    bar = (f'<div class="txn-bar"><h4>逐笔明细</h4>'
           f'<span>共 {escape(str(count))} 笔 · 上下滚动查看全部</span></div>')
    st.markdown(
        _STYLE
        + f'<div class="txn-wrap">{bar}<table class="txn-tbl"><thead>{head}</thead>'
        + f"<tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def render(facts: Mapping[str, Any], *, key: str) -> bool:
    """画流水详情卡；facts 不是流水形状（没有 items）→ `False`，上层退回原回执。"""
    if not list(facts.get("items") or []):
        return False
    _trend(facts)
    _ledger(facts)
    return True
