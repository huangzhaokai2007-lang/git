"""服务后台结算，不依赖浏览器打开申请记录。"""
from datetime import timedelta
from time import monotonic, sleep

from data import dao
from tests.test_card_lost_flow import prepared, submit


def test_worker_settles_without_records_page(seeded, monkeypatch):
    from tools import card_lost
    from tools import _card_lost_runner as runner
    flow, card_id, preview = prepared()
    submit(flow, preview)
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    worker = runner.start(interval=0.02)
    try:
        deadline = monotonic() + 2
        while monotonic() < deadline and dao.get_card(card_id)['status'] != 'lost':
            sleep(0.02)
        assert dao.get_card(card_id)['status'] == 'lost'
        assert runner.start(interval=0.02) is worker
    finally:
        worker.stop.set()
        worker.thread.join(timeout=2)
