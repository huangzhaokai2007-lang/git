"""数据层 DAO（任务卡 03；卡 05b 拆分后）：**只放 16 个公开读写函数**，一次只做一件事。

连接状态、写原语（`_insert`/`_apply_update`）与入参校验 helper 都在 `data/_dao_core.py`；
本模块反向 import 它们，并 re-export `connect_db` / `close` / `connection`（工具层仍从 `data.dao` 取用）。
依赖单向 `dao → _dao_core`，禁反向。

本模块的全局约定（各函数不再重复）：
- 不判权限档、限额、风控因子、收款人歧义（都是 `guard/` 的活）：只回答"库里有什么行"和"写入这一行"。
- 金额一律整数分（`int`）；float / bool / 字符串 → `ValueError`。读返回纯 dict（无 → `None` / `[]`）。
- 写操作按主键**幂等**：同 id 同内容 → 返回既有行且不产生第二行；同 id 异内容 → `ValueError`；
  写操作**不代写** `audit_log` —— 审计由编排层显式调 `insert_audit`。
- 枚举值取自 DDL 注释（savings|credit、normal|locked|lost|frozen、active|cancelled|paused、
  monthly|yearly、R1..R5、in|out、night|geo|device|velocity|amount_jump|new_payee、
  downgrade|block|to_human、L0..L3），越界 → `ValueError`。
"""

from __future__ import annotations

from datetime import date, timedelta
import uuid

from data._dao_core import (
    ACCOUNT_TYPES,
    ACTORS,
    AUDIT_RESULTS,
    CARD_STATUSES,
    DIRECTIONS,
    MAX_LIMIT,
    RISK_ACTIONS,
    RISK_FACTORS,
    RISK_LEVELS,
    SUBSCRIPTION_CYCLES,
    SUBSCRIPTION_STATUSES,
    TIERS,
    _AUDIT_COLUMNS,
    _RISK_COLUMNS,
    _TXN_COLUMNS,
    _apply_update,
    _cents,
    _choice,
    _insert,
    _iso_date,
    _json_text,
    _many,
    _one,
    _period_range,
    _stamp,
    _text,
    _writing,
    close,
    connect_db,
    connection,
    db_path,
)


def get_balance(account_type: str) -> dict | None:
    """按账户类型取账户行（savings|credit，同类型多条取 id 最小者）；`as_of` 等事实包字段由工具层组装（铁律 2）。"""
    return _one("SELECT * FROM account WHERE type = ? ORDER BY id LIMIT 1",
                (_choice(account_type, "account_type", ACCOUNT_TYPES),))


def list_txn(date_from: str, date_to: str, category: str | None = None,
             min_amount: int | None = None, limit: int = 50) -> dict:
    """按日期区间（含首尾）翻页取流水 → `{"items": [...], "total_count": n}`。

    区间按 `[date_from, date_to + 1 天)` 比较（`ts` 为 ISO8601 字符串）→ `date_to` 当天全含；
    `category` 精确匹配（取值开放，命中不到即空）；`min_amount` 按**金额绝对值**过滤（分，≥）；
    `items` 时间倒序（同秒按 id 倒序）；`total_count` 是过滤后总条数，不受 `limit` 影响。
    """
    start, end = _iso_date(date_from, "date_from"), _iso_date(date_to, "date_to")
    if start > end:
        raise ValueError(f"date_from({start}) 不能晚于 date_to({end})")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 必须是 1..{MAX_LIMIT} 的整数，收到 {limit!r}")
    upper = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    where, params = "ts >= ? AND ts < ?", [start, upper]
    if category is not None:
        where, params = where + " AND category = ?", [*params, _text(category, "category")]
    if min_amount is not None:
        floor = _cents(min_amount, "min_amount")
        if floor < 0:
            raise ValueError(f"min_amount 不能为负，收到 {min_amount!r}")
        where, params = where + " AND (amount >= ? OR amount <= ?)", [*params, floor, -floor]
    total = _one(f"SELECT COUNT(*) AS n FROM txn WHERE {where}", tuple(params))
    items = _many(f"SELECT * FROM txn WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
                  (*params, limit))
    return {"items": items, "total_count": 0 if total is None else total["n"]}


