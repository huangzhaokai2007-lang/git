"""F18 补全：订阅列表 / 订阅提醒的每一行都带「取消订阅」按钮。

只测本次新增的界面层行为；订阅工具与取消流程本身在 `test_tools_subscription*` /
`test_subscription_cancel_flow.py` 里已覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier
from interfaces.web import components, subscription_ui

SUBS = [
    {"id": "sub_0001", "merchant": "云音乐", "amount": 1500, "amount_yuan": "15.00",
     "cycle": "monthly", "cycle_cn": "每月", "next_charge_date": "2026-09-14"},
    {"id": "sub_0002", "merchant": "健悦健身", "amount": 19900, "amount_yuan": "199.00",
     "cycle": "monthly", "cycle_cn": "每月", "next_charge_date": "2026-09-20"},
]


def _turn(items, *, intent="subscription_list", status="active", error_code=None):
    return {"trace_id": "subs-ui", "intent": intent, "error_code": error_code,
            "facts": {"status": status, "items": items, "subscription_count": len(items)},
            "states": ["REPLY"], "tier": "L0"}


def _render(turn, actions):
    def page(turn, actions):
        from interfaces.web import components, subscription_ui
        components.transcript([{"role": "assistant", "text": "订阅", "turn": turn}],
                              subscription_card=lambda current, key: subscription_ui.render_turn(
                                  current, ask=actions.append, key_prefix=key))
    return AppTest.from_function(page, args=(turn, actions), default_timeout=30).run()


def test_every_subscription_row_has_a_cancel_button() -> None:
    actions: list[str] = []
    at = _render(_turn(SUBS), actions)
    assert not at.exception
    assert [b.label for b in at.button].count("取消订阅") == 2


def test_cancel_button_sends_the_merchant_to_the_agent() -> None:
    """按钮只把「取消 X 订阅」交给编排层，界面不自己取消。"""
    actions: list[str] = []
    at = _render(_turn(SUBS), actions)
    next(b for b in at.button if b.key == "subs-0-cancel-sub_0001").click().run()
    assert actions == ["取消云音乐订阅"]


def test_cancelled_list_gets_no_buttons() -> None:
    """查已取消的订阅是只读记录，再给「取消」会让人以为能取消第二次。"""
    actions: list[str] = []
    at = _render(_turn(SUBS, status="cancelled"), actions)
    assert not at.exception
    assert not any(b.label == "取消订阅" for b in at.button)


def test_reminder_rows_show_days_left_and_can_be_cancelled() -> None:
    items = [{**SUBS[0], "subscription_id": "sub_0001", "days_left": 2, "days_cn": "2 天后"}]
    actions: list[str] = []
    at = _render(_turn(items, intent="subscription_remind"), actions)
    assert not at.exception
    assert any("2 天后" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "取消订阅").click().run()
    assert actions == ["取消云音乐订阅"]


def test_empty_or_failed_turn_renders_nothing() -> None:
    actions: list[str] = []
    at = _render(_turn([], error_code="INVALID_STATE"), actions)
    assert not at.exception
    assert not any(b.label == "取消订阅" for b in at.button)


def test_overdue_row_still_offers_cancel() -> None:
    """逾期项也是 active，同样可以取消；明细要说「原定…已过 N 天」，不能说「N 天后」。"""
    overdue = [{**SUBS[0], "subscription_id": "sub_0001", "days_overdue": 4,
                "overdue_cn": "已过 4 天"}]
    facts = {"status": "active", "items": [], "overdue_items": overdue,
             "overdue_count": 1, "overdue_total_amount_yuan": "15.00"}
    turn = {"trace_id": "subs-overdue", "intent": "subscription_remind", "error_code": None,
            "facts": facts, "states": ["REPLY"], "tier": "L0"}
    actions: list[str] = []
    at = _render(turn, actions)
    assert not at.exception
    assert any("已过 4 天" in m.value for m in at.markdown)
    assert not any("4 天后" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "取消订阅").click().run()
    assert actions == ["取消云音乐订阅"]


def test_manage_subscriptions_scene_shows_cancel_buttons_end_to_end(monkeypatch, seeded: Path):
    """点「管订阅」→ 列表每行有取消按钮 → 点它真的进取消确认流程。"""
    from tests.test_card_binding_ui import app, click

    real = classifier.classify

    def route(text, history=None):
        #: 编排层会把「当前日期」作为第二行附在 user 消息里（`_with_date_context`），只比首行。
        if text.split("\n", 1)[0].strip() == "我有哪些订阅":
            return classifier.IntentOut(intent="subscription_list", confidence=1.0, slots={})
        return real(text, history)                    # 「取消X订阅」走确定性规则，不调模型

    monkeypatch.setattr(classifier, "classify", route)
    at = app(monkeypatch, seeded)
    click(at, "管订阅")
    assert not at.exception
    assert [b.label for b in at.button].count("取消订阅") == 4      # seed 里 4 条 active

    next(b for b in at.button if b.label == "取消订阅").click().run()
    assert not at.exception
    last = at.session_state["messages"][-1]["turn"]
    assert last["intent"] == "subscription_cancel"
    assert not last["executed"]                                   # 只出确认卡，没真取消
