"""F02 流水查询的**汇总事实**（分类 / 日序 / 净额 / 大额）—— 纯整数分，无 LLM。

为什么单开一个文件：`tools/query.py` 已 432 行、`tools/_query_analysis.py` 399 行
（都过了 CLAUDE.md 的 300 行红线），照 `tools/_scene_reads.py`（卡 22）与
`tools/_query_analysis.py`（卡 09）的先例：新核算逻辑进新文件，`query.py` 只焊接。

纪律（与 `_query_analysis` 同源）：
- **只做整数分运算**，金额一律阴性出万一 + 收支柱分列，绝不用浮点；
- 分组、占比、排序都在**这里一次算完**放进 facts —— 界面只读不重算（铁律 2）：
  旧版界面把它自己的分组/求和塞在 UI 里，同一份数字两处算，早晚对不上；
- **分组 Percent（pct）用整数百分比**，与 T3 `_split_pct` 的口径一致（合计严格 ≤ 100）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping

from tools._query_common import _money_facts

#: 明细行里带出来给界面展示的**非金额字段**（用户看一笔流水要的是：对方是谁、走的什么渠道、
#: 这笔之后还剩多少 —— 光有金额和时间认不出这笔账）。
DETAIL_FIELDS = ("channel", "memo", "balance_after", "direction")

#: 大额交易榜最多几笔（与回执里的量级一致，多了会淹没重点）。
TOP_LARGE = 5


def detail_item(row: Mapping) -> dict:
    """一笔流水 → facts 里的明细项（所有值照抄库行，界面不再二次加工）。

    `balance_after` 是**这笔之后的账户余额**（库里本来就有），展示明细时作为最后一列 ——
    用户对一笔账最常问的其实是"这笔扣完还剩多少"，有它才说得清收支变化。
    """
    item = {
        "id": row["id"],
        "ts": row["ts"],
        "counterparty": row.get("counterparty") or "",
        "category": row.get("category") or "",
        "direction": row.get("direction") or ("in" if row["amount"] > 0 else "out"),
        "channel": row.get("channel") or "",
        "memo": row.get("memo") or "",
        **_money_facts(row["amount"], "amount"),
    }
    after = row.get("balance_after")
    if after is not None:
        item.update(_money_facts(int(after), "balance_after"))
    return item


def category_groups(rows: list[dict]) -> list[dict]:
    """按**分类**汇总支出 → `[{key, amount_yuan, count, pct}]`。

    为什么只统计支出（不动收入）：占比的分母必须是"这个区间花掉的钱"才有意义 ——
    收入混进来会让"分类占比"变成一笔糊涂账（T3 账单分析也是这个口径，两边必须一致）。
    返回列表按金额降序（与 T3 `groups` 的排布一致）。
    """
    totals: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["amount"] >= 0:
            continue
        key = row.get("category") or "其他"
        totals[key] += -row["amount"]
        counts[key] += 1
    total = sum(totals.values())
    if not total:
        return []
    groups = [{"key": key, **_money_facts(amount, "amount"), "count": counts[key],
               "pct": amount * 100 // total}
              for key, amount in totals.items()]
    groups.sort(key=lambda group: group["amount"], reverse=True)   # `amount` 是整数分（见 `_money_facts`）
    return groups


def daily_series(rows: list[dict]) -> list[dict]:
    """按**日**汇总收支 → `[{date, out_yuan, in_yuan}]`（升序，日期来自 `ts` 前缀）。

    按**交易日期**而不是按月：流水回执已经点明了查询区间，用户真正想看的是
    "这一个月里钱是什么时候出去的"，日序才是那根该给的曲线。日期是 `ts` 的切片，不是新数字。
    """
    buckets: dict[str, int] = defaultdict(int)
    inflows: dict[str, int] = defaultdict(int)
    for row in rows:
        day = str(row["ts"])[:10]
        if row["amount"] < 0:
            buckets[day] += -row["amount"]
        else:
            inflows[day] += row["amount"]
    days = sorted(set(buckets) | set(inflows))
    return [{"date": day,
             **_money_facts(buckets.get(day, 0), "out"),
             **_money_facts(inflows.get(day, 0), "in")} for day in days]


def largest_items(rows: list[dict], limit: int = TOP_LARGE) -> list[dict]:
    """金额**绝对值**最大的几笔（按记账流水里最常被关心的笔为准）。"""
    ranked = sorted(rows, key=lambda row: (abs(row["amount"]), str(row["ts"])), reverse=True)
    return [detail_item(row) for row in ranked[:limit]]


def summarize(rows: list[dict], page: list[dict]) -> dict:
    """整份区间 + 本页明细的汇总事实包，全部并入 T2 的 `facts`。

    `rows` = **整个区间命中**的流水（不受 `limit` 限制）→ 合计数不受分页影响；
    `page` = 本次实际返回的那一页 → `items` 只印这一页，二者在回执里各归各位，不混说。
    """
    out = sum(-row["amount"] for row in rows if row["amount"] < 0)
    inflow = sum(row["amount"] for row in rows if row["amount"] > 0)
    return {
        "out_count": sum(1 for row in rows if row["amount"] < 0),
        "in_count": sum(1 for row in rows if row["amount"] > 0),
        **_money_facts(out, "out_sum"),
        **_money_facts(inflow, "in_sum"),
        **_money_facts(inflow - out, "net"),
        "categories": category_groups(rows),
        "daily": daily_series(rows),
        "largest": largest_items(rows),
        "items": [detail_item(row) for row in page],
    }
