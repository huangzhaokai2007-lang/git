"""卡 16b 单测：数据层的**线程安全**（卡 16 现场实测的坑）。

四条：
① 同一线程多次取连接必须是同一份（否则 `_writing()` 的"已在外层事务里"永远为假、事务会嵌套）；
② **不同线程各持一份**连接，跨线程调用 DAO 不再抛
   `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`
   （Streamlit rerun / FastAPI 请求线程的等价场景）；
③ 多线程**真并发写**同一库：账不错、钱不串（每线程自己的事务互不干扰，写竞争由 SQLite 写锁 +
   `busy_timeout` 串行化）；
④ `close()` 只关本线程，关掉后自动重开（换库/测试复位口径不变）。

线程里的断言与异常都回传到主线程 —— 否则线程内失败会变成假绿。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

import pytest

from data import dao
from data.db import transaction
from data.seed import SAVINGS_ID

from tests.conftest import balance, count, raw

ACCOUNT = SAVINGS_ID


def _threads(targets: list[Callable[[], object]], *, timeout: float = 30.0) -> list[object]:
    """并发跑一批任务（**同时**启动后统一 join），返回值与异常都带回主线程。"""
    results: list[object] = [None] * len(targets)
    errors: list[BaseException] = []

    def wrapper(index: int, target: Callable[[], object]) -> None:
        try:
            results[index] = target()
        except BaseException as exc:                      # noqa: BLE001 —— 线程里的异常要回传
            errors.append(exc)

    workers = [threading.Thread(target=wrapper, args=(index, target), daemon=True)
               for index, target in enumerate(targets)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout)
    assert not any(worker.is_alive() for worker in workers), "线程超时未结束"
    if errors:
        raise errors[0]
    return results


def test_same_thread_reuses_one_connection(seeded: Path) -> None:
    """① 同线程同一份连接 —— 多步事务的前提（`transaction(dao.connection())` 里再写才算同一事务）。"""
    assert dao.connection() is dao.connection()
    with transaction(dao.connection()):
        dao.update_account_balance(ACCOUNT, -1_000)
        assert dao.connection().in_transaction                    # 写原语真的加入了这个事务


def test_each_thread_gets_its_own_connection(seeded: Path) -> None:
    """② 跨线程调用 DAO 不再抛 ProgrammingError（在**线程内**取连接、查询、断言）。"""
    main_connection = dao.connection()

    def in_thread() -> bool:
        conn = dao.connection()
        assert conn is not main_connection, "每个线程必须各持一份连接"
        assert conn.execute("PRAGMA database_list").fetchone()["file"].endswith("bank.db")
        assert conn.in_transaction is False
        row = dao.get_balance("savings")
        return row is not None and row["id"] == ACCOUNT

    assert _threads([in_thread])[0] is True


def test_parallel_writes_do_not_interleave_or_lose_money(seeded: Path) -> None:
    """③ 四线程各写 5 笔：流水条数与余额变动逐笔对得上（没互相踩、没丢更新）。"""
    workers, per_worker = 4, 5
    before_balance, before_txn = balance(seeded), count(seeded, "txn")
    started = threading.Barrier(workers)                          # 同时冲线，制造真并发窗口

    def worker(index: int) -> int:
        started.wait(timeout=20)
        for step in range(per_worker):
            row = dao.update_account_balance(ACCOUNT, -100)       # 本线程自己的连接与事务
            dao.insert_txn(ACCOUNT, None, -100, "out", row["balance"], id=f"txn-conc-{index}-{step}")
        return dao.connection().execute(
            "SELECT COUNT(*) AS n FROM txn WHERE id LIKE ?", (f"txn-conc-{index}-%",)).fetchone()["n"]

    counts = _threads([lambda index=index: worker(index) for index in range(workers)])
    per_thread_cents, rows_written = per_worker * 100, workers * per_worker
    assert balance(seeded) == before_balance - rows_written * 100
    assert count(seeded, "txn") == before_txn + rows_written
    assert counts == [per_worker] * workers, counts                # 每个线程都看得见自己写的每一笔
    assert raw(seeded, "SELECT COUNT(DISTINCT id) AS n FROM txn WHERE id LIKE 'txn-conc-%'")[0]["n"] == rows_written
    assert per_thread_cents == 500                                 # 口径自证：每线程 100 分 × 5 笔


def test_close_is_per_thread_and_reconnects(seeded: Path) -> None:
    """④ `close()` 只关本线程的连接；关掉后下一次取连接自动重开（换库/复位口径不变）。"""
    first = dao.connection()
    dao.close()
    assert dao.connection() is not first
    assert dao.db_path().name == "bank.db"
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")                                 # 旧连接真的被关掉了


def test_two_threads_reading_then_writing_do_not_deadlock(seeded: Path) -> None:
    """两条连接都"先读后写"时不得僵死 —— `transaction()` 一进事务就拿写锁（`BEGIN IMMEDIATE`）。

    时序用 `sleep` 固定（不靠 CPU 竞速）：A 进事务→读→按住 200ms；B 在 A 读过之后进事务→读→写。
    用默认的 deferred BEGIN，两边会各持 SHARED 再同时要升级成写锁 = SQLite 的经典**升级死锁**，
    `busy_timeout` 也救不了（卡 16b 实测四线程用例 ~12% 报 `database is locked`）；IMMEDIATE 下
    B 只会在写锁上排队等 A 提交。
    """

    def read_then_write(start_after: float, hold: float) -> None:
        time.sleep(start_after)
        with transaction(dao.connection()):                        # 一进事务就拿写锁（IMMEDIATE）
            dao.connection().execute("SELECT COUNT(*) AS n FROM txn").fetchone()   # 先读
            time.sleep(hold)                                                     # 让 B 也读到
            dao.update_account_balance(ACCOUNT, -100)                             # 再写

    _threads([lambda: read_then_write(0.0, 0.2), lambda: read_then_write(0.05, 0.0)])
    assert balance(seeded) >= 0

