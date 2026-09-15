"""工具层 T1–T5（任务卡 04；卡 05b 拆分后）：只读查询工具，统一返回 `ToolResult`（含 facts 事实包）。

分析内核（账期换算 / 占比 / 异常规则 / 报告渲染）与阈值常量在 `tools/_query_analysis.py`；
共享薄封装与归属断言在 `tools/_query_common.py`。本模块只留 5 个公开工具，依赖单向
`query → _query_analysis → _query_common`。

依据 `docs/01-接口规格.md` 第 2 节（函数名/字段名冻结）+ 第 6 节 L2（越权防护）+ `CLAUDE.md` 铁律：
- **LLM 不参与任何计算**：金额、占比、异常判定全部由整数运算决定。
- 金额一律整数分，全程不出现 float。
- 错误消息**一律不含数字**：回执的每个数字都必须能在 facts 里找到。
- 资源归属：DAO 不按 user 圈定（账本风险台账已登记），本层做归属断言，越权 → `FORBIDDEN`。
"""

from __future__ import annotations

import logging
from datetime import datetime
import re
from data import dao

from tools._query_analysis import (
    AMOUNT_RATIO_THRESHOLD, BASELINE_DAYS, KIND_LABELS, NIGHT_END_HOUR, NIGHT_START_HOUR, VELOCITY_MINUTES_T4,
    VELOCITY_MIN_TXNS, _anomalies, _owned_txns, _period_bounds, _previous_period, _report_markdown, _scan,
    _spend_groups, _split_pct, _vs_prev_pct,
)
from tools._query_common import (
    PCT_TOTAL, ToolError, _dao_reject, _fail, _invalid, _money, _money_facts, _ok, _owned_account_ids,
    require_owned, set_current_user, current_user_id, set_current_user,
)
from tools.schemas import (
    AnalyzeSpendingReq, AnomaliesData, AnomalyItem, BalanceData, BillReportData, DetectAnomaliesReq, ErrorCode,
    GenerateBillReportReq, GetBalanceReq, ListTxnData, ListTxnReq, MAX_LIMIT, SpendingData, SpendingGroup,
    ToolResult, TxnItem,
)

logger = logging.getLogger(__name__)

def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")

def _owned_subscriptions() -> list[dict]:
    """当前用户的进行中订阅（按 user_id 显式查询 → 归属由构造保证，天然无越权）。"""
    return dao.list_subscriptions(current_user_id(), "active")

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
             "velocity_window_minutes": VELOCITY_MINUTES_T4, "velocity_min_txns": VELOCITY_MIN_TXNS,
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


ACCOUNT_LABELS = {"savings": "储蓄账户", "credit": "信用账户"}

PERIOD_KINDS = {"monthly": re.compile(r"^\d{4}-\d{2}$"), "yearly": re.compile(r"^\d{4}$")}
