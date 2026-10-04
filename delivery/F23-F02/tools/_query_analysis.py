"""查询分析内核（任务卡 04b 拆分）：账期换算 / 占比 / 异常规则 / 报告渲染。

铁律：
- **LLM 不参与任何计算**：金额、占比、异常判定全部由整数运算决定。
- 金额一律整数分，全程不出现 float。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal

from tools._query_common import ToolError, current_user_id
from tools.schemas import ErrorCode, MAX_LIMIT

logger = logging.getLogger(__name__)

# ---------- 常量（来源逐条注明） ----------

AMOUNT_RATIO_THRESHOLD = 3  # 金额偏离倍数（> 均值 ×3）
BASELINE_DAYS = 90          # 基准天数
NEW_MERCHANT_DAYS = 30      # 新商户天数
NIGHT_START_HOUR = 23       # 凌晨时段开始
NIGHT_END_HOUR = 6          # 凌晨时段结束
VELOCITY_MINUTES_T4 = 60    # 短时高频窗口（分钟）
VELOCITY_MIN_TXNS = 3       # 短时高频笔数阈值
KIND_LABELS = {"monthly": "月度", "yearly": "年度"}


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
    """账期 → (开始日期, 结束日期)。"""
    period = period.strip()
    if len(period) == 7:  # YYYY-MM
        start = date.fromisoformat(f"{period}-01")
        # 下个月第一天减一天
        if start.month == 12:
            end = date(start.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        return start, end
    elif len(period) == 4:  # YYYY
        start = date(int(period), 1, 1)
        end = date(int(period), 12, 31)
        return start, end
    else:
        raise ValueError(f"无法解析账期: {period}")


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

    # 查询所有账户的流水
    account_ids = dao.list_account_ids(current_user_id())
    if not account_ids:
        return []

    rows = dao.list_txns_by_accounts(account_ids, date_from, date_to)

    # 检查是否超过上限
    if len(rows) > MAX_LIMIT:
        raise ToolError(ErrorCode.TOO_MANY_ROWS, "数据量过大，请缩小查询范围")

    return rows


def _scan(start: date, end: date) -> tuple[list[dict], list[dict], dict]:
    """扫描账期内的流水，返回 (rows, series, merchants)。"""
    rows = _owned_txns(start.isoformat(), end.isoformat())
    series = []
    merchants = defaultdict(list)
    for row in rows:
        series.append(row)
        if row.get("counterparty"):
            merchants[row["counterparty"]].append(row)
    return rows, series, dict(merchants)


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


def _vs_prev_pct(current: int, previous: int | None) -> int | None:
    """环比变化百分比（整数，上期无数据 → None）。"""
    if previous is None or previous == 0:
        return None
    return (current - previous) * 100 // previous


# ---------- 异常检测 ----------

def _anomalies(
    rows: list[dict], series: list[dict], merchants: dict[str, list[dict]]
) -> list[dict]:
    """检测异常交易。"""
    # 计算近 90 天支出均值
    from data.seed import AS_OF
    baseline_end = AS_OF
    baseline_start = baseline_end - timedelta(days=BASELINE_DAYS)
    baseline_rows = _owned_txns(baseline_start.isoformat(), baseline_end.isoformat())
    baseline_spends = [abs(r["amount"]) for r in baseline_rows if r["amount"] < 0]
    baseline_mean = sum(baseline_spends) // len(baseline_spends) if baseline_spends else 0

    anomalies = []
    for row in rows:
        if row["amount"] >= 0:
            continue  # 只检测支出

        reasons = []
        amount = abs(row["amount"])

        # 规则 1: 金额偏离
        if baseline_mean > 0 and amount > baseline_mean * AMOUNT_RATIO_THRESHOLD:
            reasons.append("金额异常")

        # 规则 2: 凌晨时段
        ts = datetime.fromisoformat(row["ts"])
        hour = ts.hour
        if hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR:
            reasons.append("凌晨交易")

        # 规则 3: 同商户短时高频
        merchant = row.get("counterparty")
        if merchant and merchant in merchants:
            merchant_txns = merchants[merchant]
            same_time = [
                r
                for r in merchant_txns
                if r["id"] != row["id"]
                and abs(
                    (datetime.fromisoformat(r["ts"]) - ts).total_seconds()
                )
                <= VELOCITY_MINUTES_T4 * 60
            ]
            if len(same_time) >= VELOCITY_MIN_TXNS - 1:
                reasons.append("高频交易")

        if reasons:
            severity = (
                "high"
                if len(reasons) >= 3
                else "medium"
                if len(reasons) == 2
                else "low"
            )
            anomalies.append(
                {
                    "txn_id": row["id"],
                    "reason": "、".join(reasons),
                    "severity": severity,
                    "amount": -amount,
                    "baseline_mean": baseline_mean,
                }
            )

    return anomalies


# ---------- 报告渲染 ----------

def _report_markdown(
    period: str, kind: str, facts: dict, groups: list[dict], subscriptions: list[dict]
) -> str:
    """渲染账单报告 Markdown。"""
    lines = [f"# {period} {KIND_LABELS.get(kind, '')}账单报告", ""]

    lines.append("## 收支概览")
    lines.append(f"- 支出：{facts['out_sum_yuan']} 元（{facts['out_count']} 笔）")
    lines.append(f"- 收入：{facts['in_sum_yuan']} 元（{facts['in_count']} 笔）")
    lines.append(f"- 结余：{facts['net_yuan']} 元")
    lines.append("")

    if facts["vs_prev_pct"] is not None:
        change = "增加" if facts["vs_prev_pct"] >= 0 else "减少"
        lines.append(f"- 较上期：{change} {abs(facts['vs_prev_pct'])}%")
    else:
        lines.append("- 较上期：无数据")
    lines.append("")

    lines.append("## 支出分类")
    for group in groups:
        lines.append(f"- {group['key']}: {group['amount_yuan']} 元 ({group['pct']}%)")
    lines.append("")

    if subscriptions:
        lines.append("## 订阅服务")
        for sub in subscriptions:
            lines.append(
                f"- {sub['merchant']}: {sub['amount_yuan']} 元/{sub['cycle']}"
            )
        lines.append("")

    if facts["anomaly_count"] > 0:
        lines.append(f"## 异常提醒")
        lines.append(f"发现 {facts['anomaly_count']} 笔异常交易，请关注。")
        lines.append("")

    lines.append("---")
    lines.append("*数据仅供参考*")

    return "\n".join(lines)
