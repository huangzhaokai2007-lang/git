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

import csv
import io
from typing import Any, Callable, Mapping, Sequence

import streamlit as st
from agent.render_query import TXN_STATUS_LABELS      # 交易状态改名表（agent 层单一出处，界面不另抄一份）
from interfaces.web import anomaly_card_ui, chart_ui, txn_card_ui

#: 有富卡片的意图（其余意图一律不画 —— 宁可朴素，也不许拿半截数据凑图）。
#: 2026-10-29（用户 Comment 9）：`bill_report` 从这张卡里**整体摘掉** —— 三格指标（支出/收入/净额）、
#: 「接着问」按钮、「导出 / 复制这份回执」用户都不要，报告只留正文（分类饼图在 `spending_pie`、
#: 订阅块由 `legacy_bill_ui` 挂 `subscription_ui`）。留在这里就等于把它们再画回来。
INSIGHT_INTENTS = ("balance_query", "txn_query", "bill_analysis", "anomaly_check")

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
    chart_ui.bar(list(series.items()), height=height)


# ---------------- 各意图的图 ----------------

def _draw_balance(facts: Mapping[str, Any]) -> None:
    """F23：余额 —— 两个指标 + 一张余额/可用余额对照（都是 facts 原值）。"""
    left, right = st.columns(2)
    left.metric("账户余额（元）", str(facts.get("balance_yuan") or "—"))
    right.metric("可用余额（元）", str(facts.get("available_yuan") or "—"))
    _bar([("账户余额", facts.get("balance_yuan")), ("可用余额", facts.get("available_yuan"))],
         height=180)


def _draw_txns(facts: Mapping[str, Any]) -> None:
    """F02：流水 —— 交给 `txn_card_ui` 画**按日走势（置顶）+ 带流水号/交易状态的全量明细表**。

    2026-10-26 改：原先只给两个指标 + 「每根柱子 = 一笔」的柱图 —— 五十笔流水压成十根柱子，
    既看不清分类也点不到具体一笔，用户拿到的是张"看着像图表、实际没信息"的图，
    这是 F02 验收里最刺眼的一处。**详情卡已迁往 `interfaces/web/txn_card_ui.py`**
    （本文件够了 300 行红线），这里只做一行转发，调用点与页面桩全部不变。

    2026-10-26 二次验收：气泡里的回执只留头句（`components.LEDGER_CARD_INTENTS`），
    曲线与明细由这张卡统一呈现 —— 走势置顶，同一批数不再印两遍。
    """
    txn_card_ui.render(facts, key="txn")


def _draw_spending(facts: Mapping[str, Any]) -> None:
    """The category pie is already embedded in the reply at its original table."""


def _draw_anomalies(facts: Mapping[str, Any]) -> None:
    """F03：异常检测 —— 交给 `anomaly_card_ui` 画**依据分布图 + 自适应结论 + 命中清单**。

    2026-10-27 二次改动：卡片升级成「有结论、有分布」后本文件会撞 300 行红线，
    故按 `txn_card_ui` 的先例整体迁出；这里只做一行转发，调用点与页面桩全部不变。
    """
    anomaly_card_ui.render(facts, key="anomaly")


_DRAWERS: dict[str, Callable[[Mapping[str, Any]], None]] = {
    "balance_query": _draw_balance,
    "txn_query": _draw_txns,
    "bill_analysis": _draw_spending,
    "anomaly_check": _draw_anomalies,
}


def _csv(rows: Sequence[Mapping[str, Any]], headers: Sequence[str]) -> str:
    """把 facts 里的一张表拼成 CSV 文本（**只搬运，不改数**）；空表 → 空串，不给下载。"""
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(headers), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _table(facts: Mapping[str, Any], intent: str) -> tuple[str, Sequence[str], Sequence[Mapping]]:
    """这份回执里值得导出的那张表：`(文件名前缀, 列名, 行)`。没表 → 返回空。"""
    groups = list(facts.get("groups") or [])
    if intent in ("bill_analysis", "bill_report") and groups:
        return ("分类明细", ["支出分类", "金额（元）", "占比"],
                [{"支出分类": item.get("key"), "金额（元）": item.get("amount_yuan"),
                  "占比": f"{item.get('pct')}%"} for item in groups])
    items = list(facts.get("items") or [])
    if intent == "txn_query" and items:
        return ("流水明细", ["时间", "流水号", "交易对手", "分类", "交易状态", "金额（元）"],
                [{"时间": str(item.get("ts"))[:16].replace("T", " "),
                  "流水号": item.get("ref_no") or "",
                  "交易对手": item.get("counterparty") or "",
                  "分类": item.get("category") or "",
                  "交易状态": TXN_STATUS_LABELS.get(str(item.get("status") or ""), "—"),
                  "金额（元）": item.get("amount_yuan")} for item in items])
    if intent == "anomaly_check" and items:
        return ("异常清单", ["时间", "交易对手", "金额（元）", "等级", "依据"],
                [{"时间": str(item.get("ts"))[:16].replace("T", " "),
                  "交易对手": item.get("counterparty") or "",
                  "金额（元）": item.get("amount_yuan"),
                  "等级": item.get("severity"), "依据": item.get("reason")} for item in items])
    return ("", (), ())


def _export(turn: Mapping[str, Any], intent: str, facts: Mapping[str, Any], key: str) -> None:
    """卡片底部的导出/复制：回执原文（Markdown）+ 明细表（CSV）。

    做 PPT / 汇报材料时直接从这里拿，不用再手抄屏幕上的数字 —— 导出的每个数字仍然
    只是 `Turn.facts` 的原值搬运，界面不重算。
    """
    reply = str(turn.get("reply") or "")
    label, headers, rows = _table(facts, intent)
    with st.expander("⬇️ 导出 / 复制这份回执", expanded=False):
        if reply:
            st.download_button("下载回执（Markdown）", data=reply,
                               file_name=f"{intent}-{str(turn.get('trace_id') or key)[:8]}.md",
                               mime="text/markdown", key=f"{key}-dl-md", width="stretch")
            st.code(reply, language="markdown")   # 手动复制用：streamlit 代码块自带复制按钮
        if label and (csv_text := _csv(rows, headers)):
            # Excel recognizes UTF-8 reliably on direct open when the file has a BOM.
            st.download_button(f"下载{label}（CSV）", data=csv_text.encode('utf-8-sig'),
                               file_name=f"{label}-{str(turn.get('trace_id') or key)[:8]}.csv",
                               mime="text/csv; charset=utf-8", key=f"{key}-dl-csv", width="stretch")


def render(turn: Mapping[str, Any], *, key: str, on_ask: Callable[[str], None], readonly: bool = False) -> bool:
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
        #: 异常卡的追问按钮**跟着用户刚问的时间段走**（其余意图是固定三问）：
        #: 问完四月的异常，按钮点下去查的还是四月，不会跳回「上个月」。
        prompts = (anomaly_card_ui.follow_ups(facts) if intent == "anomaly_check"
                   else FOLLOW_UPS[intent])
        columns = st.columns(len(prompts))
        for index, (label, prompt) in enumerate(prompts):
            if columns[index].button(label, key=f"{key}-ask-{index}", width="stretch", disabled=readonly):
                on_ask(prompt)
        _export(turn, intent, facts, key)
    return True
