"""数据层：建库、连接与事务（任务卡 01）。

约束来源：`CLAUDE.md` 铁律 + `docs/01-接口规格.md` 第 1 节。

- 只用 stdlib `sqlite3`，禁止 ORM（不用 SQLAlchemy）。
- 表结构定义在 `data/schema.sql`，它是规格第 1 节 DDL 的逐字副本，不得改动字段。
- 金额一律是「分」的整数（INTEGER），禁止浮点。
- 每次连接都开启外键约束；所有写入必须包在 `transaction()` 里。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

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
)


def schema_sql(schema_path: Path | str = SCHEMA_PATH) -> str:
    """读回 DDL 文本（data/schema.sql）。"""
    return Path(schema_path).read_text(encoding="utf-8")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接：开启外键约束，返回 Row 行工厂。

    连接处于**自动提交**模式（`isolation_level=None`），事务由 `transaction()` 显式控制，
    这样 `PRAGMA foreign_keys` 之类的设置不会被一个意外开启的事务吞掉。
    调用方负责 `close()`。
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """显式事务：正常结束 COMMIT，抛异常时 ROLLBACK 后原样抛出。

    不可嵌套（SQLite 不支持 `BEGIN` 套 `BEGIN`）；需要组合时由最外层调用方开启。
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """建库建表（幂等）：表已存在则跳过 DDL。返回**打开**的连接，调用方负责 close()。"""
    path = Path(db_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        missing = [name for name in TABLES if not _table_exists(conn, name)]
        if not missing:
            logger.debug("数据库已建好，跳过 DDL：%s", path)
            return conn
        with transaction(conn):
            for statement in statements(schema_sql()):
                conn.execute(statement)
    except BaseException:
        conn.close()
        raise
    logger.info("已初始化数据库 %s（新建 %d 张表）", path, len(missing))
    return conn


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
