"""工具层 T1–T5（任务卡 04）：只读查询工具，统一返回 `ToolResult`（含 facts 事实包）。

依据 `docs/01-接口规格.md` 第 2 节（函数名/字段名冻结）+ 第 6 节 L2（越权防护）+ `CLAUDE.md` 铁律：
- **LLM 不参与任何计算**：金额、占比、异常判定全部由本模块的整数运算决定。
- **金额一律整数分**，全程不出现 float（含均值、百分比：用整除 + 最大余额法）。
- 只允许调 `data/`（DAO）；不写 SQL、不做权限档判定（那是 `guard/` 的活）。
- 本模块的**阈值常量**（文件头集中声明，逐条注明来源）之外不得再出现业务数字字面量。
- 错误消息**一律不含数字**：回执的每个数字都必须能在 facts 里找到，参数回显会污染数字校验器
  （详见 `guard/facts_check.py`，卡 13），故明细只写 logging。
- 资源归属：DAO 不按 user 圈定（账本风险台账已登记），本层做归属断言，越权 → `FORBIDDEN`。
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from datetime import date, datetime, timedelta

from data import dao
from tools._query_common import (PCT_TOTAL, ToolError, _dao_reject, _fail, _invalid, _money,
                                 _money_facts, _ok, _owned_account_ids, current_user_id,
                                 require_owned, set_current_user)
from tools.schemas import (MAX_LIMIT, AnalyzeSpendingReq, AnomaliesData, AnomalyItem,
                           BalanceData, BillReportData, DetectAnomaliesReq, ErrorCode,
                           GenerateBillReportReq, GetBalanceReq, ListTxnData, ListTxnReq,
                           SpendingData, SpendingGroup, ToolResult, TxnItem)

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
AMOUNT_RATIO_THRESHOLD = 3          # 来源：规格 §2 T4 备注（>近 90 天支出均值 3 倍）；§5 的 amount_jump 5 倍=写操作降级因子，场景不同
BASELINE_DAYS = 90                  # 来源：卡 04 第 3 条「近 90 天」；基准只取**支出**流水（见 _baseline_series）
NIGHT_START_HOUR, NIGHT_END_HOUR = 23, 6   # 来源：规格 §5「night(23:00-06:00)」
VELOCITY_WINDOW_MINUTES = 60        # 来源：卡 04 第 3 条「同商户 1 小时内」
VELOCITY_MIN_TXNS = 3               # 来源：卡 04 第 3 条「≥3 笔」
ACCOUNT_LABELS = {"savings": "储蓄账户", "credit": "信用账户"}
CYCLE_LABELS = {"monthly": "每月", "yearly": "每年"}
KIND_LABELS = {"monthly": "月度", "yearly": "年度"}
PERIOD_KINDS = {"monthly": re.compile(r"^\d{4}-\d{2}$"), "yearly": re.compile(r"^\d{4}$")}
REASON_AMOUNT_JUMP, REASON_NIGHT, REASON_VELOCITY = "金额显著高于近期均值", "凌晨时段交易", "同商户短时密集交易"

_SESSION_USER: str | None = None
_PERIOD_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")


# ToolError / set_current_user / current_user_id / require_owned 与会话用户上下文，
# 以及 _ok/_fail/_invalid/_dao_reject/_money/_money_facts 已收敛到 tools/_query_common.py
# （卡 04b：单份实现，禁复制；本模块只 import 复用）。


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
    """把日期区间切成按月的小窗口（DAO 单次翻页上限 500 行，按窗口取可绕开截断）。"""
    windows, cursor = [], start
    while cursor <= end:
        last = min(_next_month(cursor) - timedelta(days=1), end)
        windows.append((cursor.isoformat(), last.isoformat()))
        cursor = last + timedelta(days=1)
    return windows


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


def _anomalies(rows: list[dict], series: list[tuple[datetime, int]]) -> list[dict]:
    """三条规则（口径见文件头常量）；severity 由**命中规则条数**决定：≥2 high，1 medium。

    `series` 是基准序列（该用户**支出**流水的 (时间, 绝对金额)，按时间升序）；
    每笔交易的基准是它自己"近 90 天"（不含本笔）的均值 —— 规格字面口径，
    与"用哪个账期来看"无关，故同一笔交易在任何账期下判定一致。
    """
    velocity = _velocity_hits(rows)
    items: list[dict] = []
    for row in sorted(rows, key=lambda item: (item["ts"], item["id"])):
        baseline = _baseline_for(_dt(row["ts"]), series)
        hits = []
        if baseline > 0 and -row["amount"] > baseline * AMOUNT_RATIO_THRESHOLD:
            hits.append(REASON_AMOUNT_JUMP)
        if _is_night(row["ts"]):
            hits.append(REASON_NIGHT)
        if row["id"] in velocity:
            hits.append(REASON_VELOCITY)
        if hits:
            items.append({"txn_id": row["id"], "reason": "、".join(hits),
                          "severity": "high" if len(hits) > 1 else "medium",
                          "amount": row["amount"], "baseline_mean": baseline})
    return items


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


def _scan(start: date, end: date) -> tuple[list[dict], list[tuple[datetime, int]]]:
    """分析期流水 + 基准序列：一次取 [分析期起点 - BASELINE_DAYS 天, 分析期终点] 的流水，
    期内的作扫描对象，全部作基准池（保证每笔都能看到自己往前 90 天的样本）。
    """
    pool = _owned_txns((start - timedelta(days=BASELINE_DAYS)).isoformat(), end.isoformat())
    inside = [row for row in pool if start.isoformat() <= row["ts"][:10] <= end.isoformat()]
    return inside, _baseline_series(pool)


def _is_night(ts: str) -> bool:
    hour = _dt(ts).hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def _velocity_hits(rows: list[dict]) -> set[str]:
    """同商户 (counterparty) 在 VELOCITY_WINDOW_MINUTES 分钟内出现 ≥VELOCITY_MIN_TXNS 笔 → 窗口内每笔命中。

    无对手方名的流水（counterparty 为空）不参与该规则：没有商户可归组。
    """
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        if row["counterparty"]:
            buckets.setdefault(row["counterparty"], []).append(row)
    window = timedelta(minutes=VELOCITY_WINDOW_MINUTES)
    hits: set[str] = set()
    for bucket in buckets.values():
        ordered = sorted(bucket, key=lambda row: (row["ts"], row["id"]))
        for index, first in enumerate(ordered):
            group = [row for row in ordered[index:] if _dt(row["ts"]) - _dt(first["ts"]) < window]
            if len(group) >= VELOCITY_MIN_TXNS:
                hits.update(row["id"] for row in group)
    return hits


def _owned_subscriptions() -> list[dict]:
    """当前用户的进行中订阅（按 user_id 显式查询 → 归属由构造保证，天然无越权）。"""
    return dao.list_subscriptions(current_user_id(), "active")


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


# ---------------- T1–T5 公开工具 ----------------

def get_balance(account_type: str) -> ToolResult:
    """T1 余额查询（L0）。data: `balance` / `available` / `as_of`（金额为整数分）。

    归属断言：DAO 的 `get_balance` 不按 user 圈定，返回行不属于当前用户 → `FORBIDDEN`。
    """
    if (bad := _invalid(GetBalanceReq, account_type=account_type)) is not None:
        return bad
    try:
        row = dao.get_balance(account_type)
    except ValueError as exc:
        return _dao_reject(exc)
    if row is None:
        return _fail(ErrorCode.NOT_FOUND, "没有这类账户")
    try:
        require_owned("账户", row["user_id"], row["id"])
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    facts = {**_money_facts(row["balance"], "balance"), **_money_facts(row["available"], "available"),
             "as_of": _stamp()}
    data = BalanceData(balance=row["balance"], available=row["available"], as_of=facts["as_of"])
    message = (f"{ACCOUNT_LABELS[account_type]}余额 {facts['balance_yuan']} 元，"
               f"可用 {facts['available_yuan']} 元。")
    return _ok(data.model_dump(), facts, message)


def list_txn(date_from: str, date_to: str, category: str | None = None,
             min_amount: int | None = None, limit: int = 50) -> ToolResult:
    """T2 流水查询（L0）。data: `items` / `total_count`；`total_count` 不受 `limit` 影响。

    `min_amount` 按金额**绝对值**过滤（分，≥），`category` 精确匹配；只返回当前用户账户的流水。
    """
    if (bad := _invalid(ListTxnReq, date_from=date_from, date_to=date_to, category=category,
                        min_amount=min_amount, limit=limit)) is not None:
        return bad
    try:
        rows = _owned_txns(date_from, date_to)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    if category is not None:
        rows = [row for row in rows if row["category"] == category]
    if min_amount is not None:
        rows = [row for row in rows if abs(row["amount"]) >= min_amount]
    rows.sort(key=lambda row: (row["ts"], row["id"]), reverse=True)
    page = rows[:limit]
    facts = {"total_count": len(rows), "returned_count": len(page),
             **_money_facts(sum(-row["amount"] for row in page if row["amount"] < 0), "page_out_sum"),
             **_money_facts(sum(row["amount"] for row in page if row["amount"] > 0), "page_in_sum"),
             "items": [{"id": row["id"], "ts": row["ts"], **_money_facts(row["amount"], "amount")}
                       for row in page]}
    data = ListTxnData(items=[TxnItem(**row) for row in page], total_count=len(rows))
    if not page:
        return _ok(data.model_dump(), facts, "该区间没有符合条件的流水。")
    message = (f"共 {facts['total_count']} 笔流水，本次展示 {facts['returned_count']} 笔；"
               f"支出合计 {facts['page_out_sum_yuan']} 元，收入合计 {facts['page_in_sum_yuan']} 元。")
    return _ok(data.model_dump(), facts, message)


def analyze_spending(period: str, group_by: str = "category") -> ToolResult:
    """T3 账单分析（L0）。data: `groups[{key,amount,pct}]` / `total` / `vs_prev_pct`。

    口径（整数、无浮点）：只统计**支出**（amount<0）的绝对金额；`total` 为各组之和（分）；
    `pct` 为整数百分比，最大余额法保证和恒为 100；`vs_prev_pct` 对比**上一个同长度账期**，
    上期无支出 → `None`。`group_by` 支持 category / channel。
    """
    if (bad := _invalid(AnalyzeSpendingReq, period=period, group_by=group_by)) is not None:
        return bad
    try:
        start, end = _period_bounds(period)
        rows = _owned_txns(start.isoformat(), end.isoformat())
        prev_start, prev_end = _period_bounds(_previous_period(period))
        prev_total = _spend_groups(_owned_txns(prev_start.isoformat(), prev_end.isoformat()), group_by)[1]
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    totals, total = _spend_groups(rows, group_by)
    ranked = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    pcts = _split_pct([amount for _, amount in ranked], total)
    groups = [SpendingGroup(key=key, amount=amount, pct=pct).model_dump()
              for (key, amount), pct in zip(ranked, pcts)]
    facts = {"period": period.strip(), "group_by": group_by, "group_count": len(groups),
             **_money_facts(total, "total"), **_money_facts(prev_total, "prev_total"),
             "vs_prev_pct": _vs_prev_pct(total, prev_total),
             "groups": [{**group, "amount_yuan": _money(group["amount"])} for group in groups]}
    data = SpendingData(groups=groups, total=total, vs_prev_pct=facts["vs_prev_pct"])
    if not groups:
        return _ok(data.model_dump(), facts, "该账期没有支出记录。")
    change = ("上期没有可比支出" if facts["vs_prev_pct"] is None
              else f"较上一账期 {facts['vs_prev_pct']}%")
    message = f"{facts['period']} 支出合计 {facts['total_yuan']} 元，{change}。"
    return _ok(data.model_dump(), facts, message)


def detect_anomalies(period: str) -> ToolResult:
    """T4 异常检测（L0，纯规则，无任何 LLM 判断）。data: `items[{txn_id,reason,severity}]`。

    规则（常量见文件头，来源逐条注明）：金额偏离（>近 90 天支出均值 ×3，严格大于，无基准不判定）、
    凌晨时段（23:00–06:00）、同商户短时高频（60 分钟内 ≥3 笔）。severity 由命中规则条数决定。
    """
    if (bad := _invalid(DetectAnomaliesReq, period=period)) is not None:
        return bad
    try:
        start, end = _period_bounds(period)
        rows, series = _scan(start, end)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    items = _anomalies(rows, series)
    facts = {"period": period.strip(), "scanned_count": len(rows), "anomaly_count": len(items),
             "amount_ratio_threshold": AMOUNT_RATIO_THRESHOLD, "baseline_days": BASELINE_DAYS,
             "night_from_hour": NIGHT_START_HOUR, "night_to_hour": NIGHT_END_HOUR,
             "velocity_window_minutes": VELOCITY_WINDOW_MINUTES, "velocity_min_txns": VELOCITY_MIN_TXNS,
             "items": [{"txn_id": item["txn_id"], "severity": item["severity"], "reason": item["reason"],
                        **_money_facts(item["amount"], "amount"),
                        **_money_facts(item["baseline_mean"], "baseline_mean")} for item in items]}
    data = AnomaliesData(items=[AnomalyItem(txn_id=item["txn_id"], reason=item["reason"],
                                            severity=item["severity"]) for item in items])
    if not items:
        return _ok(data.model_dump(), facts, "该账期没有发现异常交易。")
    message = (f"扫描 {facts['scanned_count']} 笔流水，发现 {facts['anomaly_count']} 笔异常交易；"
               f"金额规则为单笔支出超过其近 {facts['baseline_days']} 天支出均值的 "
               f"{facts['amount_ratio_threshold']} 倍。")
    return _ok(data.model_dump(), facts, message)


def generate_bill_report(period: str, kind: str = "monthly") -> ToolResult:
    """T5 账单报告（L0）。data: `markdown` / `summary_numbers`。

    **幻觉红线**：markdown 里出现的每个数字都取自 facts（测试逐字比对）。
    `kind` 必须与 `period` 粒度一致（monthly↔YYYY-MM、yearly↔YYYY），否则 `INVALID_ARGUMENT`。
    订阅来自当前用户的进行中订阅（按 user_id 查询，天然不越权）。
    """
    if (bad := _invalid(GenerateBillReportReq, period=period, kind=kind)) is not None:
        return bad
    clean = period.strip() if isinstance(period, str) else ""
    if PERIOD_KINDS.get(kind) is None or not PERIOD_KINDS[kind].match(clean):
        logger.warning("kind 与 period 粒度不符：kind=%s period=%r", kind, period)
        return _fail(ErrorCode.INVALID_ARGUMENT, "报告类型与账期粒度不一致")
    try:
        start, end = _period_bounds(clean)
        rows, series = _scan(start, end)
        prev_start, prev_end = _period_bounds(_previous_period(clean))
        prev_total = _spend_groups(_owned_txns(prev_start.isoformat(), prev_end.isoformat()), "category")[1]
        subs = _owned_subscriptions()
        anomalies = _anomalies(rows, series)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    totals, total = _spend_groups(rows, "category")
    ranked = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    pcts = _split_pct([amount for _, amount in ranked], total)
    groups = [{"key": key, "amount": amount, "pct": pct, "amount_yuan": _money(amount)}
              for (key, amount), pct in zip(ranked, pcts)]
    out_sum = sum(-row["amount"] for row in rows if row["amount"] < 0)
    in_sum = sum(row["amount"] for row in rows if row["amount"] > 0)
    facts = {"period": clean, "kind": kind, "group_count": len(groups),
             **_money_facts(out_sum, "out_sum"), **_money_facts(in_sum, "in_sum"),
             **_money_facts(in_sum - out_sum, "net"), **_money_facts(prev_total, "prev_total"),
             "out_count": sum(1 for row in rows if row["amount"] < 0),
             "in_count": sum(1 for row in rows if row["amount"] > 0),
             "vs_prev_pct": _vs_prev_pct(out_sum, prev_total), "anomaly_count": len(anomalies),
             "groups": groups,
             "subscriptions": [{"id": sub["id"], "merchant": sub["merchant"], "cycle": sub["cycle"],
                                "next_charge_date": sub["next_charge_date"],
                                **_money_facts(sub["amount"], "amount")} for sub in subs]}
    markdown = _report_markdown(clean, kind, facts, groups, facts["subscriptions"])
    summary = {key: value for key, value in facts.items() if isinstance(value, int)}
    data = BillReportData(markdown=markdown, summary_numbers=summary)
    message = f"{facts['period']} {KIND_LABELS[kind]}账单：支出 {facts['out_sum_yuan']} 元，收入 {facts['in_sum_yuan']} 元。"
    return _ok(data.model_dump(), facts, message)