def sum_by_category(period: str) -> list[dict]:
    """按账期汇总各分类 → `[{category, amount, count}]`（按分类排序）；`amount` 是**代数和**（分：支出为负、收入为正，符号含义由工具层解释），空账期 → `[]`。"""
    start, end = _period_range(period)
    return _many("SELECT category, COALESCE(SUM(amount), 0) AS amount, COUNT(*) AS count FROM txn"
                 " WHERE ts >= ? AND ts < ? GROUP BY category ORDER BY category", (start, end))


def find_payee(query: str) -> list[dict]:
    """按关键词模糊查收款人（姓名 / 手机号 / 银行任一**子串**命中）；返回全部命中行：可能同名多条，歧义判定在工具层，关键词里 `%` `_` 按字面量处理，空关键词 → `ValueError`。"""
    escaped = str(_text(query, "query")).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    return _many("SELECT * FROM payee WHERE name LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\'"
                 " OR bank LIKE ? ESCAPE '\\' ORDER BY name, id", (like, like, like))


def get_payee(payee_id: str) -> dict | None:
    """按 id 取单个收款人；不存在 → `None`。**不做归属判断**（越权判定是 `tools/guard` 的活）。"""
    return _one("SELECT * FROM payee WHERE id = ?", (_text(payee_id, "payee_id"),))


def get_card(card_id: str) -> dict | None:
    """取卡片行（含状态与各限额，整数分）；不存在 → `None`。"""
    return _one("SELECT * FROM card WHERE id = ?", (_text(card_id, "card_id"),))


def update_card(card_id: str, **fields: object) -> dict | None:
    """改卡片白名单字段（type/credit_limit/single_limit/daily_limit/status），返回改后整行。

    **不做权限或状态机判断**（给已挂失的卡改状态也照改，判断在 `guard/`）；卡片不存在 → `None`。
    """
    if "type" in fields:
        fields["type"] = _choice(fields["type"], "type", ACCOUNT_TYPES)
    if "status" in fields:
        fields["status"] = _choice(fields["status"], "status", CARD_STATUSES)
    for name in ("credit_limit", "single_limit", "daily_limit"):
        if name in fields:
            fields[name] = _cents(fields[name], name, allow_none=True)
    return _apply_update("card", str(_text(card_id, "card_id")), dict(fields))


def list_subscriptions(user_id: str, status: str) -> list[dict]:
    """按用户 + 状态（active|cancelled|paused）列订阅，按下次扣费日排序；无命中 → `[]`。"""
    return _many("SELECT * FROM subscription WHERE user_id = ? AND status = ?"
                 " ORDER BY next_charge_date, id",
                 (_text(user_id, "user_id"), _choice(status, "status", SUBSCRIPTION_STATUSES)))


def get_subscription(sub_id: str) -> dict | None:
    """取单条订阅；不存在 → `None`。"""
    return _one("SELECT * FROM subscription WHERE id = ?", (_text(sub_id, "sub_id"),))


def update_subscription(sub_id: str, **fields: object) -> dict | None:
    """改订阅白名单字段（merchant/amount/cycle/next_charge_date/source_txn_id/status），返回改后整行。

    「取消订阅」= 把 `status` 写成 `cancelled`（生效日与是否要 OTP 由工具层和 `guard/` 决定）；
    订阅不存在 → `None`；字段名不在白名单 / 值非法 → `ValueError`。
    """
    if "merchant" in fields:
        fields["merchant"] = _text(fields["merchant"], "merchant")
    if "amount" in fields:
        fields["amount"] = _cents(fields["amount"], "amount")
    if "cycle" in fields:
        fields["cycle"] = _choice(fields["cycle"], "cycle", SUBSCRIPTION_CYCLES)
    if "next_charge_date" in fields:
        fields["next_charge_date"] = _iso_date(fields["next_charge_date"], "next_charge_date")
    if "source_txn_id" in fields:
        fields["source_txn_id"] = _text(fields["source_txn_id"], "source_txn_id", allow_none=True)
    if "status" in fields:
        fields["status"] = _choice(fields["status"], "status", SUBSCRIPTION_STATUSES)
    return _apply_update("subscription", str(_text(sub_id, "sub_id")), dict(fields))


