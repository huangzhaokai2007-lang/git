"""数据层 DAO（任务卡 03）：SQLite 读写原语 —— 一次只做一件事，**不做业务/权限/风控判断**。

依据 `docs/01-接口规格.md` 第 1 节 DDL + `CLAUDE.md` 铁律；架构第 ⑤ 层，只允许 `tools/` 调用。
本模块的全局约定（各函数不再重复）：
- 不判权限档、限额、风控因子、收款人歧义（都是 `guard/` 的活）：只回答"库里有什么行"和"写入这一行"。
- 金额一律整数分（`int`）；float / bool / 字符串 → `ValueError`。读返回纯 dict（无 → `None` / `[]`）。
- 写操作按主键**幂等**：同 id 同内容 → 返回既有行且不产生第二行；同 id 异内容 → `ValueError`；
  写操作**不代写** `audit_log` —— 审计由编排层显式调 `insert_audit`。
- 枚举值取自 DDL 注释（savings|credit、normal|locked|lost|frozen、active|cancelled|paused、
  monthly|yearly、R1..R5、in|out、night|geo|device|velocity|amount_jump|new_payee、
  downgrade|block|to_human、L0..L3），越界 → `ValueError`。
- 连接进程内共享：`connect_db(path)` 指定库文件（默认环境变量 `DB_PATH`，与 `data/seed.py` 同口径）；
  写语句若已在**外层事务**中则加入，不嵌套 `BEGIN`（`data/db.py` 的 `transaction()` 不可嵌套）。
  另：为守「单文件 ≤300 行」，顶层函数之间用 1 行空行（PEP8 常规为 2 行）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from data.db import init_db, transaction

logger = logging.getLogger(__name__)
DEFAULT_DB_PATH = "data/bank.db"                       # 环境变量 DB_PATH 优先
MAX_LIMIT = 500                                        # 分页上限，防一次拉全表进上下文
ACCOUNT_TYPES, CARD_STATUSES = ("savings", "credit"), ("normal", "locked", "lost", "frozen")
SUBSCRIPTION_STATUSES, SUBSCRIPTION_CYCLES = ("active", "cancelled", "paused"), ("monthly", "yearly")
RISK_LEVELS, DIRECTIONS = ("R1", "R2", "R3", "R4", "R5"), ("in", "out")
ACTORS, TIERS = ("user", "agent", "system"), ("L0", "L1", "L2", "L3")
AUDIT_RESULTS = ("success", "rejected", "pending_confirm", "error")
RISK_FACTORS = ("night", "geo", "device", "velocity", "amount_jump", "new_payee")
RISK_ACTIONS = ("downgrade", "block", "to_human")
CARD_FIELDS = ("type", "credit_limit", "single_limit", "daily_limit", "status")
SUBSCRIPTION_FIELDS = ("merchant", "amount", "cycle", "next_charge_date", "source_txn_id", "status")
_UPDATABLE = {"card": CARD_FIELDS, "subscription": SUBSCRIPTION_FIELDS}   # update_* 字段白名单
_TXN_COLUMNS = ("id", "account_id", "ts", "amount", "direction", "counterparty", "category",
                "channel", "memo", "balance_after")
_AUDIT_COLUMNS = ("id", "trace_id", "session_id", "ts", "actor", "intent", "tool", "params_json",
                  "risk_level", "permission_tier", "result", "error_code")
_RISK_COLUMNS = ("id", "trace_id", "ts", "user_id", "factor", "detail", "action_taken")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")
_connection: sqlite3.Connection | None = None
_connection_path: Path | None = None


def connect_db(db_path: str | Path | None = None) -> Path:
    """指定本进程 DAO 使用的库文件（先关掉旧连接）；缺省取环境变量 `DB_PATH`。返回生效路径。"""
    global _connection_path
    close()
    _connection_path = Path(db_path or os.environ.get("DB_PATH") or DEFAULT_DB_PATH)
    logger.debug("DAO 连接到 %s", _connection_path)
    return _connection_path


def close() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


def connection() -> sqlite3.Connection:
    """共享连接（自动提交 + 外键开启，建表幂等）；工具层组合多步事务时用它。"""
    global _connection
    if _connection is None:
        _connection = init_db(_connection_path or Path(os.environ.get("DB_PATH") or DEFAULT_DB_PATH))
    return _connection


def _one(sql: str, params: tuple = ()) -> dict | None:
    row = connection().execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _many(sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection().execute(sql, params)]


@contextmanager
def _writing() -> Iterator[sqlite3.Connection]:
    """写语句包装：已在外层事务中则加入，否则自己开一个（绝不嵌套 BEGIN）。"""
    conn = connection()
    if conn.in_transaction:
        yield conn
        return
    with transaction(conn):
        yield conn


def _text(value: object, name: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串，收到 {value!r}")
    return value.strip()


def _cents(value: object, name: str, *, allow_none: bool = False) -> int | None:
    """金额必须是整数分（bool 是 int 子类，单独挡掉；float 一律拒）。"""
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数分（int），收到 {value!r}")
    return value


def _choice(value: object, name: str, allowed: tuple[str, ...], *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if value not in allowed:
        raise ValueError(f"{name} 只能是 {allowed} 之一，收到 {value!r}")
    return str(value)


def _iso_date(value: object, name: str) -> str:
    text = str(_text(value, name))
    if not _DATE_RE.match(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD，收到 {value!r}")
    date.fromisoformat(text)
    return text


def _stamp(ts: object, name: str = "ts") -> str:
    if ts is None:
        return datetime.now().isoformat(timespec="seconds")
    text = str(_text(ts, name))
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 ISO8601 时间戳，收到 {ts!r}") from exc
    return text


def _period_range(period: object) -> tuple[str, str]:
    """`"2026-03"` / `"2026"` → 左闭右开的 ISO 区间（供 ts 字符串比较）。"""
    match = _PERIOD_RE.match(str(_text(period, "period")))
    if match is None:
        raise ValueError(f"period 必须是 YYYY-MM 或 YYYY，收到 {period!r}")
    year, month = int(match.group(1)), match.group(2)
    if month is None:                                  # 整年 → 自然年区间
        return f"{year:04d}-01-01", f"{year + 1:04d}-01-01"
    number = int(month)
    if not 1 <= number <= 12:
        raise ValueError(f"period 的月份非法：{period!r}")
    return date(year, number, 1).isoformat(), date(year + number // 12, number % 12 + 1, 1).isoformat()


def _json_text(value: object, name: str) -> str | None:
    """dict → JSON 字符串；str 原样存但必须是合法 JSON（脱敏是调用方的责任）。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(_text(value, name))
    try:
        json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{name} 不是合法 JSON，收到 {value!r}") from exc
    return text


