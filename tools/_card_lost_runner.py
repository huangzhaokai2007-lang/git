"""本地服务挂失到期结算；连接在线程内使用，数据库切换即停止。"""
from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from data import dao
from tools import card_lost

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_WORKERS: dict[Path, 'Worker'] = {}


@dataclass
class Worker:
    stop: threading.Event
    thread: threading.Thread


def _run(path: Path, stop: threading.Event, interval: float) -> None:
    try:
        while not stop.wait(interval):
            if dao.db_path().resolve() != path:
                break
            try:
                result = card_lost.settle()
                if not result.ok:
                    logger.warning('挂失到期结算未成功，保留申请等待重试：%s', result.error_code)
            except Exception:
                logger.exception('挂失到期结算异常，保留申请等待重试')
    finally:
        dao.close()


def start(interval: float = 1.0) -> Worker:
    path = dao.db_path().resolve()
    with _LOCK:
        previous = _WORKERS.get(path)
        if previous and previous.thread.is_alive() and not previous.stop.is_set():
            return previous
        stop = threading.Event()
        thread = threading.Thread(target=_run, args=(path, stop, interval),
                                  name='card-lost-settlement', daemon=True)
        worker = Worker(stop, thread)
        _WORKERS[path] = worker
        thread.start()
        return worker
