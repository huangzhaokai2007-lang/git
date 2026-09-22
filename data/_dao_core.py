"""数据层 DAO 的**内核**（任务卡 05b 从 `data/dao.py` 拆出）：连接状态 + 写原语 + 入参校验 helper。

依赖方向**单向**：`data/dao.py` → 本模块；本模块只 import 标准库 + `data/db.py`，**绝不反向** import
`data.dao`（否则形成回边，卡 05b 明文禁止）。

⚠ 为什么连接状态（线程局部 `_local` + `_connection_path`）必须跟 `connect_db/close/connection/_one/_many/_writing`
以及写原语 `_insert/_apply_update` **一起**待在这里：把它们拆开就会出现**两份连接状态**，
`test_writes_join_an_outer_transaction_and_roll_back_together`（外层事务内不重复 BEGIN）会当场变红
—— 卡 05b 验收门 ① 钉的就是这条边界。

卡 16b：连接从"进程级单例"改成**线程局部**（`threading.local`）。`sqlite3` 的连接有线程亲和，
而调用方天然多线程（Streamlit 每次 rerun 一个线程、FastAPI 每个请求一个线程）—— 单例连接在第二个
线程上就抛 `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`。
线程各持一份连接后：事务互相独立、谁也不串谁；同一文件的并发写由 SQLite 写锁 +
`data.db.connect` 的 `busy_timeout` 串行化。线程结束时其连接随线程局部变量被回收并关闭。

本模块不做任何业务判断：金额一律整数分、枚举越界 → `ValueError`、写按主键幂等、不代写审计。

卡 23：**读原语** `list_cards` 也住这里（不是 `data/dao.py`）—— `data/dao.py` 已顶格 300 行，且它
docstring 里的「22 个公开读写函数」是文档化不变量，不该被一张新卡扰动；调用方是 `tools/card_query.py`。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from data.db import init_db, transaction

logger = logging.getLogger(__name__)

# ---------------- 常量 ----------------

#: 库文件缺省位置（环境变量 DB_PATH 优先）。**故意放在 `var/` 而不是 `data/`**：`data/` 是代码目录
#: （dao.py / db.py / schema.sql / seed.py），库文件混在里面会让「挂卷」把代码盖住 —— 卡 20-C 真机事故：
#: 卷挂在 /app/data 上，容器里跑的是第一次建卷时的旧 dao.py，`POST /api/payee` 必 500。卷只能挂数据目录。
#: 父目录由 data/db.py 的 init_db/reset_db 自动创建（`mkdir(parents=True, exist_ok=True)`）。
DEFAULT_DB_PATH = "var/bank.db"                       # 环境变量 DB_PATH 优先；库文件住**数据目录** var/，与代码目录 data/ 分开
MAX_LIMIT = 500                                        # 分页上限，防一次拉全表进上下文
ACCOUNT_TYPES, CARD_STATUSES = ("savings", "credit"), ("normal", "locked", "lost", "frozen")
SUBSCRIPTION_STATUSES, SUBSCRIPTION_CYCLES = ("active", "cancelled", "paused"), ("monthly", "yearly")
RISK_LEVELS, DIRECTIONS = ("R1", "R2", "R3", "R4", "R5"), ("in", "out")
ACTORS, TIERS = ("user", "agent", "system"), ("L0", "L1", "L2", "L3")
AUDIT_RESULTS = ("success", "rejected", "pending_confirm", "error")
RISK_FACTORS = ("night", "geo", "device", "velocity", "amount_jump", "new_payee",
                "unauthorized_resource")            # 卡 14b：越权尝试的安全留痕因子
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
#: 线程局部的连接（`conn` / `path`）：**每个线程一份**，见模块头「卡 16b」。
_local = threading.local()
#: 当前生效的库文件路径（`connect_db` 设置；未设置时取环境变量 / 默认路径）。
_connection_path: Path | None = None




def connect_db(db_path: str | Path | None = None) -> Path:
    """指定本进程 DAO 使用的库文件（先关掉**本线程**的旧连接）；缺省取环境变量 `DB_PATH`。返回生效路径。

    其它线程的连接在下一次 `connection()` 时按新路径重开（线程各自持有路径，不需要全局登记表）。
    """
    global _connection_path
    close()
    _connection_path = Path(db_path or os.environ.get("DB_PATH") or DEFAULT_DB_PATH)
    logger.debug("DAO 连接到 %s", _connection_path)
    return _connection_path


def db_path() -> Path:
    """当前生效的库文件（未 `connect_db` 时取环境变量 `DB_PATH`，再退到默认路径）。"""
    return _connection_path or Path(os.environ.get("DB_PATH") or DEFAULT_DB_PATH)


def close() -> None:
    """关掉**本线程**的连接（其余线程各持一份，各自按需重连）。"""
    conn = getattr(_local, "conn", None)
    _local.conn, _local.path = None, None
    if conn is not None:
        conn.close()


def connection() -> sqlite3.Connection:
    """**当前线程**的连接（自动提交 + 外键开启 + 建表幂等）；工具层组合多步事务时用它。

    为什么按线程（卡 16b）：`sqlite3` 的连接有线程亲和（默认 `check_same_thread=True`），而调用方
    天然多线程（Streamlit 每次 rerun 一个线程、FastAPI 每个请求一个线程）—— 进程级单例连接在第二个
    线程上就会抛 `ProgrammingError`。线程各持一份后：事务互相独立、`_writing()` 的"是否已在外层事务里"
    永远看到本线程的事务；跨线程写竞争由 SQLite 写锁 + `data.db.connect` 的 `busy_timeout` 串行化。
    库文件被 `connect_db` 换掉时，本线程的旧连接会被关掉重开。
    """
    path = db_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    if conn is not None:                                    # 换库了：丢掉本线程的旧连接
        conn.close()
    conn = init_db(path)
    _local.conn, _local.path = conn, path
    return conn


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


# ---------------- 卡 23：只读原语（读卡清单） ----------------


def list_cards(user_id: str, status: str | None = None) -> list[dict]:
    """按**用户**列卡（可按状态过滤），按 id 排序；无命中 → `[]`。

    归属由 `user_id` 作为**查询条件**显式圈定（不是事后过滤）→ 结构上不可能读到他人的卡。
    `status` 省略 = 不过滤；越界取值 → `ValueError`（取值域 = DDL 注释的 `CARD_STATUSES` 四值）。
    只回答「库里有哪些行」，不做任何业务判断（权限档、卡状态流转都是 `guard/` 与 `tools/` 的事）。
    """
    owner = _text(user_id, "user_id")
    if status is None:
        return _many('SELECT * FROM card WHERE user_id = ? ORDER BY id', (owner,))
    return _many('SELECT * FROM card WHERE user_id = ? AND status = ? ORDER BY id',
                 (owner, _choice(status, "status", CARD_STATUSES)))
