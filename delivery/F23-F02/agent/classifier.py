"""意图分类器（占位）。"""

from __future__ import annotations

from dataclasses import dataclass

INTENT_LABELS = [
    "balance_query", "txn_query", "bill_analysis", "anomaly_check",
    "bill_report", "subscription_list", "card_query", "wealth_recommend",
    "transfer_single", "transfer_scheduled", "payee_add", "payee_resolve",
    "aa_collect", "card_apply", "card_limit", "card_lock", "card_unlock",
    "card_lost", "cancel_subscription", "manage_card", "trade_wealth",
    "plan_gift", "create_aa_request", "assess_risk",
    "smalltalk", "out_of_scope", "unsafe_request",
]


@dataclass
class IntentOut:
    intent: str
    confidence: float
    slots: dict
    missing_slots: list[str]
    unsafe_reason: str | None = None


def classify(text: str, history: list[str] | None = None) -> IntentOut:
    """分类用户意图（简化版）。"""
    text = text.lower()
    slots = {}

    if any(kw in text for kw in ["余额", "多少钱", "还剩"]):
        return IntentOut("balance_query", 0.95, {"account_type": "savings"}, [])

    if any(kw in text for kw in ["流水", "交易", "明细"]):
        return IntentOut("txn_query", 0.92, {}, ["date_from", "date_to"])

    if any(kw in text for kw in ["账单", "花了多少", "支出"]):
        return IntentOut("bill_analysis", 0.88, {"period": "2026-09"}, [])

    if any(kw in text for kw in ["异常", "可疑"]):
        return IntentOut("anomaly_check", 0.85, {"period": "2026-09"}, [])

    if any(kw in text for kw in ["报告", "报表"]):
        return IntentOut("bill_report", 0.87, {"period": "2026-09"}, [])

    if any(kw in text for kw in ["订阅", "会员"]):
        return IntentOut("subscription_list", 0.90, {}, [])

    if any(kw in text for kw in ["卡", "卡片"]):
        return IntentOut("card_query", 0.88, {}, [])

    if any(kw in text for kw in ["理财", "投资", "推荐"]):
        return IntentOut("wealth_recommend", 0.86, {"risk_level": "R3"}, [])

    if any(kw in text for kw in ["转账", "汇款"]):
        return IntentOut("transfer_single", 0.90, {}, [])

    if any(kw in text for kw in ["危险", "破解", "绕过"]):
        return IntentOut("unsafe_request", 0.95, {}, [], "检测到不安全请求")

    return IntentOut("out_of_scope", 0.3, {}, [])
