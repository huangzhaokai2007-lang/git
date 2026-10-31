"""T26 理财持仓查询（L0 只读）—— 用户需求「一键得知自己目前有什么理财产品还在订阅中」。

**为什么单独一个文件**：`tools/wealth.py` 已 284 行（红线 300），装不下持仓查询；
按本仓拆文件先例（`_wealth_risk` / `_query_analysis`）拆出，`tools/wealth.py` re-export，
调用方 import 路径不变。

口径：
- **只读当前 user 的 `status='held'` 持仓**（已全部赎回的不算「还在」）；归属 fail-closed；
- 金额一律整数分 + `_money_facts` 的「元」展示形态；年化照抄 `_yield_text` 的百分数字符串；
- 产品名/风险等级/期限 JOIN `wealth_product` 取，**不在编排层拼**。
"""

from __future__ import annotations

from datetime import date, timedelta

from data import dao
from data.seed import AS_OF
from tools._query_common import ToolError, _dao_reject, _fail, _money_facts, _ok, current_user_id
from tools import _wealth_products
from tools._wealth_risk import RISK_LABELS, _rank, _yield_text, require_assessment
from tools.schemas import ErrorCode, ToolResult

TOOL = "list_holdings"
INTENT = "wealth_holdings"
TIER = "L0"

#: 一行持仓的字段（编排层渲染与事实包共用，避免两处各写一份键名）
FIELDS = ("holding_id", "product_id", "name", "risk_level", "yield", "term_days", "purchase_date")


def _income_facts(amount_cents: int, expected_yield: float, term_days: int,
                  purchase_date: str) -> dict:
    """持仓的收益预估（**全部在工具层算**，界面只排版 —— 铁律 2）。

    - `annual_income_yuan`：按当前持有金额放满一年的收益；
    - `maturity_date` / `remaining_days`：到期日与剩余天数（活期无到期日）；
    - `to_maturity_income_yuan`：从现在持有到期的预估收益（活期为空）。

    口径写死在这里，别在界面再乘一遍：金额 × 年化 ÷ 100 × 剩余天数 ÷ 365。
    演示数据没有复利、没有费用，回执里已注明「模拟」。
    """
    amount_yuan = amount_cents / 100
    annual = amount_yuan * expected_yield / 100
    facts = {"annual_income_yuan": f"{annual:,.2f}"}
    if term_days <= 0:                                  # 活期：没有到期日
        return {**facts, "maturity_date": "", "remaining_days": 0,
                "to_maturity_income_yuan": ""}
    maturity = date.fromisoformat(purchase_date) + timedelta(days=term_days)
    remaining = max(0, (maturity - AS_OF).days)
    to_maturity = annual * remaining / 365
    return {**facts, "maturity_date": maturity.isoformat(), "remaining_days": remaining,
            "to_maturity_income_yuan": f"{to_maturity:,.2f}"}


def _rows(user_id: str) -> list[dict]:
    """当前用户 `held` 状态的持仓，**按产品合并**（同一产品多次申购只显示一行）。

    合并口径：金额求和、买入日取最早的一笔（"你从什么时候开始持有"）。不合并的话
    同一只基金会出现多行（每笔申购一行），用户看不出自己到底持有多少。
    """
    conn = dao.connection()
    return [dict(row) for row in conn.execute(
        "SELECT MIN(h.id) AS holding_id, h.product_id, SUM(h.amount) AS amount,"
        "       MIN(h.purchase_date) AS purchase_date,"
        "       p.name, p.risk_level, p.expected_yield, p.term_days"
        "  FROM holding h JOIN wealth_product p ON p.id = h.product_id"
        " WHERE h.user_id = ? AND h.status = 'held'"
        " GROUP BY h.product_id, p.name, p.risk_level, p.expected_yield, p.term_days"
        " ORDER BY MIN(h.purchase_date), h.product_id", (user_id,)).fetchall()]


def list_all_products() -> ToolResult:
    """T27 列出**全部**理财产品并标注是否在用户风险等级内（L0 只读，**不做推荐排序**）。

    用户需求：「除了推荐的，还能看到其他风险承受能力之外的产品，但不做推荐」。
    与 T14 的区别：T14 **只返回**等级内的产品且按收益降序（那是"推荐"）；
    本工具返回全部，用 `recommended` 标记划出"适合您"与"超出您等级"两组，
    **排序按风险等级从低到高**（不做收益排序，避免变成变相推荐）。
    未测评 → `INVALID_STATE`（编排层会引导去做测评）。
    """
    try:
        level = require_assessment()["risk_level"]
        user_rank = _rank(level)
        products = dao.list_products()
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    items = [{"product_id": row["id"], "name": row["name"], "risk_level": row["risk_level"],
              "yield": _yield_text(row["expected_yield"]), "term": row["term_days"],
              "recommended": _rank(row["risk_level"]) <= user_rank,
              **_money_facts(int(row["min_amount"]), "min_amount")} for row in products]
    items.sort(key=lambda item: _rank(item["risk_level"]))
    fit = sum(1 for item in items if item["recommended"])
    #: facts 里的每只产品带上"类型 + 结构化条款"（界面据此排版）；`data` 契约不含这两项
    #:  —— 它们是展示口径，不是对外契约，改契约要动规格。
    facts = {"items": [_wealth_products.merge(item) for item in items],
             "total_count": len(items), "fit_count": fit,
             "user_risk_level": level, "user_risk_label": RISK_LABELS.get(level, level)}
    data = {"items": items, "total_count": len(items), "fit_count": fit,
            "user_risk_level": level}
    return _ok(data, facts, f"共 {len(items)} 只产品，其中 {fit} 只在您的风险等级"
                            f"（{facts['user_risk_label']}）内。")


def list_holdings() -> ToolResult:
    """列出当前用户的理财持仓（L0 只读）。`data` = `items[]` / `total_count` / `total_amount`。

    没有持仓 → `ok=True` + 空列表（**不是错误**：用户只是还没买过，回执要说清）。
    """
    try:
        rows = _rows(current_user_id())
    except ValueError as exc:
        return _dao_reject(exc)
    items = [{"holding_id": row["holding_id"], "product_id": row["product_id"],
              "name": row["name"], "risk_level": row["risk_level"],
              "yield": _yield_text(row["expected_yield"]), "term_days": row["term_days"],
              "purchase_date": row["purchase_date"],
              **_money_facts(int(row["amount"]), "amount"),
              **_income_facts(int(row["amount"]), float(row["expected_yield"]),
                              int(row["term_days"] or 0), row["purchase_date"])}
             for row in rows]
    total = sum(int(row["amount"]) for row in rows)
    facts = {"items": items, "total_count": len(items), **_money_facts(total, "total_amount")}
    data = {"items": [{key: item[key] for key in FIELDS + ("amount", "amount_yuan")}
                      for item in items],
            "total_count": len(items), "total_amount": total}
    if not items:
        message = "您目前没有持有中的理财产品。"
    else:
        message = (f"您目前持有 {len(items)} 只理财产品，合计 {facts['total_amount_yuan']} 元："
                   + "；".join(f"{item['name']} {item['amount_yuan']} 元" for item in items))
    return _ok(data, facts, message)
