"""查询分析内核（任务卡 04b 拆分）：账期换算 / 占比 / 异常规则 / 报告渲染。

铁律：
- **LLM 不参与任何计算**：金额、占比、异常判定全部由整数运算决定。
- 金额一律整数分，全程不出现 float。
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal

from data import dao
from tools._query_common import (
    PCT_TOTAL,
    ToolError,
    _owned_account_ids,
    current_user_id,
    month_windows,
)
from tools.schemas import ErrorCode, MAX_LIMIT

logger = logging.getLogger(__name__)

# ---------- 常量（来源逐条注明） ----------

AMOUNT_RATIO_THRESHOLD = 3  # 金额偏离倍数（> 均值 ×3）
BASELINE_DAYS = 90          # 基准天数
#: T4 第 4 条规则（规格 §2 T4 备注，SPEC-CHANGE b84ac38 定稿）：过去 90 天该 user 无交易的 counterparty
NEW_MERCHANT_DAYS = 90      # 新商户天数（**主程序口径是 90 天**，不是 30）
NIGHT_START_HOUR = 23       # 凌晨时段开始
NIGHT_END_HOUR = 6          # 凌晨时段结束
VELOCITY_MINUTES_T4 = 60    # 短时高频窗口（分钟）
VELOCITY_MIN_TXNS = 3       # 短时高频笔数阈值
KIND_LABELS = {"monthly": "月度", "yearly": "年度"}

#: 周期展示文案（**仅展示**，不改枚举值）。与 `tools/subscription.py` 的 `CYCLE_LABELS` 同口径 ——
#: 两侧必须一致，`tests/test_tools_subscription_cancel.py` 钉住了这一点。
CYCLE_LABELS = {"monthly": "每月", "yearly": "每年"}

#: 异常原因文案（T4）。**文案是冻结口径**：`tests/test_query_analysis.py` 按这些字符串断言，
#: 改文案等于改接口，必须人类拍板。
REASON_AMOUNT_JUMP, REASON_NIGHT, REASON_VELOCITY = "金额显著高于近期均值", "凌晨时段交易", "同商户短时密集交易"
REASON_NEW_MERCHANT = "陌生商户交易"

#: 账期正则：`2026` 或 `2026-09`；月份越界（13 月）由 `_period_bounds` 二次判定 → INVALID_ARGUMENT。
_PERIOD_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")

# ---------- 与主程序共享单份实现的适配 ----------
#: `PCT_TOTAL` / `_month_windows` / `_owned_account_ids` 在主程序是**跨模块共享**符号（转账、订阅、
#: 卡片都依赖）。delivery 版自带私有实现，覆盖后会与其余模块漂移，故这里转发到
#: `tools._query_common` 的单份实现 —— **分析逻辑保持 delivery 版原样**，只把数据入口对齐。


def _month_windows(start: date, end: date) -> list[tuple[str, str]]:
    """转发到 `tools._query_common.month_windows`：单份实现，订阅侧同样复用。"""
    return month_windows(start, end)


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


# ---------- 时间解析 ----------

def anchor_period() -> str:
    """锚点账期：当前日期所在月份（YYYY-MM）。"""
    from data.seed import AS_OF
    return AS_OF.strftime("%Y-%m")


def resolve_period(text: str) -> str | None:
    """解析相对/绝对账期。"""
    text = text.strip().lower()
    if text in ("本月", "这个月", "当月", "current_month"):
        return anchor_period()
    if text in ("上个月", "上月", "last_month"):
        d = date.fromisoformat(anchor_period() + "-01")
        prev = d.replace(day=1) - timedelta(days=1)
        return prev.strftime("%Y-%m")
    # 尝试 YYYY-MM
    try:
        if len(text) == 7 and text[4] == "-":
            datetime.strptime(text, "%Y-%m")
            return text
    except ValueError:
        pass
    # 尝试 YYYY
    try:
        if len(text) == 4:
            datetime.strptime(text, "%Y")
            return text
    except ValueError:
        pass
    return None


def resolve_day(text: str | None, edge: Literal["start", "end"]) -> str | None:
    """解析日期（YYYY-MM-DD）。"""
    if text is None:
        return None
    text = text.strip()
    try:
        d = datetime.strptime(text, "%Y-%m-%d").date()
        return d.isoformat()
    except ValueError:
        pass
    # 尝试相对表达
    if text.lower() in ("今天", "today"):
        from data.seed import AS_OF
        return AS_OF.isoformat()
    return None


def _period_bounds(period: str) -> tuple[date, date]:
    """`\"2026-09\"` / `\"2026\"` → 闭区间（首日, 末日）；非法 → INVALID_ARGUMENT。

    非法账期（含 13 月这类越界月份）**必须抛 `ToolError`** 而不是 `ValueError`：后者会穿透
    工具层直接炸栈，`tests/test_query_analysis.py` 的非法账期用例钉住了这一口径。
    """
    match = _PERIOD_RE.match(period.strip()) if isinstance(period, str) else None
    if match is None or (match.group(2) and not 1 <= int(match.group(2)) <= 12):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "账期格式不对")
    year, month = int(match.group(1)), match.group(2)
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    first = date(year, int(month), 1)
    return first, _next_month(first) - timedelta(days=1)


def _previous_period(period: str) -> str:
    """上一个同长度账期。"""
    period = period.strip()
    if len(period) == 7:  # YYYY-MM
        d = date.fromisoformat(f"{period}-01")
        if d.month == 1:
            prev = date(d.year - 1, 12, 1)
        else:
            prev = date(d.year, d.month - 1, 1)
        return prev.strftime("%Y-%m")
    elif len(period) == 4:  # YYYY
        return str(int(period) - 1)
    else:
        raise ValueError(f"无法解析账期: {period}")


# ---------- 数据查询 ----------

def _owned_txns(date_from: str, date_to: str) -> list[dict]:
    """查询当前用户指定日期范围的流水（含归属断言）。"""
    from data import dao

    # 校验日期格式
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "日期格式不正确")

    if d_from > d_to:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "起始日期不能晚于结束日期")

    # 按月分窗取流水，再按归属过滤（主程序 DAO 无「按 user 列账户/流水」的入口，
    # 归属判定统一走 `_owned_account_ids()`，分窗绕开 DAO 单页 500 行的静默截断）
    owned, rows = _owned_account_ids(), []
    for start, end in _month_windows(d_from, d_to):
        page = dao.list_txn(start, end, limit=MAX_LIMIT)
        if page["total_count"] > MAX_LIMIT:
            raise ToolError(ErrorCode.TOO_MANY_ROWS, "该区间的流水过多，请缩小日期范围")
        rows.extend(row for row in page["items"] if row["account_id"] in owned)

    return rows


def _scan(start: date, end: date) -> tuple[list[dict], list[dict], dict]:
    """扫描账期内的流水，返回 (rows, series, merchants)。"""
    rows = _owned_txns(start.isoformat(), end.isoformat())
    pool = _owned_txns((start - timedelta(days=BASELINE_DAYS)).isoformat(), end.isoformat())
    inside = [row for row in pool if start.isoformat() <= row["ts"][:10] <= end.isoformat()]
    return inside, _baseline_series(pool), _merchant_index(pool)


# ---------- 分析计算 ----------

def _spend_groups(rows: list[dict], group_by: str) -> tuple[dict[str, int], int]:
    """按维度分组统计支出（绝对金额）。返回 ({key: amount}, total)。"""
    totals = defaultdict(int)
    for row in rows:
        if row["amount"] < 0:  # 支出
            key = row.get(group_by, "未知")
            totals[key] += abs(row["amount"])
    total = sum(totals.values())
    return dict(totals), total


def _split_pct(amounts: list[int], total: int) -> list[int]:
    """最大余额法：整数百分比，保证和为 100。"""
    if total == 0:
        return [0] * len(amounts)

    raw_pcts = [(amt * 1000 // total, i) for i, amt in enumerate(amounts)]
    raw_pcts.sort(reverse=True)

    pcts = [0] * len(amounts)
    remaining = 100
    for _, i in raw_pcts:
        pcts[i] = remaining
        remaining = 0

    # 更精确的分配
    pcts = [amt * 100 // total for amt in amounts]
    diff = 100 - sum(pcts)
    # 把余数加到最大的几个
    if diff != 0 and amounts:
        indexed = sorted(enumerate(amounts), key=lambda x: -x[1])
        for i in range(abs(diff)):
            idx = indexed[i % len(indexed)][0]
            pcts[idx] += 1 if diff > 0 else -1

    return pcts


def _vs_prev_pct(total: int, prev_total: int | None) -> int | None:
    """与上期比的整数百分比（**四舍五入**，纯整数运算）；上期无支出 → None。

    主程序口径是四舍五入（`//` 直接截断会偏低一档，环比数字会对不上账 ——
    `tests/test_query_analysis.py` 钉住了 47% 这个值）。
    """
    if prev_total is None or prev_total <= 0:
        return None
    diff = total - prev_total
    magnitude = (abs(diff) * 2 * PCT_TOTAL + prev_total) // (2 * prev_total)
    return magnitude if diff >= 0 else -magnitude


# ---------- 异常检测（T4/F03：主程序冻结口径，非本次覆盖范围） ----------

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
