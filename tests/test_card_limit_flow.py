"""F06 行为验收：真实 SQLite，预览、验证、审批、到期和隔离。"""
from datetime import datetime, timedelta

import pytest

from data import dao
from tools import card_binding, query, transfer
from tests.conftest import raw


def service():
    from agent import card_limit_flow
    return card_limit_flow


def bind(number):
    preview = card_binding.preview_binding(number, "000000", session_id="f06")
    assert preview.ok
    result = card_binding.confirm_binding(preview.data["token"], session_id="f06")
    assert result.ok
    return result.data["card_id"]


@pytest.mark.parametrize("digit,status", [("1", "pending"), ("2", "approved"), ("3", "rejected")])
def test_three_outcomes_persistent_and_idempotent(seeded, digit, status):
    flow = service()
    card = bind(digit * 16)
    before = raw(seeded, "SELECT * FROM card WHERE id=?", (card,))[0]
    preview = flow.preview(card, "fixed", "30000", None, "f06")
    assert preview["ok"]
    assert raw(seeded, "SELECT * FROM card WHERE id=?", (card,))[0] == before
    token = preview["data"]["token"]
    result = flow.submit(token, "123456", "f06")
    assert result["ok"] and result["data"]["status"] == status
    assert flow.submit(token, "123456", "f06")["data"] == result["data"]
    assert len(flow.records()["data"]["items"]) == 1
    after = raw(seeded, "SELECT * FROM card WHERE id=?", (card,))[0]
    assert after["credit_limit"] == (3000000 if status == "approved" else 2000000)
    account = raw(seeded, "SELECT * FROM account WHERE id=?", (before["account_id"],))[0]
    assert account["balance"] == 0
    assert account["available"] == after["credit_limit"]
    dao.close()
    dao.connect_db(seeded)
    assert flow.records()["data"]["items"][0]["status"] == status
    audits = raw(seeded, "SELECT * FROM audit_log WHERE intent='card_limit_adjust'")
    assert audits and "123456" not in str(audits)


def test_wrong_otp_owner_session_expiry_and_stale_preview(seeded, monkeypatch):
    flow = service()
    from tools import card_limit
    card = bind("2" * 16)
    token = flow.preview(card, "fixed", "30000", None, "f06")["data"]["token"]
    assert not flow.submit(token, "000000", "f06")["ok"]
    assert not flow.submit(token, "123456", "other")["ok"]
    query.set_current_user("other")
    assert not flow.submit(token, "123456", "f06")["ok"]
    assert flow.records()["data"]["items"] == []
    query.set_current_user(None)
    original_now = card_limit._now()
    monkeypatch.setattr(card_limit, "_now", lambda: original_now + timedelta(seconds=301))
    assert not flow.submit(token, "123456", "f06")["ok"]
    monkeypatch.setattr(card_limit, "_now", lambda: original_now)
    dao.update_card(card, credit_limit=2100000)
    assert not flow.submit(token, "123456", "f06")["ok"]
    assert flow.records()["data"]["items"] == []


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "1e6", "1.001", "99999999999999999999"])
def test_invalid_amount_never_creates_application(seeded, amount):
    flow = service()
    card = bind("2" * 16)
    assert not flow.preview(card, "fixed", amount, None, "f06")["ok"]
    assert flow.records()["data"]["items"] == []


def test_temporary_expiry_restores_fixed_and_available(seeded, monkeypatch):
    flow = service()
    from tools import card_limit
    now = datetime(2026, 10, 10, 12)
    monkeypatch.setattr(card_limit, "_now", lambda: now)
    card = bind("2" * 16)
    token = flow.preview(card, "temporary", "30000", 7, "f06")["data"]["token"]
    assert flow.submit(token, "123456", "f06")["ok"]
    row = raw(seeded, "SELECT * FROM card WHERE id=?", (card,))[0]
    assert row["credit_limit"] == 2000000
    assert flow.cards("temporary")["data"]["items"][0]["effective_limit_yuan"] == "30,000.00"
    assert raw(seeded, "SELECT available FROM account WHERE id=?", (row["account_id"],))[0]["available"] == 3000000
    monkeypatch.setattr(card_limit, "_now", lambda: now + timedelta(days=8))
    assert flow.records()["data"]["items"][0]["expired"]
    assert raw(seeded, "SELECT available FROM account WHERE id=?", (row["account_id"],))[0]["available"] == 2000000
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE tool='expire_card_limit'")) == 1
    flow.records()
    assert len(raw(seeded, "SELECT * FROM audit_log WHERE tool='expire_card_limit'")) == 1


def test_savings_fixed_disallowed_and_daily_enforced_on_transfer(seeded):
    flow = service()
    p = card_binding.preview_binding("6222000000000001", "314159", session_id="f06")
    card_binding.confirm_binding(p.data["token"], session_id="f06")
    card = p.data["card_id"]
    assert not flow.preview(card, "fixed", "30000", None, "f06")["ok"]
    transfer_preview = transfer.preview_transfer("payee_0001", 20000)
    assert transfer_preview.ok
    token = flow.preview(card, "daily", "100", None, "f06")["data"]["token"]
    assert flow.submit(token, "123456", "f06")["ok"]
    assert not transfer.preview_transfer("payee_0001", 20000).ok
    assert not transfer.execute_transfer(transfer_preview.data["preview_token"], "123456").ok


