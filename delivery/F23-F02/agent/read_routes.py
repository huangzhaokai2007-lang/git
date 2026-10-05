"""编排层**只读路由表**（卡 23 从 `agent/orchestrator.py` 拆出，照 `agent/payee_flow.py` 的先例）。

为什么单开一个文件：`orchestrator.py` 已 293 行（红线 300），而卡 23 要把 `card_query` 接上
T18 `list_cards`；照卡 23 的授权把「只读意图 → 工具」这张表整体挪出来，`orchestrator` 反向 import
并 re-export（历史调用点写 `orchestrator.TOOL_ROUTES` 仍然有效，指向同一份表 —— 同一份实现，不复制）。

铁律 1 / 卡 09 禁止项：表里**只有只读工具**；写操作一概不在（`agent/write_flow.py` 是写路径的唯一归属，
`tests/test_orchestrator_readonly.py` 有静态红线钉住这条）。

分层：本模块属编排层，只 import `tools/`；不碰 `guard/`、不写 SQL。
"""

from __future__ import annotations

from typing import Callable

from tools import query, subscription, wealth
from tools.card_query import list_cards
from tools.schemas import ToolResult

#: 只读工具的翻页缺省（来源：规格 §2 T2 `limit=50`）；随路由表一起搬过来，表内自洽。
LIMIT_DEFAULT = 50

#: 意图 → (工具名, 调用器)。**只读意图在这里，写操作一概不在**（卡 09 禁止项）。
#: `card_query` 由卡 23 接通（T18 `list_cards`，L0）；槽位只有 `status`（可选，不传 = 全部）。
TOOL_ROUTES: dict[str, tuple[str, Callable[[dict], ToolResult]]] = {
    "balance_query": ("get_balance", lambda s: query.get_balance(s.get("account_type") or "savings")),
    "txn_query": ("list_txn", lambda s: query.list_txn(s["date_from"], s["date_to"],
                                                      category=s.get("category"),
                                                      min_amount=s.get("min_amount"),
                                                      limit=s.get("limit", LIMIT_DEFAULT))),
    "bill_analysis": ("analyze_spending", lambda s: query.analyze_spending(s["period"], s.get("group_by") or "category")),
    "anomaly_check": ("detect_anomalies", lambda s: query.detect_anomalies(s["period"])),
    "bill_report": ("generate_bill_report", lambda s: query.generate_bill_report(s["period"], s.get("kind") or "monthly")),
    "subscription_list": ("list_subscriptions", lambda s: subscription.list_subscriptions(s.get("status") or "active")),
    "card_query": ("list_cards", lambda s: list_cards(s.get("status"))),
    "wealth_recommend": ("recommend_wealth", lambda s: wealth.recommend_wealth(
        s.get("risk_level"), s.get("horizon_days"), s.get("amount"))),
}