def _insert(table: str, columns: tuple[str, ...], values: tuple, row_id: str,
            skip_ts: bool = False) -> dict:
    # 按主键幂等：同 id 同内容 → 既有行；同 id 异内容 → ValueError
    # `skip_ts`：仅当 ts 是 DAO 自己生成的（调用方没给）才为 True —— 该 ts 不参与比对，
    # 否则同一业务内容的重试只要跨过一个秒边界就会被误判成"内容不同"，幂等键形同虚设。
    # 刻意收窄成布尔、而不是接受任意列名集合：除了这个自生成的时间戳，**所有**业务字段
    # （尤其 amount）永远参与比对，杜绝"误把金额放进豁免集 → 同 id 异金额被静默吞掉"的幂等失效。
    with _writing() as conn:
        existing = _one(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,))
        if existing is not None:
            if any(existing[name] != value for name, value in zip(columns, values)
                   if not (skip_ts and name == "ts")):
                raise ValueError(f"{table}.id={row_id} 已存在且内容不同（幂等键冲突）")
            return existing
        holes = ", ".join("?" * len(columns))
        conn.execute(f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({holes})', values)
    written = _one(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,))
    if written is None:                                # 防御性兜底，正常走不到
        raise RuntimeError(f"{table}.id={row_id} 写入后读不回")
    return written


def _apply_update(table: str, row_id: str, fields: dict) -> dict | None:
    # 白名单校验 + 更新；库里没有这一行 → None（不新建、不判断业务状态）
    unknown = set(fields) - set(_UPDATABLE[table])
    if unknown:
        raise ValueError(f"{table} 不允许改字段 {sorted(unknown)}，允许：{_UPDATABLE[table]}")
    if not fields:
        raise ValueError(f"update_{table} 至少要给一个字段")
    names = sorted(fields)
    with _writing() as conn:
        if _one(f'SELECT id FROM "{table}" WHERE id = ?', (row_id,)) is None:
            return None
        conn.execute(f'UPDATE "{table}" SET {", ".join(f"{n} = ?" for n in names)} WHERE id = ?',
                     (*[fields[n] for n in names], row_id))
    return _one(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,))


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
