"""对话内**富卡片**（图表 + 追问按钮）—— 画在助手气泡里，数据一律来自 `Turn.facts`。

为什么有这个模块（用户的验收口径是「在对话框里看到内容」）：
- 老链路只 `st.markdown(turn.reply)`：五张任务卡（余额 / 流水 / 账单分析 / 异常 / 账单总结）
  在对话框里都只是一段**纯文字**，既没有图形也没有可点的东西；
- 图表此前只出现在「📊 账单图表」页，而且坐标是**正则从回执文案里反解**出来的
  （`components.parse_bill`）—— 改一句措辞就断链，这是最脆的一环。

数据来源的纪律（与 `components.py` 同源，铁律 2）：
- 坐标全部**照抄** `Turn.facts` 里工具层已经算好、且回执里已经印出来的那些值
  （`amount_yuan` / `out_sum_yuan` / `balance_yuan` …），**只做去千分位的格式化**；
- 界面**不求和、不对比、不排序、不补数**：分组图表直接用工具层给的 `groups[]`，
  流水图每根柱子就是一笔流水的 `amount_yuan`，一个数字都不重算。

追问按钮的口径（照 `payee_card_ui._dispatch` 的先例）：
- 点一下 = **替用户说一句完整的话**，仍然走 `orchestrator.handle` 同一条流水线
  （意图识别 → 槽位 → 权限 → 工具 → 数字校验 → 回执），不绕过任何一层；
- 措辞刻意取离线替身（`app/cli.py: OFFLINE_RULES`）也能命中的说法：
  「余额」「流水」「花了多少」「异常」「账单总结」—— 没配模型 key 时演示同样点得动。
  账期统一说「上个月」：合成数据窗口的末尾就在锚点前一个月，问空账期会得到空回执。

失败即降级：facts 里没有该画的键 → **什么都不画**（返回 False），绝不画空图或补零。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import streamlit as st

#: 有富卡片的意图（其余意图一律不画 —— 宁可朴素，也不许拿半截数据凑图）。
INSIGHT_INTENTS = ("balance_query", "txn_query", "bill_analysis", "anomaly_check", "bill_report")

#: 每个意图的追问按钮：`(按钮文案, 替用户说的那句话)`。
#: 三列排布，文案是**给用户看的动作**，话是**真送进编排层的输入**（两者不同：按钮要短，话要完整）。
FOLLOW_UPS: dict[str, tuple[tuple[str, str], ...]] = {
    "balance_query": (("📄 上个月流水", "上个月的流水"),
                       ("📊 上个月花了多少", "上个月花了多少"),
                       ("🧾 上个月账单总结", "上个月的账单总结")),
    "txn_query": (("📊 按分类分析支出", "上个月花了多少"),
                  ("🧾 出一份账单总结", "上个月的账单总结"),
                  ("🔍 查异常交易", "上个月有没有异常交易")),
    "bill_analysis": (("📄 看流水明细", "上个月的流水"),
                       ("🧾 出一份账单总结", "上个月的账单总结"),
                       ("🔍 查异常交易", "上个月有没有异常交易")),
    "anomaly_check": (("💰 查一下余额", "查一下余额"),
                      ("📄 看流水明细", "上个月的流水"),
                      ("🧾 出一份账单总结", "上个月的账单总结")),
    "bill_report": (("🔍 查异常交易", "上个月有没有异常交易"),
                    ("📊 按分类分析支出", "上个月花了多少"),
                    ("💰 查一下余额", "查一下余额")),
}

#: 流水图最多画多少根柱子（回执表格是 `agent/render_query.TXN_ROWS` 行，这里取同一量级；
#: 这是**展示截断**，不是数据截断 —— facts 里的流水一笔不少）。
TXN_BARS = 10

#: 卡片的**唯一标记**（那行说明）：界面测试靠它断言"卡片真的画出来了 / 真的没画"，
#: 比数图表元素稳（页面别处可能本来就有图）。文案照旧是给人看的，不是为测试留的暗号。
CARD_MARK = "📎 下面这张卡片的数据来自本次工具调用的**事实包**（与上面回执同一份数字）。"


def _num(yuan: object) -> float:
    """展示用坐标：把事实包里的「元」串（`4,370.00`）去千分位 → 数值。纯格式化，不重算金额。"""
    try:
        return float(str(yuan).replace(",", ""))
    except ValueError:
        return 0.0


def _bar(pairs: Sequence[tuple[str, object]], *, height: int = 240) -> None:
    """画柱状图；`pairs` 是 `(横坐标名, 金额元串)`，空则不画（不画空图）。"""
    series = {label: _num(value) for label, value in pairs if str(value or "").strip()}
    if not series:
        return
    st.bar_chart(series, height=height)


# ---------------- 各意图的图 ----------------

def _draw_balance(facts: Mapping[str, Any]) -> None:
    """F23：余额 —— 两个指标 + 一张余额/可用余额对照（都是 facts 原值）。"""
    left, right = st.columns(2)
    left.metric("账户余额（元）", str(facts.get("balance_yuan") or "—"))
    right.metric("可用余额（元）", str(facts.get("available_yuan") or "—"))
    _bar([("账户余额", facts.get("balance_yuan")), ("可用余额", facts.get("available_yuan"))],
         height=180)


def _draw_txns(facts: Mapping[str, Any]) -> None:
    """F02：流水 —— 本期支出/收入合计 + 每笔流水一根柱子（金额逐笔照抄）。"""
    items = list(facts.get("items") or [])
    if not items:
        return
    left, right = st.columns(2)
    left.metric("区间支出合计（元）", str(facts.get("page_out_sum_yuan") or "—"))
    right.metric("区间收入合计（元）", str(facts.get("page_in_sum_yuan") or "—"))
    st.caption(f"下面每根柱子 = 一笔流水（金额原样取自事实包，共 {len(items)} 笔）：")
    _bar([(str(item.get("ts"))[:10], item.get("amount_yuan")) for item in items[:TXN_BARS]])


def _draw_spending(facts: Mapping[str, Any]) -> None:
    """F14：账单分析 —— 分类金额柱状图（`groups[]` 由工具层算好，界面不重算）。"""
    groups = list(facts.get("groups") or [])
    if not groups:
        return
    st.caption(f"支出分类（共 {len(groups)} 类，合计 {facts.get('total_yuan') or '—'} 元，"
               "占比由工具层给出）：")
    _bar([(str(group.get("key")), group.get("amount_yuan")) for group in groups])


def _draw_anomalies(facts: Mapping[str, Any]) -> None:
    """F03：异常检测 —— 扫描/命中两个指标 + 命中清单（笔数取自 facts，界面不数）。"""
    left, right = st.columns(2)
    left.metric("扫描笔数", str(facts.get("scanned_count") or "—"))
    right.metric("命中可疑笔数", str(facts.get("anomaly_count") or "—"))
    items = list(facts.get("items") or [])
    if not items:
        return
    rows = [{"时间": str(item.get("ts"))[:16].replace("T", " "),
             "交易对手": item.get("counterparty") or "—",
             "金额（元）": item.get("amount_yuan"),
             "等级": item.get("severity"),
             "依据": item.get("reason")}
            for item in items]
    st.dataframe(rows, hide_index=True, width="stretch")


def _draw_report(facts: Mapping[str, Any]) -> None:
    """F24：账单总结 —— 收/支/净三指标 + 分类柱状图。"""
    cols = st.columns(3)
    cols[0].metric("支出合计（元）", str(facts.get("out_sum_yuan") or "—"))
    cols[1].metric("收入合计（元）", str(facts.get("in_sum_yuan") or "—"))
    cols[2].metric("净额（元）", str(facts.get("net_yuan") or "—"))
    _bar([("支出", facts.get("out_sum_yuan")), ("收入", facts.get("in_sum_yuan"))], height=200)
    _draw_spending(facts)


_DRAWERS: dict[str, Callable[[Mapping[str, Any]], None]] = {
    "balance_query": _draw_balance,
    "txn_query": _draw_txns,
    "bill_analysis": _draw_spending,
    "anomaly_check": _draw_anomalies,
    "bill_report": _draw_report,
}


def render(turn: Mapping[str, Any], *, key: str, on_ask: Callable[[str], None]) -> bool:
    """在助手气泡里画富卡片；画不出来（意图没有卡片 / facts 为空）→ `False`，调用方已退回纯文本。

    `on_ask` 由 `app.py` 注入：点按钮 → 把那句话交给编排层 → rerun。
    界面因此**不需要**知道自己被点了什么业务：它只是替用户把话说了一遍。
    """
    intent = str(turn.get("intent") or "")
    facts = turn.get("facts") or {}
    if intent not in INSIGHT_INTENTS or not facts:
        return False
    with st.container(border=True):
        st.caption(CARD_MARK)
        _DRAWERS[intent](facts)
        st.markdown("**接着问：**")
        columns = st.columns(len(FOLLOW_UPS[intent]))
        for index, (label, prompt) in enumerate(FOLLOW_UPS[intent]):
            if columns[index].button(label, key=f"{key}-ask-{index}", width="stretch"):
                on_ask(prompt)
    return True
