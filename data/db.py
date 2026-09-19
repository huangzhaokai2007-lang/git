"""数据层：建库、连接与事务（任务卡 01；卡 16b 修「旧库结构漂移」）。

约束来源：`CLAUDE.md` 铁律 + `docs/01-接口规格.md` 第 1 节。

- 只用 stdlib `sqlite3`，禁止 ORM（不用 SQLAlchemy）。
- 表结构定义在 `data/schema.sql`，它是规格第 1 节 DDL 的逐字副本，不得改动字段。
- 金额一律是「分」的整数（INTEGER），禁止浮点。
- 每次连接都开启外键约束；所有写入必须包在 `transaction()` 里。
- **连接不跨线程共享**（卡 16b）：`data._dao_core` 按线程各持一份，写竞争由 SQLite 写锁 +
  `BUSY_TIMEOUT_SECONDS` 兜住。本模块只负责"怎么开一个连接"，不持有全局状态。
- `init_db` 幂等**且可修缺表**：只补建缺失的表；建完自检列结构，缺列 → `SchemaDriftError`。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: 写事务等待上限（秒）：多线程各自持连接时，同库并发写由 SQLite 写锁串行化 —— 等不到就等
#: （默认 5s；等不到才 SQLITE_BUSY）。短事务 + 这个上限足够 demo/评测场景。
BUSY_TIMEOUT_SECONDS = 5.0

#: 从 DDL 语句里认表名（`CREATE TABLE xxx` / `CREATE TABLE IF NOT EXISTS xxx`）
_CREATE_TABLE = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                           re.IGNORECASE)

#: 规格第 1 节的表清单（顺序无关；用于建库自检与幂等判断）
TABLES: tuple[str, ...] = (
    "user",
    "account",
    "txn",
    "card",
    "subscription",
    "wealth_product",
    "holding",
    "payee",
    "audit_log",
    "risk_event",
    "idempotency",
    "rate_limit",
)


class SchemaDriftError(ValueError):
    """库文件结构与 `schema.sql` 不一致（旧库缺列等）——缺列无法安全补建，只能重建。

    继承 `ValueError`：`data.seed` 的 CLI 把 ValueError 当"可预期的失败"报出来（不抛栈）。
    """


def schema_sql(schema_path: Path | str = SCHEMA_PATH) -> str:
    """读回 DDL 文本（data/schema.sql）。"""
    return Path(schema_path).read_text(encoding="utf-8")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接：外键约束开启、Row 行工厂、写竞争等待 `BUSY_TIMEOUT_SECONDS` 秒。

    连接处于**自动提交**模式（`isolation_level=None`），事务由 `transaction()` 显式控制，
    这样 `PRAGMA foreign_keys` 之类的设置不会被一个意外开启的事务吞掉。
    调用方负责 `close()`；**同一个连接不要跨线程用**（`data._dao_core` 按线程各开一份）。
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """显式事务：正常结束 COMMIT，抛异常时 ROLLBACK 后原样抛出。

    用 **`BEGIN IMMEDIATE`**（而不是默认的 `BEGIN DEFERRED`）：deferred 事务先读后写时，两个连接会
    各自持有 SHARED 锁、再同时要升级成写锁 —— 这是 SQLite 的经典**升级死锁**，`busy_timeout` 也救不了
    （等下去双方都拿不到锁），表现为随机的 `database is locked`（卡 16b 在四线程并发用例上实测 ~12% 复现）。
    IMMEDIATE 一进事务就拿 RESERVED 写锁：第二个写者只在 `busy_timeout` 内**排队**，读者照常读，
    不会互相卡死。多进程（uvicorn 多 worker）同理。

    不可嵌套（SQLite 不支持 `BEGIN` 套 `BEGIN`）；需要组合时由最外层调用方开启。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def created_table(statement: str) -> str | None:
    """语句若是 `CREATE TABLE xxx`，返回表名；其它语句（索引等）返回 `None`。"""
    match = _CREATE_TABLE.match(statement)
    return match.group(1) if match is not None else None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """表在库里的列名（表不存在 → 空列表）。"""
    return [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]