def list_products(risk_level: str | None = None) -> list[dict]:
    """列理财产品（可按 R1..R5 过滤），按起购金额排序；无命中 → `[]`。只读，不做风险适配判断。"""
    level = _choice(risk_level, "risk_level", RISK_LEVELS, allow_none=True)
    if level is None:
        return _many("SELECT * FROM wealth_product ORDER BY min_amount, id")
    return _many("SELECT * FROM wealth_product WHERE risk_level = ? ORDER BY min_amount, id", (level,))


def get_product(product_id: str) -> dict | None:
    """取单个理财产品；不存在 → `None`。"""
    return _one("SELECT * FROM wealth_product WHERE id = ?", (_text(product_id, "product_id"),))


def insert_txn(account_id: str, ts: str, amount: int, direction: str, balance_after: int, *,
               counterparty: str | None = None, category: str | None = None,
               channel: str | None = None, memo: str | None = None, id: str | None = None) -> dict:
    """写一条流水（整数分；`in` 必须为正、`out` 必须为负），返回写入后整行。

    `balance_after` 由**调用方**算好（DAO 不记账、不动 account 余额）；`memo` 是不可信文本，
    原样入库、包裹在编排层（铁律 7）；`id` 缺省自动生成，传了即作为幂等键。
    """
    cents = _cents(amount, "amount")
    if cents == 0:
        raise ValueError("amount 不能为 0（DDL：正=入账 负=出账）")
    way = _choice(direction, "direction", DIRECTIONS)
    if (way == "in") != (cents > 0):
        raise ValueError(f"direction={way!r} 与 amount={cents} 符号不一致")
    values = (str(_text(id, "id")) if id is not None else f"txn_{uuid.uuid4().hex[:12]}",
              _text(account_id, "account_id"), _stamp(ts), cents, way,
              _text(counterparty, "counterparty", allow_none=True),
              _text(category, "category", allow_none=True),
              _text(channel, "channel", allow_none=True),
              _text(memo, "memo", allow_none=True), _cents(balance_after, "balance_after"))
    return _insert("txn", _TXN_COLUMNS, values, str(values[0]), skip_ts=ts is None)


def insert_audit(trace_id: str, session_id: str, *, ts: str | None = None, actor: str = "agent",
                 intent: str | None = None, tool: str | None = None,
                 params_json: str | dict | None = None, risk_level: str | None = None,
                 permission_tier: str | None = None, result: str | None = None,
                 error_code: str | None = None, id: str | None = None) -> dict:
    """写一条审计（每请求必写、带 `trace_id`），返回写入后整行；`params_json` 必须**已脱敏**。"""
    values = (str(_text(id, "id")) if id is not None else f"audit_{uuid.uuid4().hex[:12]}",
              _text(trace_id, "trace_id"), _text(session_id, "session_id"), _stamp(ts),
              _choice(actor, "actor", ACTORS), _text(intent, "intent", allow_none=True),
              _text(tool, "tool", allow_none=True), _json_text(params_json, "params_json"),
              _text(risk_level, "risk_level", allow_none=True),
              _choice(permission_tier, "permission_tier", TIERS, allow_none=True),
              _choice(result, "result", AUDIT_RESULTS, allow_none=True),
              _text(error_code, "error_code", allow_none=True))
    return _insert("audit_log", _AUDIT_COLUMNS, values, str(values[0]), skip_ts=ts is None)


