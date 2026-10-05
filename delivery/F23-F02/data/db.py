"""数据库连接工具。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """获取数据库连接。"""
    if db_path is None:
        from data import dao
        return dao._get_conn()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """事务上下文管理器。"""
    conn.execute("BEGIN")
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
