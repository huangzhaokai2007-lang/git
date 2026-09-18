"""工具层分析内核（任务卡 05b 从 `tools/query.py` 拆出）：账期换算、占比/环比、异常规则、报告渲染。

依赖方向**单向**：`tools/query.py` → 本模块 → `tools/_query_common.py`；本模块**绝不** import
`tools.query`（否则形成回边，卡 05b 验收门 ② 会 FAIL）。

金额一律整数分、全程无浮点（占比用最大余额法、环比用整数四舍五入）；
阈值常量集中在本文件顶部，逐条注明来源。
⚠ `VELOCITY_MINUTES_T4`（本文件，只读检测 60 分钟）与 `_transfer_risk.VELOCITY_MINUTES_WRITE`
（§5 写操作降级 10 分钟）**同名不同义**，卡 05b 要求 #3 明确两者不可合并。
"""

from __future__ import annotations

import re
from bisect import bisect_left
from datetime import date, datetime, timedelta

from data import dao
from tools._query_common import (
    PCT_TOTAL, ToolError, _owned_account_ids, month_windows,
)
from tools.schemas import (
    ErrorCode, MAX_LIMIT,
)

def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts)

def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)

def _period_bounds(period: str) -> tuple[date, date]:
    """`"2026-09"` / `"2026"` → 闭区间（首日, 末日）；非法 → INVALID_ARGUMENT。"""
    match = _PERIOD_RE.match(period.strip()) if isinstance(period, str) else None
    if match is None or (match.group(2) and not 1 <= int(match.group(2)) <= 12):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "账期格式不对")
    year, month = int(match.group(1)), match.group(2)
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    first = date(year, int(month), 1)
    return first, _next_month(first) - timedelta(days=1)

def _previous_period(period: str) -> str:
    """上一个同长度账期：`2026-09` → `2026-08`，`2026-01` → `2025-12`，`2026` → `2025`。"""
    start, _ = _period_bounds(period)
    previous = date(start.year, start.month, 1) - timedelta(days=1)
    return f"{previous.year:04d}-{previous.month:02d}" if len(period.strip()) > 4 else f"{previous.year:04d}"

def _month_windows(start: date, end: date) -> list[tuple[str, str]]:
    """（卡 06b）转发到 `tools._query_common.month_windows`：单份实现，订阅侧同样复用。"""
    return month_windows(start, end)

def _owned_txns(date_from: str, date_to: str) -> list[dict]:
    """取区间内**属于当前用户账户**的全部流水（按月分窗，条数超上限则报 TOO_MANY_ROWS）。

    `date_from > date_to` 直接拒（与 DAO 同口径，避免悄悄返回空结果掩盖参数错误）。
    """
    first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if first > last:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "起始日期不能晚于结束日期")
    owned, rows = _owned_account_ids(), []
    for start, end in _month_windows(first, last):
        page = dao.list_txn(start, end, limit=MAX_LIMIT)
        if page["total_count"] > MAX_LIMIT:
            raise ToolError(ErrorCode.TOO_MANY_ROWS, "该区间的流水过多，请缩小日期范围")
        rows.extend(row for row in page["items"] if row["account_id"] in owned)
    return rows