@lru_cache(maxsize=1)
def reference_columns() -> dict[str, tuple[str, ...]]:
    """`schema.sql` 应当长成的样子：在**内存库**里真跑一遍 DDL 再读回（不解析 SQL 文本）。

    用内存库做"真值来源"而不是正则解析 DDL：解析要考虑注释/约束/大小写，容易与 DDL 漂移；
    跑一遍是最省事且不会说谎的做法。DDL 是常量，结果缓存一份。
    """
    conn = connect(":memory:")
    try:
        for statement in statements(schema_sql()):
            conn.execute(statement)
        return {name: tuple(table_columns(conn, name)) for name in TABLES}
    finally:
        conn.close()


def schema_drift(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """实际库 vs DDL 的**缺列**报告（空 dict = 结构一致）。

    只报**已存在表**的缺列：缺表由 `init_db` 直接补建、不在此列；多出来的列（历史遗留）不影响
    DAO 的显式列表读写，只记不报。
    """
    problems: dict[str, list[str]] = {}
    for name, expected in reference_columns().items():
        actual = set(table_columns(conn, name))
        if not actual:                          # 表不存在 → 归 init_db 的补建职责，不算漂移
            continue
        if missing := [column for column in expected if column not in actual]:
            problems[name] = missing
    return problems


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """建库建表（幂等 + **可修缺表** + 结构自检）：返回**打开**的连接，调用方负责 close()。

    - 只补建**缺失的表**：卡 16b 之前是"只要有一张表缺，就对全量 DDL 无条件执行"，
      于是"部分过期的库"（例如卡 14b 之前建的库缺 idempotency / rate_limit）会撞
      `table user already exists` 直接崩。
    - 非 `CREATE TABLE` 的语句（索引等）始终执行 —— schema.sql 目前没有索引，将来若加请写成
      `CREATE INDEX IF NOT EXISTS`。
    - 建完做一次**结构漂移自检**：缺列 → `SchemaDriftError`（消息里带上重建命令）。
    """
    path = Path(db_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        missing = [name for name in TABLES if not _table_exists(conn, name)]
        if missing:
            with transaction(conn):
                for statement in statements(schema_sql()):
                    if (name := created_table(statement)) is None or name in missing:
                        conn.execute(statement)
            logger.info("已初始化数据库 %s（补建 %d 张表：%s）", path, len(missing), "、".join(missing))
        else:
            logger.debug("数据库已建好，跳过 DDL：%s", path)
        if problems := schema_drift(conn):
            raise SchemaDriftError(drift_hint(path, problems))
    except BaseException:
        conn.close()
        raise
    return conn


def drift_hint(path: Path, problems: dict[str, list[str]]) -> str:
    """结构漂移的可执行提示（缺列无法安全补建 → 直接重建合成库）。"""
    detail = "；".join(f"{table} 缺列 {columns}" for table, columns in sorted(problems.items()))
    return (f"{path} 的表结构与 schema.sql 不一致（{detail}）。"
            "库数据是合成的，直接重建：`uv run python -m data.seed --reset`")


def reset_db(db_path: str | Path) -> sqlite3.Connection:
    """清库重建：删掉所有表后按 schema.sql 重新建表，返回**打开**的连接，调用方负责 close()。

    表名取自 `sqlite_master`（非用户输入），因此用双引号包裹即可安全拼接。
    """
    path = Path(db_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        with transaction(conn):
            for name in _existing_tables(conn):
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            for statement in statements(schema_sql()):
                conn.execute(statement)
    except BaseException:
        conn.close()
        raise
    logger.info("已重置数据库 %s", path)
    return conn


def statements(sql: str) -> list[str]:
    """把 DDL 切分成完整语句（容忍注释里的分号），供放进显式事务里逐条执行。"""
    result: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            text = buffer.strip()
            if text:
                result.append(text)
            buffer = ""
    tail = buffer.strip()
    if tail:
        raise ValueError(f"schema.sql 结尾有未闭合的语句：{tail[:60]!r}")
    return result


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _existing_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]
