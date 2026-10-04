"""真实合成库上的绑定、凭证和权限回归，不调用模型。"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from data import dao
from data.seed import USER_ID
from tests.conftest import FOREIGN_USER, raw
from tools._query_common import set_current_user

NUMBER, PIN, CARD = "6222000000000001", "314159", "card_savings_0001"
SESSION = "binding-test"


def api():
    from tools import card_binding
    return card_binding


def bind():
    preview = api().preview_binding(NUMBER, PIN, session_id=SESSION)
    assert preview.ok, preview.message
    return api().confirm_binding(preview.data["token"], session_id=SESSION)


def test_binding_is_available():
    from tools import card_binding
    assert callable(card_binding.preview_binding)


def test_new_demo_is_unbound_but_t18_is_unchanged(seeded):
    from tools.card_query import list_cards
    assert api().list_bound_cards().data == {"items": [], "total_count": 0}
    assert list_cards().data["total_count"] == 3


def test_preview_does_not_bind_and_confirmation_persists(seeded):
    preview = api().preview_binding("6222 0000 0000 0001", PIN, session_id=SESSION)
    assert preview.ok and preview.data["card_no_mask"] == "6222 **** **** 0001"
    assert api().list_bound_cards().data["total_count"] == 0
    result = api().confirm_binding(preview.data["token"], session_id=SESSION)
    assert result.ok and not result.data["already_bound"]
    dao.close()
    dao.connect_db(seeded)
    assert api().list_bound_cards().data["items"][0]["card_id"] == CARD


@pytest.mark.parametrize(("number", "password"), [
    (NUMBER, "999999"), ("6222000000009999", PIN),
    ("6222 **** **** 0001", PIN), (NUMBER, ""), (NUMBER, 314159),
])
def test_invalid_credentials_do_not_bind(seeded, number, password):
    result = api().preview_binding(number, password, session_id=SESSION)
    assert not result.ok
    assert api().list_bound_cards().data["total_count"] == 0
    assert NUMBER not in json.dumps(result.model_dump()) and PIN not in result.message


def test_foreign_user_and_wrong_account_owner_are_denied(foreign):
    set_current_user(FOREIGN_USER)
    assert not api().preview_binding(NUMBER, PIN, session_id=SESSION).ok
    set_current_user(None)
    dao.connection().execute("UPDATE account SET user_id=? WHERE id=(SELECT account_id FROM card WHERE id=?)",
                             (FOREIGN_USER, CARD))
    assert not api().preview_binding(NUMBER, PIN, session_id=SESSION).ok


def test_confirmation_is_scoped_to_session_and_user(foreign):
    token = api().preview_binding(NUMBER, PIN, session_id=SESSION).data["token"]
    assert not api().confirm_binding(token, session_id="other").ok
    set_current_user(FOREIGN_USER)
    assert not api().confirm_binding(token, session_id=SESSION).ok
    set_current_user(None)
    assert api().list_bound_cards().data["total_count"] == 0


def test_confirmation_rechecks_ownership(foreign):
    token = api().preview_binding(NUMBER, PIN, session_id=SESSION).data["token"]
    dao.connection().execute("UPDATE card SET user_id=? WHERE id=?", (FOREIGN_USER, CARD))
    assert not api().confirm_binding(token, session_id=SESSION).ok


def test_expired_confirmation_does_not_bind(seeded, monkeypatch):
    module = api()
    monkeypatch.setattr(module, "_now", lambda: 1000.0)
    token = module.preview_binding(NUMBER, PIN, session_id=SESSION).data["token"]
    monkeypatch.setattr(module, "_now", lambda: 1301.0)
    assert not module.confirm_binding(token, session_id=SESSION).ok
    assert module.list_bound_cards().data["total_count"] == 0


def test_repeated_and_concurrent_confirmations_are_idempotent(seeded):
    token = api().preview_binding(NUMBER, PIN, session_id=SESSION).data["token"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: api().confirm_binding(token, session_id=SESSION), range(4)))
    assert all(result.ok for result in results)
    assert sum(not result.data["already_bound"] for result in results) == 1
    assert len(raw(seeded, "SELECT * FROM agent_card_binding")) == 1
    assert api().preview_binding(NUMBER, PIN, session_id=SESSION).data["already_bound"]


def test_balance_and_details_use_the_bound_account(seeded):
    before = raw(seeded, "SELECT * FROM account")
    assert bind().ok
    item = api().list_bound_cards().data["items"][0]
    assert item["balance_yuan"] == "46,634.00"
    assert api().card_detail(CARD).data["balance_yuan"] == item["balance_yuan"]
    assert not api().card_detail("card_credit_0002").ok
    assert raw(seeded, "SELECT * FROM account") == before


def test_filter_and_total_count_apply_to_bound_cards_only(seeded):
    assert bind().ok
    assert api().list_bound_cards("normal").data["total_count"] == 1
    assert api().list_bound_cards("lost").data == {"items": [], "total_count": 0}
    assert not api().list_bound_cards("").ok
    assert not api().list_bound_cards("unknown").ok


def test_card_number_requires_fresh_password_and_bound_ownership(foreign):
    assert not api().reveal_card_number(CARD, PIN, session_id=SESSION).ok
    assert bind().ok
    assert not api().reveal_card_number(CARD, "271828", session_id=SESSION).ok
    result = api().reveal_card_number(CARD, PIN, session_id=SESSION)
    assert result.ok and result.data == {"card_no": NUMBER}
    assert NUMBER not in json.dumps(result.facts)
    set_current_user(FOREIGN_USER)
    assert not api().reveal_card_number(CARD, PIN, session_id=SESSION).ok


def test_five_failures_lock_credentials_and_expiry_allows_retry(seeded, monkeypatch):
    module = api()
    monkeypatch.setattr(module, "_now", lambda: 1000.0)
    for _ in range(5):
        assert not module.preview_binding(NUMBER, "999999", session_id=SESSION).ok
    assert not module.preview_binding(NUMBER, PIN, session_id=SESSION).ok
    monkeypatch.setattr(module, "_now", lambda: 1301.0)
    assert module.preview_binding(NUMBER, PIN, session_id=SESSION).ok


def test_hashes_are_salted_and_audit_contains_no_credentials(seeded, caplog):
    assert bind().ok
    assert not api().preview_binding(NUMBER, "999999", session_id=SESSION).ok
    assert api().reveal_card_number(CARD, PIN, session_id=SESSION).ok
    secrets = raw(seeded, "SELECT salt, pin_hash FROM agent_card_secret")
    assert len({row["salt"] for row in secrets}) == 3
    assert all(row["pin_hash"] != PIN for row in secrets)
    audits = raw(seeded, "SELECT * FROM audit_log WHERE tool LIKE '%binding%' OR tool='reveal_card_number'")
    assert len(audits) == 4
    text = json.dumps(audits) + caplog.text
    assert NUMBER not in text and PIN not in text and "999999" not in text
    assert raw(seeded, "SELECT user_id, card_id FROM agent_card_binding") == [{"user_id": USER_ID, "card_id": CARD}]


def test_audit_failure_rolls_back_binding(seeded, monkeypatch):
    token = api().preview_binding(NUMBER, PIN, session_id=SESSION).data["token"]

    def failed_audit(*args, **kwargs):
        raise sqlite3.OperationalError("test audit unavailable")

    monkeypatch.setattr(dao, "insert_audit", failed_audit)
    assert not api().confirm_binding(token, session_id=SESSION).ok
    assert raw(seeded, "SELECT * FROM agent_card_binding") == []


def test_initialization_is_additive_and_preserves_existing_credentials(seeded):
    before = raw(seeded, "SELECT * FROM card")
    api().list_bound_cards()
    original = raw(seeded, "SELECT * FROM agent_card_secret")
    api().list_bound_cards()
    assert raw(seeded, "SELECT * FROM card") == before
    assert raw(seeded, "SELECT * FROM agent_card_secret") == original


def test_reset_of_an_augmented_database_remains_supported(seeded):
    from data.seed import generate

    assert bind().ok
    dao.close()
    generate(seeded, reset=True)
    dao.connect_db(seeded)
    assert api().list_bound_cards().data["total_count"] == 0
