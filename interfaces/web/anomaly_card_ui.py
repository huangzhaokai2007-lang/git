"""异常检测**详情卡**（F03）—— 从 `insight_card_ui` 迁出（照 `txn_card_ui` 的先例）。

迁出的直接原因：2026-10-27 追加「依据分档 + 自适应结论 + 沿期追问」后，`insight_card_ui`
会撞 300 行红线（当时 261 行，加完约 310 行）。职责上也该分家：四类依据怎么分档、
结论怎么组织，是异常分析自己的事，不该和余额/流水/账单三张卡共用一个抽屉。

三条铁律照 `insight_card_ui` 原样继承：
- 每个数字都来自 `Turn.facts`（`scanned_count` / `anomaly_count` / `period` / `items[]`），
  界面**不重算、不排序、不补零**；
- facts 缺该画的键 → 什么都不画（宁可朴素，也不许拿半截数据凑图）；
- 卡片只做**归类与呈现**，一条判定都不改（判定在 `tools/_query_analysis`）。

卡片的三个巧思（全是零新增数字的呈现层功夫 —— 因为用户问的是"这个月主要是哪类问题"，
而不是"再给我一排数字"）：
1. **依据分布图**：四类依据画成柱图，"哪类最多"看一眼就知道，比读四个数字快；
2. **自适应结论句**：卡片会看构成说话 —— 一类独大说「集中在这一处」，多类并存说
   「分散在 N 类、以谁为主」；不同月份进来，卡片说的是不同的话；
3. **追问按钮跟着时间段走**：问「四月份的异常」→ 按钮是「看这段时间的流水」，
   点下去查的是四月，而不是永远跳回「上个月」。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import streamlit as st
from agent.render_lists import SEVERITY_CN       # 严重程度中文档位（agent 层单一出处，界面不另抄）
from interfaces.web import chart_ui

#: 依据的**分档口径**：工具层 `reason` 是「、」拼起来的规则名（冻结文案），
#: 这里只做归类打标，不改判定、不重算。顺序 = 卡片里的展示顺序。
#: 分档的意义：`anomaly_count` 只说"几笔"，用户在图里看到的应当是"哪一类问题、各几笔"。
ANOMALY_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("金额偏高", ("金额显著高于近期均值",)),
    ("时间异常", ("凌晨时段交易",)),
    ("短时密集", ("同商户短时密集交易",)),
    ("陌生商户", ("陌生商户交易",)),
)

#: facts 里没有 `period` 时的追问（不猜用户在问哪段 —— 猜错比问一句更贵）。
_FALLBACK_FOLLOW_UPS: tuple[tuple[str, str], ...] = (
    ("💰 查一下余额", "查一下余额"),
    ("📄 看流水明细", "上个月的流水"),
    ("🧾 出一份账单总结", "上个月的账单总结"),
)


def kinds(items: Sequence[Mapping[str, Any]]) -> list[tuple[str, int]]:
    """按事实包里的 `reason` 归类计数：`[(档名, 条数)]`，只留真的命中的档（不补零）。

    一笔可能同时命中多档（`reason` 里是「、」拼接），故各档之和可以多于 `anomaly_count` ——
    这是**规则条数**，不是交易笔数；卡片上会写明，避免用户拿它对不上笔数。
    """
    counted: list[tuple[str, int]] = []
    for label, keys in ANOMALY_KINDS:
        hits = sum(1 for item in items if any(key in str(item.get("reason") or "") for key in keys))
        if hits:
            counted.append((label, hits))
    return counted


def summary(counted: Sequence[tuple[str, int]], total: int) -> str:
    """本期风险的**整句结论**：把已有分档组织成一句话（不新增任何一个数字）。

    为什么要这句：`scanned_count` / `anomaly_count` 是两个孤立数字，用户还得自己
    去图里对比才知道"主要是哪类问题"。卡片替他把话说完整 ——
    一类独大说「集中」，多类并存说「分散、以谁为主」，**不同月份进来结论不同**。
    """
    if not counted:
        return ""
    top_label, top_hits = max(counted, key=lambda pair: pair[1])
    if len(counted) == 1:
        return (f"本期 {total} 笔可疑交易，依据全部指向「{top_label}」"
                "—— 这段时间的问题就集中在这一处。")
    return (f"本期 {total} 笔可疑交易，风险分散在 {len(counted)} 类依据上，"
            f"以「{top_label}」为主（{top_hits} 条）。")


def follow_ups(facts: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """追问按钮**沿用用户刚问的那段时间**（而不是一律「上个月」）。

    以前按钮硬编码「上个月的流水」：问完「四月份的异常分析」再点「看流水明细」，
    跳到的是上个月的流水 —— 用户以为在看四月那几笔的上下文，实际看的是另一个月。
    按钮文案仍说「这段时间」（按钮要短），送进编排层的那句话则带上真实账期
    （如「2026-04的流水」），走的是同一条流水线，不绕过任何一层。
    """
    period = str(facts.get("period") or "").strip()
    if not period:
        return _FALLBACK_FOLLOW_UPS
    return (("💰 查一下余额", "查一下余额"),
            ("📄 看这段时间的流水", f"{period}的流水"),
            ("🧾 这段时间的账单总结", f"{period}的账单总结"))


def render(facts: Mapping[str, Any], *, key: str) -> None:
    """F03：指标 → 依据分布图 → 结论句 → 命中清单（数字一律取自 facts，界面不数）。

    2026-10-27 验收口径：异常依据不止「金额远高于均值」，还有凌晨时段、同商户短时密集、
    陌生商户四条规则（工具层早就都实现了，只是在卡片里全挤在「依据」一列，看不出层次）。
    所以先分档、再画图、最后给结论，让"这个月主要是哪类问题"一眼可见。
    扫描笔数为 0 时（账期无数据）不多画 —— 没扫到东西就没有"问题类型"可谈。
    """
    items = list(facts.get("items") or [])
    cols = st.columns(3)
    cols[0].metric("扫描笔数", str(facts.get("scanned_count") or "—"))
    cols[1].metric("命中可疑笔数", str(facts.get("anomaly_count") or "—"))
    if not items:
        return
    counted = kinds(items)
    cols[2].metric("涉及依据", f"{len(counted)} 类" if counted else "—")
    if not counted:
        return
    st.caption("按依据分档（一笔可能同时命中多条规则，故各档之和可多于命中笔数）：")
    chart_ui.bar(list(counted), height=180)
    #: 结论句用**正文**（不是 caption）：它是这张卡最该被看到的一句话 ——
    #: 图下面那行灰字是图例说明，结论是给用户读的答复，两者不是一个分量。
    st.markdown(summary(counted, facts.get("anomaly_count") or 0))
    rows = [{"时间": str(item.get("ts"))[:16].replace("T", " "),
             "交易对手": item.get("counterparty") or "—",
             "金额（元）": item.get("amount_yuan"),
             "等级": SEVERITY_CN.get(str(item.get("severity") or ""), str(item.get("severity") or "—")),
             "依据": item.get("reason")}
            for item in items]
    st.dataframe(rows, hide_index=True, width="stretch")
