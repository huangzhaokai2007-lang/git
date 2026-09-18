"""工具层 T15（任务卡 07）：理财申购 / 赎回（L2 高危写）。

T13/T14（风险评估 + 推荐）在同族的 `tools/_wealth_risk.py`，本文件 **re-export** 它们，
调用方 `from tools.wealth import assess_risk` 的路径不变（规格冻结的是函数名，不是文件）。

依据 `docs/01-接口规格.md` §2 T15、§5 权限矩阵、`CLAUDE.md` 铁律 + 卡 07 第 3 条：
- 买入校验：测评有效 → 风险等级匹配（**绝不推荐/申购超风险等级产品**）→ 起购额 → 余额充足；
- 赎回规则：持有天数 ≥ `wealth_product.term_days`（口径见下）；
- 走 `confirm_ref` 确认闭环（**直接 import `tools/subscription.py` 的机制，卡 06 先例，不复制第三份**）+
  写 `audit_log` + 金额整数分 + 单一事务（复用 DAO `_writing()` 的 `in_transaction` 检测，不嵌套 BEGIN）。

口径（规格未定义处，逐条进交付说明的「需要人类决定」）：
- **赎回规则**：DDL 只有 `term_days`、无赎回规则表 → 口径 =「持有天数 ≥ `term_days` 才可赎回」，
  未到期 → `INVALID_STATE`。规则源待补 → `TODO(07b)`。
- **用户风险等级存哪**：DDL 无存储表、规格 T15 签名也没有 risk 参数 → 测评结果存进程内私有存储
  （`_wealth_risk._ASSESSMENTS`，同族私有约定）；T15 据此校验「风险等级匹配」。持久化 → `TODO(07b)`。
- 买入/赎回**写流水**（`category='理财'`）以保证 T1/T2 的余额与流水自洽；
  `expected_confirm_date` = 交易日 + 1 天（T+1）；**时间锚 = `data.seed.AS_OF`**（跨天可复现）。
- 买入金额 < 起购额 / 赎回金额 > 持仓 → `INVALID_ARGUMENT`；风险等级不匹配 / 账户非本人 → `FORBIDDEN`
  （并按 reviewer 口径写 rejected 审计）；余额不足 → `INSUFFICIENT_FUNDS`。
- L2 需 OTP，但规格 T15 签名无 `otp` 参数（同 T11）→ 校验落在编排层确认卡环节，本层在 facts 给
  `requires_otp=True`。
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from data import dao
from data.db import transaction
from data.seed import AS_OF
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money_facts, _ok, current_user_id,
)
from tools._wealth_risk import (             # noqa: F401 —— assess_risk/recommend_wealth/_ASSESSMENTS 为 re-export
    _ASSESSMENTS, _rank, assess_risk, recommend_wealth, require_assessment,
)
from tools.schemas import ErrorCode, ToolResult
from tools.subscription import (
    _CONFIRM_LOCK, check_confirm_ref, confirmed_result, finish_confirm_ref, reject_audit,
)

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
TRADE_TIER = "L2"                                 # 来源：规格 §2 T15 权限档列 = L2（§5：申购赎回 = 确认卡 + OTP）
TRADE_ACTION = "trade_wealth"                     # 凭证绑定的 action 名（与工具名一致）
CONFIRM_DAYS = 1                                  # 来源：规格 T15 的 expected_confirm_date；口径 = T+1 确认

STRICT = ConfigDict(strict=True, extra="forbid")


# ---------------- 出入参模型（卡 07 范围不扩 schemas.py → 模型放本族文件内，07b 归位） ----------------

class TradeWealthReq(BaseModel):
    model_config = STRICT

    product_id: str = Field(min_length=1)
    action: Literal["buy", "redeem"]
    amount: int = Field(gt=0)
    confirm_ref: str = Field(min_length=1)


class TradeWealthData(BaseModel):
    order_id: str
    product_name: str
    amount: int
    expected_confirm_date: str


# ---------------- 内部辅助 ----------------

def _holdings(product_id: str) -> list[dict]:
    """该用户指定产品的持仓。TODO(07b)：DAO 补 `list_holdings`/`insert_holding`，现走 DAO 自己的连接。"""
    conn = dao.connection()
    rows = conn.execute("SELECT * FROM holding WHERE user_id = ? AND product_id = ? AND status = 'held'"
                        " ORDER BY id", (current_user_id(), product_id)).fetchall()
    return [dict(row) for row in rows]


def _buy_plan(product: dict, amount: int) -> dict:
    """买入前置校验（测评有效 → 风险等级匹配 → 起购额 → 账户归属 → 余额）并给出执行计划。"""
    assessment = require_assessment()
    if _rank(product["risk_level"]) > _rank(assessment["risk_level"]):
        raise ToolError(ErrorCode.FORBIDDEN,
                        f"该产品风险等级 {product['risk_level']} 高于您的等级 {assessment['risk_level']}")
    if amount < product["min_amount"]:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "申购金额低于该产品的起购金额")
    account = dao.get_balance("savings")
    if account is None or account["user_id"] != current_user_id():
        raise ToolError(ErrorCode.FORBIDDEN, "储蓄账户不属于当前用户")
    if account["balance"] < amount:
        raise ToolError(ErrorCode.INSUFFICIENT_FUNDS, "储蓄账户余额不足")
    return {"account_id": account["id"], "delta": -amount, "amount": amount,
            "user_risk_level": assessment["risk_level"]}


def _redeem_plan(product: dict, amount: int) -> dict:
    """赎回前置校验（持仓存在 → 已过封闭期 → 金额不超持仓 → 账户归属）并给出执行计划。"""
    holdings = _holdings(product["id"])
    if not holdings:
        raise ToolError(ErrorCode.NOT_FOUND, "没有该产品的可赎回持仓")
    holding = holdings[0]
    held_days = (AS_OF - date.fromisoformat(holding["purchase_date"])).days
    if held_days < product["term_days"]:
        raise ToolError(ErrorCode.INVALID_STATE,
                        f"该产品处于封闭期（持有 {held_days} 天 < {product['term_days']} 天）")
    if amount > holding["amount"]:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "赎回金额超过持仓金额")
    account = dao.get_balance("savings")
    if account is None or account["user_id"] != current_user_id():
        raise ToolError(ErrorCode.FORBIDDEN, "储蓄账户不属于当前用户")
    return {"account_id": account["id"], "delta": amount, "amount": amount, "holding": holding}


def _write_holding(action: str, product: dict, plan: dict, conn: sqlite3.Connection) -> str:
    """落持仓：买入新增一行；赎回扣减（归零则置 `redeemed`）。TODO(07b)：DAO 补持仓原语。"""
    if action == "buy":
        order_id = f"holding_{uuid.uuid4().hex[:12]}"
        conn.execute("INSERT INTO holding (id, user_id, product_id, amount, purchase_date, status)"
                     " VALUES (?, ?, ?, ?, ?, 'held')",
                     (order_id, current_user_id(), product["id"], plan["amount"], AS_OF.isoformat()))
        return order_id
    holding = plan["holding"]
    remaining = holding["amount"] - plan["amount"]
    conn.execute("UPDATE holding SET amount = ?, status = ? WHERE id = ?",
                 (remaining, "redeemed" if remaining == 0 else "held", holding["id"]))
    return holding["id"]


# ---------------- T15 trade_wealth ----------------

def trade_wealth(product_id: str, action: str, amount: int, confirm_ref: str) -> ToolResult:
    """T15 理财申购/赎回（L2 高危写）。data: `order_id` / `product_name` / `amount` / `expected_confirm_date`。

    顺序：参数 → 产品存在 → 凭证（`TOKEN_EXPIRED`/`FORBIDDEN`）→ 已执行则返回快照（**幂等**）
    → 业务校验（买入：测评有效 + 风险等级匹配 + 起购额 + 余额；赎回：持仓 + 封闭期 + 金额）
    → 事务内（持仓 + 余额 + 流水 + 审计）→ 写快照。`confirm_ref` 来自确认卡（本层签发校验，同 card-06）。
    """
    if (bad := _invalid(TradeWealthReq, product_id=product_id, action=action, amount=amount,
                        confirm_ref=confirm_ref)) is not None:
        return bad
    with _CONFIRM_LOCK:                                   # 校验 → 执行 → 写快照，整体串行
        return _trade_locked(product_id, action, amount, confirm_ref)


def _execute_trade(action: str, product: dict, amount: int, plan: dict,
                   record: dict) -> dict:
    """单一事务内落库：持仓 + 余额 + 流水 + 审计（不嵌套 BEGIN，复用 DAO `_writing()` 的检测）。"""
    conn = dao.connection()
    with transaction(conn):
        order_id = _write_holding(action, product, plan, conn)
        account = dao.update_account_balance(plan["account_id"], plan["delta"])
        if account is None:                               # 防御性兜底，正常走不到
            raise ToolError(ErrorCode.NOT_FOUND, "储蓄账户不存在")
        verb = "申购" if action == "buy" else "赎回"
        txn = dao.insert_txn(plan["account_id"], f"{AS_OF.isoformat()}T00:00:00", plan["delta"],
                             "out" if action == "buy" else "in", account["balance"],
                             counterparty=product["name"], category="理财", channel="理财",
                             memo=f"{verb}{product['name']}")
        dao.insert_audit(record["trace_id"], record["session_id"], actor="agent",
                         intent=f"wealth_{action}", tool=TRADE_ACTION,
                         params_json={"product_id": product["id"], "action": action, "amount": amount,
                                      "tier": TRADE_TIER, "order_id": order_id, "txn_id": txn["id"],
                                      "risk_level": product["risk_level"]},
                         risk_level=TRADE_TIER, permission_tier=TRADE_TIER, result="success")
    return {"order_id": order_id, "account": account, "txn": txn}


def _trade_locked(product_id: str, action: str, amount: int, confirm_ref: str) -> ToolResult:
    """`trade_wealth` 的临界区主体；调用方必须已持有 `_CONFIRM_LOCK`。"""
    try:
        product = dao.get_product(product_id)
        if product is None:
            return _fail(ErrorCode.NOT_FOUND, "找不到该理财产品")
        record = check_confirm_ref(confirm_ref, TRADE_ACTION, product_id)
        if (cached := confirmed_result(record)) is not None:
            return cached
        plan = _buy_plan(product, amount) if action == "buy" else _redeem_plan(product, amount)
        written = _execute_trade(action, product, amount, plan, record)
    except ToolError as exc:
        if exc.code == ErrorCode.FORBIDDEN:               # 越权/伪造/风险不匹配 → 留痕（reviewer 口径）
            reject_audit(TRADE_ACTION, f"wealth_{action}", product_id, exc.message, TRADE_TIER)
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    account, txn = written["account"], written["txn"]
    order_id = written["order_id"]
    data = TradeWealthData(order_id=order_id, product_name=product["name"], amount=amount,
                           expected_confirm_date=(AS_OF + timedelta(days=CONFIRM_DAYS)).isoformat())
    facts = {"order_id": order_id, "product_id": product_id, "product_name": product["name"],
             "action": action, "tier": TRADE_TIER, "requires_otp": True, "txn_id": txn["id"],
             "product_risk_level": product["risk_level"],
             "expected_confirm_date": data.expected_confirm_date, "status": "success",
             **_money_facts(amount, "amount"), **_money_facts(account["balance"], "balance_after")}
    message = (f"已{'申购' if action == 'buy' else '赎回'} {product['name']} {facts['amount_yuan']} 元，"
               f"预计确认日 {facts['expected_confirm_date']}，账户余额 {facts['balance_after_yuan']} 元。")
    finish_confirm_ref(confirm_ref, data.model_dump(), facts, message)
    return _ok(data.model_dump(), facts, message)
