"""Private scene reads: independent fixture sums and ownership checks."""
import sqlite3

import pytest

from data import dao
from tools import card_binding, query
from tests.conftest import assert_covered, raw


@pytest.fixture()
def scene(seeded):
    query.set_current_user(None)
    assert card_binding.list_bound_cards(audit=False).ok
    conn = dao.connection()
    conn.execute("INSERT INTO agent_card_binding VALUES (?, ?, ?)",
                 (query.current_user_id(), "card_savings_0001", 0))
    conn.commit()
    return seeded


def read(request):
    from tools._scene_reads import read as scene_read
    return scene_read("bill_report", request)


def test_scene_monthly_exact_and_numeric(scene):
    result = read({"period": "2026-08", "mode": "cards"})
    expected = raw(scene, "SELECT SUM(-amount) AS n FROM txn WHERE amount < 0 AND ts >= '2026-08' AND ts < '2026-09'")[0]["n"]
    assert result.ok, result.message
    assert result.data["summary"]["outgoing"] == expected
    assert "账户" in result.message and "无法" in result.message
    assert_covered(result.message, result.facts)


@pytest.mark.parametrize("payload", [
    {"period": "2026-13"}, {"period": "August"},
    {"period": "2026-08", "min_amount": 1.2},
    {"period": "2026-08", "min_amount": True},
    {"period": "2026-08", "direction": "other"},
])
def test_scene_invalid_request(scene, payload):
    assert not read(payload).ok


def test_scene_card_filters_and_detail(scene):
    result = read({"period": "2026-08", "mode": "transactions", "card_id": "card_savings_0001",
                   "direction": "out", "category": "餐饮"})
    assert result.ok
    assert "关联账户流水" in result.message
    assert all(x["direction"] == "out" and x["category"] == "餐饮" for x in result.data["items"])
    item = result.data["items"][0]
    detail = read({"mode": "detail", "card_id": "card_savings_0001", "txn_id": item["id"]})
    assert detail.ok and detail.data["items"][0]["id"] == item["id"]
    assert_covered(detail.message, detail.facts)


def test_scene_forbidden_card_and_foreign_detail(foreign):
    assert card_binding.list_bound_cards(audit=False).ok
    assert not read({"mode": "detail", "txn_id": "txn-x-0001"}).ok
    assert not read({"period": "2026-08", "card_id": "card_x_0001"}).ok


def test_scene_comparison_and_cautious_modes(scene):
    for mode in ("comparison", "duplicates", "refunds"):
        result = read({"period": "2026-08", "compare_period": "2026-07", "mode": mode})
        assert result.ok, result.message
        assert_covered(result.message, result.facts)
    assert "疑似" in read({"period": "2026-08", "mode": "duplicates"}).message
    assert "无法" in read({"period": "2026-08", "mode": "refunds"}).message


def test_scene_shared_account_deduplicates(scene):
    conn = dao.connection()
    card = conn.execute("SELECT * FROM card WHERE id='card_savings_0001'").fetchone()
    conn.execute("INSERT INTO card (id,user_id,account_id,card_no_mask,type,single_limit,daily_limit,status) VALUES (?,?,?,?,?,?,?,?)",
                 ("shared", card["user_id"], card["account_id"], "6222 **** **** 7777", card["type"], 1, 1, "normal"))
    conn.execute("INSERT INTO agent_card_binding VALUES (?,?,0)", (card["user_id"], "shared"))
    conn.commit()
    result = read({"period": "2026-08", "mode": "cards"})
    ids = [row["account_id"] for row in result.data["items"]]
    assert len(ids) == len(set(ids))
    assert any(len(row["cards"]) == 2 for row in result.data["items"])




def test_product_detail_works_for_product_without_holdings(scene):
    from tools.wealth_holdings import list_all_products
    from tools._wealth_risk import assess_risk, QUESTION_SCORE_RULE
    assert assess_risk({'age': 30, **{key: 3 for key in QUESTION_SCORE_RULE}}).ok
    catalog = list_all_products()
    assert catalog.ok
    held = {row['product_id'] for row in dao.connection().execute('SELECT product_id FROM holding')}
    product = next(row for row in catalog.data['items'] if row['product_id'] not in held)
    result = read({'kind': 'product_detail', 'product': product['product_id']})
    assert result.ok and result.data['items'][0]['product_id'] == product['product_id']
    assert '起购' in result.message and '期限' in result.message
    assert_covered(result.message, result.facts)
