"""F20 追加：**送礼订单号 + 状态**（T38 落订单 / T37 查订单）。

| 验收点 | 用例 |
|---|---|
| T38：锁款成功后落 `created` 订单，返回订单号 | `test_record_order_after_plan` |
| T37：列出本人订单；状态取值非法 → 拒绝 | `test_list_orders_and_bad_status` |
| 编排层：送礼计划确认后回执带订单号，且能查到 | `test_orchestration_gift_creates_queryable_order` |
| 订单列表渲染：空 → 引导句；有 → 表格五列 | `test_render_gift_orders` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, render_lists, session_memory
from tests.conftest import count, raw
from tools import _gift_order as orders

SESSION = "sess-giftorder"
CONTACT = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, intent: str, slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_record_order_after_plan(wired) -> None:
    """T38：只落快照（不重复锁款），状态 created。"""
    before = raw(wired, "SELECT available FROM account WHERE type = 'savings'")[0]["available"]
    result = orders.record_gift_order("plan_1", "lock_1", CONTACT, "2026-10-30",
                                      50_000, 30_000,
                                      [{"item_id": "g_1", "name": "鲜花", "price": 30_000}])
    assert result.ok is True and result.data["status"] == "created"
    assert result.data["order_id"].startswith("ord_")
    assert raw(wired, "SELECT available FROM account WHERE type = 'savings'")[0]["available"] == before
    row = raw(wired, "SELECT * FROM gift_order WHERE id = ?", (result.data["order_id"],))[0]
    assert row["contact"] == CONTACT and row["total"] == 30_000 and row["status"] == "created"
    assert json.loads(row["items_json"])[0]["item_id"] == "g_1"


def test_list_orders_and_bad_status(wired) -> None:
    assert orders.list_gift_orders().facts["total_count"] == 0
    orders.record_gift_order("plan_2", "lock_2", CONTACT, "2026-11-01", 50_000, 20_000,
                             [{"item_id": "g_2", "name": "蛋糕", "price": 20_000}])
    listed = orders.list_gift_orders()
    assert listed.facts["total_count"] == 1
    assert listed.facts["items"][0]["status_cn"] == "待支付"
    assert orders.list_gift_orders("paid").facts["total_count"] == 0     # 状态筛选
    assert orders.list_gift_orders("bogus").ok is False                  # 非法状态


def test_orchestration_gift_creates_queryable_order(wired, monkeypatch) -> None:
    """送礼计划确认后：回执带订单号，且能被「查订单」查回来。"""
    _fake(monkeypatch, "gift_plan", {"contact": CONTACT, "date": "2026-10-30", "budget": "500"})
    card = orchestrator.handle("给王五送个生日礼物，预算 500 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"
    otp_stage = orchestrator.handle("确认", session_id=SESSION)
    assert "验证码" in otp_stage.reply
    done = orchestrator.handle("123456", session_id=SESSION)
    assert done.executed is True and "订单号 ord_" in done.reply
    assert count(wired, "gift_order") == 1

    _fake(monkeypatch, "gift_order_list")
    listed = orchestrator.handle("我的送礼订单", session_id=SESSION)
    assert listed.intent == "gift_order_list" and "list_gift_orders" in listed.tool_calls
    assert "| 订单号 | 收货人 | 送达日期 | 金额（元） | 状态 |" in listed.reply
    assert CONTACT in listed.reply and "待支付" in listed.reply


def test_render_gift_orders() -> None:
    """空 → 引导句（不印空表）；有 → 五列表格。"""
    empty = render_lists.render_gift_orders({"total_count": 0, "items": []})
    assert "还没有送礼订单" in empty and "|" not in empty
    text = render_lists.render_gift_orders({"total_count": 1, "items": [
        {"order_id": "ord_abc", "contact": "王五", "gift_date": "2026-10-30",
         "amount_yuan": "300.00", "status": "created", "status_cn": "待支付"}]})
    assert "| 订单号 | 收货人 | 送达日期 | 金额（元） | 状态 |" in text
    assert "ord_abc" in text and "待支付" in text and "300.00" in text


# ---------------- 2026-10-24 口径：黑名单不能送礼（直接拒、无需验证码） ----------------

def test_gift_rejects_blacklisted_contact(wired, monkeypatch) -> None:
    """黑名单收款人 → 送礼**直接拒绝**（不出卡、不锁款），文案带原因。"""
    from tools import payee
    pid = payee.add_payee("拉黑的朋友", "13800008888").data["payee_id"]
    assert payee.set_payee_blacklist(pid, "总是赖账").ok is True
    before = count(wired, "gift_order")
    _fake(monkeypatch, "gift_plan",
          {"contact": "拉黑的朋友", "date": "2026-10-30", "budget": "500"})
    result = orchestrator.handle("给拉黑的朋友送个生日礼物，2026-10-30 送达，预算 500 元",
                                 session_id=SESSION)
    assert result.executed is False and "CONFIRM_CARD" not in result.states
    assert "黑名单" in result.reply and "总是赖账" in result.reply
    assert count(wired, "gift_order") == before                  # 没有落单
    assert raw(wired, "SELECT COUNT(*) AS n FROM gift_order")[0]["n"] == before


# ---------------- 2026-10-24 人类规格：平台 / 收货地址 / 订单号幂等 ----------------

def test_order_no_is_idempotent(wired) -> None:
    """同一个平台订单号第二次下单 → 直接拒（幂等键）。"""
    first = orders.record_gift_order("plan_a", "lock_a", CONTACT, "2026-10-30", 50_000, 30_000,
                                     [{"item_id": "g1", "name": "香水", "price": 30_000}],
                                     platform="京东", address="上海市****路 1 号",
                                     platform_order_no="JD20261024001")
    assert first.ok is True
    dup = orders.record_gift_order("plan_b", "lock_b", CONTACT, "2026-10-31", 50_000, 20_000,
                                   [{"item_id": "g2", "name": "蛋糕", "price": 20_000}],
                                   platform="京东", platform_order_no="JD20261024001")
    assert dup.ok is False and "已存在送礼计划" in dup.message
    assert raw(wired, "SELECT COUNT(*) AS n FROM gift_order")[0]["n"] == 1


def test_platform_and_address_are_kept(wired) -> None:
    """平台/地址/订单号落库并能查回来。"""
    orders.record_gift_order("plan_c", "lock_c", CONTACT, "2026-11-01", 50_000, 20_000,
                             [{"item_id": "g3", "name": "鲜花", "price": 20_000}],
                             platform="京东", address="北京市****街 8 号",
                             platform_order_no="JD20261024002")
    item = orders.list_gift_orders().facts["items"][0]
    assert item["platform"] == "京东" and item["address"] == "北京市****街 8 号"
    assert item["platform_order_no"] == "JD20261024002"


def test_gift_card_shows_platform_and_order_no(wired, monkeypatch) -> None:
    """卡面印「平台 / 收货地址 / 平台订单号」（说了才印）。"""
    _fake(monkeypatch, "gift_plan",
          {"contact": CONTACT, "date": "2026-10-30", "budget": "500",
           "platform": "京东", "address": "上海市****路 1 号", "order_no": "JD20261024003"})
    card = orchestrator.handle(
        "我想 2026-10-30 在京东给王五买一瓶 399 的香水，送到上海市****路 1 号，订单号 JD20261024003",
        session_id=SESSION)
    assert "CONFIRM_CARD" in card.states
    assert "平台：京东" in card.reply and "收货地址" in card.reply and "JD20261024003" in card.reply