def insert_risk_event(user_id: str, factor: str, *, trace_id: str | None = None, ts: str | None = None,
                      detail: str | None = None, action_taken: str | None = None,
                      id: str | None = None) -> dict:
    """写一条风控事件：**只记账不决策**（降档/拦截由 `guard/` 定），返回写入后整行；`factor` 取自规格第 5 节因子，`action_taken` 取 `downgrade|block|to_human` 或 None。"""
    values = (str(_text(id, "id")) if id is not None else f"risk_{uuid.uuid4().hex[:12]}",
              _text(trace_id, "trace_id", allow_none=True), _stamp(ts), _text(user_id, "user_id"),
              _choice(factor, "factor", RISK_FACTORS),
              _text(detail, "detail", allow_none=True),
              _choice(action_taken, "action_taken", RISK_ACTIONS, allow_none=True))
    return _insert("risk_event", _RISK_COLUMNS, values, str(values[0]), skip_ts=ts is None)


# ---------------- 卡 14b：幂等落库 + 写操作限流（滚动窗口） ----------------

def get_idempotent(token: str) -> dict | None:
    """按 token 取幂等快照；没有则 None。**落库**而非内存 → 进程重启后依旧有效。"""
    return _one("SELECT token, tool, user_id, result_json, created_at FROM idempotency"
                " WHERE token = ?", (str(token),))


def insert_idempotent(token: str, tool: str, user_id: str, result_json: str, created_at: str) -> None:
    """写幂等快照（`INSERT OR IGNORE`：同 token 已存在即忽略，由调用方先查后写保证一致性）。"""
    with _writing() as conn:
        conn.execute("INSERT OR IGNORE INTO idempotency"
                     " (token, tool, user_id, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
                     (str(token), str(tool), str(user_id), _json_text(result_json, "result_json"),
                      _stamp(created_at, "created_at")))


def update_idempotent(token: str, result_json: str, expect_result_json: str) -> int:
    """条件更新幂等快照：仅当当前 `result_json` 仍等于 `expect_result_json` 时才覆盖为新值。

    「DB 级守卫」（卡 14b-6）：多进程同时执行同一 token 时，只有先提交的 UPDATE 命中，
    后者受影响行 0 → 调用方走幂等返回赢家结果。与 `insert_idempotent`（首次登记）互补，
    本函数只做「preview → executed」的条件翻转，不做无条件的覆盖。
    """
    with _writing() as conn:
        cursor = conn.execute(
            "UPDATE idempotency SET result_json = ? WHERE token = ? AND result_json = ?",
            (_json_text(result_json, "result_json"), str(token),
             _json_text(expect_result_json, "expect_result_json")))
    return cursor.rowcount


def incr_rate_limit(user_id: str, tool: str, ts: str) -> None:
    """记一次**写操作尝试**（含被拒的越权/非法参数尝试——先计数、再校验）。"""
    with _writing() as conn:
        conn.execute("INSERT INTO rate_limit (user_id, tool, ts) VALUES (?, ?, ?)",
                     (str(user_id), str(tool), _stamp(ts, "ts")))


def count_rate_limit(user_id: str, since_iso: str) -> int:
    """滚动窗口内该用户的写操作尝试次数（`since_iso` 之后的行都算，含边界）。"""
    row = _one("SELECT COUNT(*) AS n FROM rate_limit WHERE user_id = ? AND ts >= ?",
               (str(user_id), _stamp(since_iso, "since_iso")))
    return int(row["n"]) if row is not None else 0


def update_account_balance(account_id: str, delta: int) -> dict | None:
    """按**增量**改账户余额（整数分，负=扣款）：`balance` 与 `available` 同步加减，返回更新后整行。

    不做业务判断（够不够扣、限额、状态机都是调用方/`guard/` 的事）；账户不存在 → `None`。
    与 `insert_txn` 一样只提供原语：**余额变动与流水写入的原子性由调用方的事务保证**。
    """
    change = _cents(delta, "delta")
    account = str(_text(account_id, "account_id"))
    with _writing() as conn:
        conn.execute("UPDATE account SET balance = balance + ?, available = available + ? WHERE id = ?",
                     (change, change, account))
    return _one("SELECT * FROM account WHERE id = ?", (account,))
