"""编排层**查询类回执**渲染（F02 流水明细 / F14 分类占比）。

为什么单开一个文件：`agent/render_lists.py` 已经到 350 行（CLAUDE.md 红线 300），
再往上堆只会更难读；照 `agent/read_routes.py`（卡 23 拆 `TOOL_ROUTES`）与
`agent/period.py`（卡 17b 拆时间解析）的先例：新文件自行实现，`agent/templates.py`
import 后**再导出**（同一份实现，不是副本），历史调用点行为不变。

与 `render_lists` 的同源纪律（铁律 2）：
- 每个数字都从 `facts` 照抄，不换算、不补全、不猜测；
- 明细用 **Markdown 表格**（照 `render_payees` 的先例：界面 `st.markdown` 会渲染成 HTML 表，
  同时也能被界面解析成图表坐标，一份数据两处用）；
- 空结果**自己的句子**，绝不套「共 0 笔：」这种残句（与 `T_CARD_NO_CARD` 同口径）。

分层：属编排层，只 import 标准库；不碰 `data/`、不写 SQL。
"""

from __future__ import annotations

from typing import Mapping

__all__ = ["BAR_CELLS", "T_ANALYSIS_EMPTY", "T_ANALYSIS_HEAD", "T_ANALYSIS_NO_PREV",
           "T_SPEND_TABLE_HEAD", "T_SPEND_TABLE_SEP", "T_TXN_EMPTY", "T_TXN_HEAD",
           "T_TXN_TABLE_HEAD", "T_TXN_TABLE_SEP", "TXN_ROWS", "TemplateError",
           "render_spending", "render_txns", "spend_row", "txn_row"]

# ---------------- F02 流水查询 ----------------
#: facts 键见 `tools/query.py` T2（`total_count` / `page_out_sum_yuan` / `page_in_sum_yuan` / `items[]`）。
#: 老口径只印一句「共查到 N 笔交易」——用户拿到合计数却**一笔流水都看不到**，
#: 这是 F02 在对话框里最直观的缺口，明细表补在这里。
#: 注意：T2 的 facts **没有** date_from/date_to 键 → 头句不得印时间范围（否则校验器当幻觉）。
T_TXN_HEAD = "共查到 {total_count} 笔交易，其中支出合计 {page_out_sum_yuan} 元、收入合计 {page_in_sum_yuan} 元。"
#: 空结果也**照印条数**（`total_count` 来自 facts）：只看「没有流水」四个字，用户无法判断
#: 是"真的没有"还是"没查到"；带上「共 0 笔交易」才是一句完整的交代。
T_TXN_EMPTY = "共查到 {total_count} 笔交易 —— 该区间没有符合条件的流水，故不列明细。"
T_TXN_TABLE_HEAD = "| 时间 | 交易对手 | 金额（元） | 分类 |"
T_TXN_TABLE_SEP = "| --- | --- | --- | --- |"

#: 回执里最多展开多少行明细。回执不是账单，铺太长会淹没重点；**这个数不进回执**
#: （数字校验器只认 facts，写「前 10 笔」会被判幻觉 → 降级）。
TXN_ROWS = 10

#: 占比条的总格数（`■` / `□` 拼出来的纯文本条形图，界面与终端都能看）。
BAR_CELLS = 10

#: 空单元格占位（与 `render_payees` 的 `T_PAYEE_NO` 同口径：是占位符，不是编造值）。
CELL_EMPTY = "—"


class TemplateError(RuntimeError):
    """模板渲染失败（缺 facts 键）。**宁可炸**也不许静默留白。

    与 `agent.render_lists.TemplateError` / `agent.templates.TemplateError` 是**同一个类**：
    本模块被 `templates.py` import 后再导出，`except templates.TemplateError` 照旧命中。
    """


def _cell(value: object) -> str:
    """表格单元格：竖线会破坏 Markdown 表格 → 换成斜杠；空值用占位符（不编造）。"""
    text = str(value or "").strip().replace("|", "/")
    return text or CELL_EMPTY