def test_locked_unbound_and_temporary_period(seeded):
    flow = service()
    flow.cards("fixed")
    assert not flow.preview("card_limit_approved", "fixed", "30000", None, "f06")["ok"]
    card = bind("2" * 16)
    for days in (None, 0, 91, True):
        assert not flow.preview(card, "temporary", "30000", days, "f06")["ok"]
    dao.update_card(card, status="locked")
    assert not flow.preview(card, "fixed", "30000", None, "f06")["ok"]


@pytest.mark.parametrize("digit,status", [("1", "pending"), ("3", "rejected")])
def test_temporary_pending_and_rejected_do_not_change_available(seeded, digit, status):
    flow = service()
    card = bind(digit * 16)
    p = flow.preview(card, "temporary", "30000", 30, "f06")
    r = flow.submit(p["data"]["token"], "123456", "f06")
    assert r["data"]["status"] == status
    account = raw(seeded, "SELECT * FROM account WHERE id=(SELECT account_id FROM card WHERE id=?)", (card,))[0]
    assert account["available"] == 2000000 and account["balance"] == 0
    assert raw(seeded, "SELECT * FROM agent_card_limit_temporary") == []


def test_fixed_change_with_active_temporary_and_occupied_credit(seeded):
    flow = service()
    card = bind("2" * 16)
    p = flow.preview(card, "temporary", "30000", 30, "f06")
    flow.submit(p["data"]["token"], "123456", "f06")
    p = flow.preview(card, "fixed", "25000", None, "f06")
    assert flow.submit(p["data"]["token"], "123456", "f06")["ok"]
    account_id = raw(seeded, "SELECT account_id FROM card WHERE id=?", (card,))[0]["account_id"]
    assert raw(seeded, "SELECT available FROM account WHERE id=?", (account_id,))[0]["available"] == 3000000
    from data.db import connect, transaction
    conn = connect(seeded)
    with transaction(conn):
        conn.execute("UPDATE account SET balance=-2600000,available=400000 WHERE id=?", (account_id,))
    conn.close()
    assert not flow.preview(card, "fixed", "25000", None, "f06")["ok"]


def test_audit_failure_rolls_back_limit_and_application(seeded, monkeypatch):
    import sqlite3
    flow = service()
    card = bind("2" * 16)
    p = flow.preview(card, "fixed", "30000", None, "f06")
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("audit unavailable")
    monkeypatch.setattr(dao, "insert_audit", fail)
    assert not flow.submit(p["data"]["token"], "123456", "f06")["ok"]
    assert raw(seeded, "SELECT * FROM agent_card_limit_application") == []
    assert raw(seeded, "SELECT credit_limit FROM card WHERE id=?", (card,))[0]["credit_limit"] == 2000000


def test_concurrent_submission_and_otp_lockout(seeded, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tools import card_limit
    flow = service()
    card = bind("2" * 16)
    p = flow.preview(card, "fixed", "30000", None, "f06")
    token = p["data"]["token"]
    for _ in range(5):
        assert not flow.submit(token, "999999", "f06")["ok"]
    assert not flow.submit(token, "123456", "f06")["ok"]
    next_time = card_limit._now() + timedelta(seconds=301)
    monkeypatch.setattr(card_limit, "_now", lambda: next_time)
    p = flow.preview(card, "fixed", "30000", None, "f06")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: flow.submit(p["data"]["token"], "123456", "f06"), range(2)))
    assert all(r["ok"] for r in results)
    assert results[0]["data"] == results[1]["data"]
    assert len(flow.records()["data"]["items"]) == 1


@pytest.mark.parametrize("text", ["不要调整额度", "我不想提高额度", "取消额度调整", "我想申请信用卡并提高额度"])
def test_negative_and_compound_requests_do_not_open_limit_form(text):
    assert service().recognize(text) is None


def test_demo_accounts_do_not_replace_primary_credit_balance(seeded):
    before = query.get_balance("credit").data
    service().records()
    after = query.get_balance("credit").data
    assert after["balance"] == before["balance"]
    assert after["available"] == before["available"]


def test_normal_balance_query_and_card_detail_refresh_expiry(seeded, monkeypatch):
    from tools import card_limit
    flow = service()
    now = datetime(2026, 10, 10, 12)
    monkeypatch.setattr(card_limit, "_now", lambda: now)
    preview = card_binding.preview_binding("6222000000000002", "271828", session_id="f06")
    card_binding.confirm_binding(preview.data["token"], session_id="f06")
    card = preview.data["card_id"]
    before = query.get_balance("credit").data["available"]
    p = flow.preview(card, "temporary", "40000", 1, "f06")
    assert flow.submit(p["data"]["token"], "123456", "f06")["ok"]
    assert query.get_balance("credit").data["available"] == before + 1000000
    monkeypatch.setattr(card_limit, "_now", lambda: now + timedelta(days=2))
    assert query.get_balance("credit").data["available"] == before
    monkeypatch.setattr(card_limit, "_now", lambda: now + timedelta(days=3))
    p = flow.preview(card, "temporary", "40000", 1, "f06")
    flow.submit(p["data"]["token"], "123456", "f06")
    monkeypatch.setattr(card_limit, "_now", lambda: now + timedelta(days=5))
    assert card_binding.card_detail(card).ok
    assert raw(seeded, "SELECT available FROM account WHERE id='acc_credit_0001'")[0]["available"] == before