def _split_pct(amounts: list[int], total: int) -> list[int]:
    """整数百分比且**和恒为 PCT_TOTAL**（最大余额法）；total ≤ 0 → 全 0。"""
    if total <= 0:
        return [0] * len(amounts)
    base = [amount * PCT_TOTAL // total for amount in amounts]
    rest = PCT_TOTAL - sum(base)
    order = sorted(range(len(amounts)), key=lambda i: (-(amounts[i] * PCT_TOTAL % total), i))
    for index in order[:rest]:
        base[index] += 1
    return base

def _vs_prev_pct(total: int, prev_total: int) -> int | None:
    """与上期比的整数百分比（四舍五入，纯整数运算）；上期无支出 → None。"""
    if prev_total <= 0:
        return None
    diff = total - prev_total
    magnitude = (abs(diff) * 2 * PCT_TOTAL + prev_total) // (2 * prev_total)
    return magnitude if diff >= 0 else -magnitude

def _spend_groups(rows: list[dict], group_by: str) -> tuple[dict[str, int], int]:
    """按 `group_by`（category|channel）汇总**支出**（amount<0）的绝对金额（分）。"""
    totals: dict[str, int] = {}
    for row in rows:
        if row["amount"] < 0:
            key = row[group_by] or "未分类"
            totals[key] = totals.get(key, 0) + -row["amount"]
    return totals, sum(totals.values())

def _anomalies(rows: list[dict], series: list[tuple[datetime, int]],
               merchant_index: dict[str, list[datetime]] | None = None) -> list[dict]:
    """四条规则（口径见文件头常量）；severity 由**命中规则条数**决定：≥2 high，1 medium。

    `series` 是基准序列（该用户**支出**流水的 (时间, 绝对金额)，按时间升序）；
    `merchant_index` 是基准池里每个 counterparty 的交易时间列表（升序），用于第 4 条规则
    「陌生商户：过去 `NEW_MERCHANT_DAYS` 天内该 counterparty 无交易（不含本笔）」。

    每笔交易的基准是它自己"近 90 天"（不含本笔）的均值 —— 规格字面口径，
    与"用哪个账期来看"无关，故同一笔交易在任何账期下判定一致。
    """
    velocity = _velocity_hits(rows)
    index = merchant_index or {}
    items: list[dict] = []
    for row in sorted(rows, key=lambda item: (item["ts"], item["id"])):
        moment = _dt(row["ts"])
        baseline = _baseline_for(moment, series)
        hits = []
        if baseline > 0 and -row["amount"] > baseline * AMOUNT_RATIO_THRESHOLD:
            hits.append(REASON_AMOUNT_JUMP)
        if _is_night(row["ts"]):
            hits.append(REASON_NIGHT)
        if row["id"] in velocity:
            hits.append(REASON_VELOCITY)
        if row["counterparty"] and _is_new_merchant(index.get(row["counterparty"], [moment]), moment):
            hits.append(REASON_NEW_MERCHANT)
        if hits:
            items.append({"txn_id": row["id"], "reason": "、".join(hits),
                          "severity": "high" if len(hits) > 1 else "medium",
                          "amount": row["amount"], "baseline_mean": baseline})
    return items


def _is_new_merchant(times: list[datetime], moment: datetime) -> bool:
    """本笔之前 `NEW_MERCHANT_DAYS` 天内该 counterparty 没有过交易 → 陌生商户（规格 T4 第 4 条）。"""
    index = bisect_left(times, moment)                      # 严格早于本笔的笔数
    if index == 0:
        return True
    return times[index - 1] < moment - timedelta(days=NEW_MERCHANT_DAYS)


def _merchant_index(rows: list[dict]) -> dict[str, list[datetime]]:
    """基准池里 counterparty → 交易时间（升序）的索引，供陌生商户规则二分查询。"""
    index: dict[str, list[datetime]] = {}
    for row in rows:
        if row["counterparty"]:
            index.setdefault(row["counterparty"], []).append(_dt(row["ts"]))
    for times in index.values():
        times.sort()
    return index

def _baseline_series(rows: list[dict]) -> list[tuple[datetime, int]]:
    """基准序列：**支出**流水的 (时间, 绝对金额)，时间升序（供二分取窗口）。

    为什么只取支出：入账（工资金额最大）若参与均值，会把基准抬高到"什么都算不上异常"，
    并且工资自身会被判为异常；金额偏离要答的是"这笔支出相对日常支出是否异常"。
    """
    return sorted((_dt(row["ts"]), -row["amount"]) for row in rows if row["amount"] < 0)

def _baseline_for(moment: datetime, series: list[tuple[datetime, int]]) -> int:
    """某笔交易"近 BASELINE_DAYS 天"（左闭右开、**不含本笔**）支出均值的整数地板值；无样本 → 0。

    全程整数运算（无浮点）：区间求和后整除。
    """
    stop = bisect_left(series, (moment,))
    begin = bisect_left(series, (moment - timedelta(days=BASELINE_DAYS),))
    window = series[begin:stop]
    return sum(amount for _, amount in window) // len(window) if window else 0

def _scan(start: date, end: date) -> tuple[list[dict], list[tuple[datetime, int]], dict[str, list[datetime]]]:
    """分析期流水 + 基准序列 + 商户索引：一次取 [分析期起点 - BASELINE_DAYS 天, 分析期终点] 的流水，
    期内的作扫描对象，全部作基准池（保证每笔都能看到自己往前 90 天的样本，陌生商户规则同样靠它）。
    """
    pool = _owned_txns((start - timedelta(days=BASELINE_DAYS)).isoformat(), end.isoformat())
    inside = [row for row in pool if start.isoformat() <= row["ts"][:10] <= end.isoformat()]
    return inside, _baseline_series(pool), _merchant_index(pool)

def _is_night(ts: str) -> bool:
    hour = _dt(ts).hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR

def _velocity_hits(rows: list[dict]) -> set[str]:
    """同商户 (counterparty) 在 VELOCITY_MINUTES_T4 分钟内出现 ≥VELOCITY_MIN_TXNS 笔 → 窗口内每笔命中。

    无对手方名的流水（counterparty 为空）不参与该规则：没有商户可归组。
    """
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        if row["counterparty"]:
            buckets.setdefault(row["counterparty"], []).append(row)
    window = timedelta(minutes=VELOCITY_MINUTES_T4)
    hits: set[str] = set()
    for bucket in buckets.values():
        ordered = sorted(bucket, key=lambda row: (row["ts"], row["id"]))
        for index, first in enumerate(ordered):
            group = [row for row in ordered[index:] if _dt(row["ts"]) - _dt(first["ts"]) < window]
            if len(group) >= VELOCITY_MIN_TXNS:
                hits.update(row["id"] for row in group)
    return hits

def _report_markdown(period: str, kind: str, facts: dict, groups: list[dict], subs: list[dict]) -> str:
    """生成 Markdown 账单报告：每个数字都从 `facts` 取（幻觉红线）。"""
    lines = [f"# {KIND_LABELS[kind]}账单报告 · {period}", "", "## 总览",
             f"- 本期支出合计：{facts['out_sum_yuan']} 元（{facts['out_count']} 笔）",
             f"- 本期收入合计：{facts['in_sum_yuan']} 元（{facts['in_count']} 笔）",
             f"- 净额：{facts['net_yuan']} 元"]
    if facts["vs_prev_pct"] is not None:
        lines.append(f"- 较上一账期：{facts['vs_prev_pct']}%（上期 {facts['prev_total_yuan']} 元）")
    lines += ["", "## 支出明细（按占比）", "| 项目 | 金额（元） | 占比 |", "| --- | --- | --- |"]
    lines += [f"| {group['key']} | {group['amount_yuan']} | {group['pct']}% |" for group in groups]
    lines += ["", "## 订阅（进行中）"]
    lines += ([f"- {sub['merchant']}：{sub['amount_yuan']} 元／{CYCLE_LABELS[sub['cycle']]}，下次扣费 {sub['next_charge_date']}"
               for sub in subs] or ["- 无"])
    lines += ["", "## 异常提示", f"本期发现 {facts['anomaly_count']} 笔可疑交易（详见异常检测结果）。", "",
              "> 本报告全部数字取自工具事实包，未经模型改写。"]
    return "\n".join(lines)


AMOUNT_RATIO_THRESHOLD = 3          # 来源：规格 §2 T4 备注（>近 90 天支出均值 3 倍）；§5 的 amount_jump 5 倍=写操作降级因子，场景不同

BASELINE_DAYS = 90                  # 来源：卡 04 第 3 条「近 90 天」；基准只取**支出**流水（见 _baseline_series）

NIGHT_START_HOUR, NIGHT_END_HOUR = 23, 6   # 来源：规格 §5「night(23:00-06:00)」

VELOCITY_MINUTES_T4 = 60        # 来源：卡 04 第 3 条「同商户 1 小时内」

VELOCITY_MIN_TXNS = 3               # 来源：卡 04 第 3 条「≥3 笔」

CYCLE_LABELS = {"monthly": "每月", "yearly": "每年"}

KIND_LABELS = {"monthly": "月度", "yearly": "年度"}

REASON_AMOUNT_JUMP, REASON_NIGHT, REASON_VELOCITY = "金额显著高于近期均值", "凌晨时段交易", "同商户短时密集交易"
#: T4 第 4 条规则（规格 §2 T4 备注，SPEC-CHANGE b84ac38 定稿）：过去 90 天该 user 无交易的 counterparty
NEW_MERCHANT_DAYS = 90
REASON_NEW_MERCHANT = "陌生商户交易"

_PERIOD_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")