def txn_row(item: Mapping) -> str:
    """一笔流水一行（Markdown 表格行），字段全部照抄 facts 的 `items[]`。

    时间只取到分钟（`2026-08-15 20:40`）—— 秒对账单没有意义，且它是 facts 里 `ts` 的
    前缀，不是新数字；金额取工具层格式化好的 `amount_yuan`（**带符号**：负数是支出）。
    """
    moment = str(item["ts"])[:16].replace("T", " ")
    return (f"| {moment} | {_cell(item.get('counterparty'))} | {item['amount_yuan']} "
            f"| {_cell(item.get('category'))} |")


def render_txns(facts: Mapping) -> str:
    """`txn_query` 回执（F02）：头句 + 明细表。

    分支顺序：**空结果优先**（自己的句子，不套列表头）。超出 `TXN_ROWS` 的部分
    **不静默截断** —— 明确说一句「其余未展开」，避免用户以为就这几笔。
    """
    items = list(facts.get("items") or [])
    head = T_TXN_EMPTY if not items else T_TXN_HEAD
    try:
        head = head.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'txn_query' 模板缺少 facts 键：{exc}") from exc
    if not items:
        return head
    rows = [txn_row(item) for item in items[:TXN_ROWS]]
    lines = [head, "", T_TXN_TABLE_HEAD, T_TXN_TABLE_SEP, *rows]
    if len(items) > TXN_ROWS:
        lines += ["", "（回执只列出其中一部分流水，其余未在此展开。）"]
    return "\n".join(lines)


# ---------------- F14 账单分析 ----------------
#: facts 键见 `tools/query.py` T3（`period` / `total_yuan` / `vs_prev_pct` / `groups[]`）。
#: 老口径只有一句「{period} 共支出 X 元，环比 Y%」——**分类占比整块被丢回执外**，
#: 用户看不到钱花在哪；这里按 `render_payees` 的表格先例补出来。
T_ANALYSIS_HEAD = "{period} 共支出 {total_yuan} 元，环比 {vs_prev_pct}%。"
#: 上期没有可比支出：老口径会把 `None` 直接印成「环比 None%」——那是模板 bug，
#: 这里换成自己的句子（工具层 `query.py` 备好的措辞只写进了 `message`，而 message 不被读取）。
T_ANALYSIS_NO_PREV = "{period} 共支出 {total_yuan} 元，上一账期没有可比的支出，故不给环比。"
#: 空结果同样要**点明账期**：不带 period 的话用户不知道查的是哪个月（也就无从判断对不对）。
T_ANALYSIS_EMPTY = "{period} 没有支出记录，故不给环比。"
T_SPEND_TABLE_HEAD = "| 分类 | 金额（元） | 占比 |"
T_SPEND_TABLE_SEP = "| --- | --- | --- |"


def pct_bar(pct: object) -> str:
    """整数百分比 → 纯文本占比条（`■■■□□□□□□□`）。

    只做**格子数的整数换算**（`pct * BAR_CELLS // 100`），不碰金额、不改百分比本身；
    条形图是给眼睛看的，真正的数字仍是旁边那两列。
    """
    filled = int(pct) * BAR_CELLS // 100
    return "■" * filled + "□" * (BAR_CELLS - filled)


def spend_row(item: Mapping) -> str:
    """一个分组一行（Markdown 表格行）：分类 / 金额 / 占比 + 占比条。"""
    return (f"| {_cell(item['key'])} | {item['amount_yuan']} | {item['pct']}% "
            f"{pct_bar(item['pct'])} |")


def render_spending(facts: Mapping) -> str:
    """`bill_analysis` 回执（F14）：头句 + 分类占比表。

    `vs_prev_pct` 为 None（上期无支出）走自己的句子，绝不印「None%」。
    """
    groups = list(facts.get("groups") or [])
    if not groups:
        head = T_ANALYSIS_EMPTY
    elif facts.get("vs_prev_pct") is None:
        head = T_ANALYSIS_NO_PREV      # 上期无可比支出 → 绝不印「环比 None%」
    else:
        head = T_ANALYSIS_HEAD
    try:
        head = head.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'bill_analysis' 模板缺少 facts 键：{exc}") from exc
    if not groups:
        return head
    rows = [spend_row(item) for item in groups]
    return "\n".join([head, "", T_SPEND_TABLE_HEAD, T_SPEND_TABLE_SEP, *rows])
