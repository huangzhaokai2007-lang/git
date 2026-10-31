"""守卫：① 确认卡里点「修改」重新预检，**不该再弹解析卡**（收款人回填脱敏手机号）；
② 群收款展开后不再有「收起新增群收款」按钮（用户说那按钮没用，去掉）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _is_pick(widget) -> bool:
    """选卡按钮：新版是「选择这张卡」，收款人卡列表里的是「银行名 + 6222 掩码」。"""
    label = str(getattr(widget, "label", ""))
    return "6222" in label or "选择这张卡" in label


def _pick_card_if_asked(app) -> None:
    """2026-10-31：转账/定时转账现在先出「选择收款人的银行卡」卡面 —— 点第一张再继续。"""
    cards = [button for button in app.button if _is_pick(button)]
    if cards:
        cards[0].click().run()


def _pick_first_card(app) -> None:
    """2026-10-31：转账必须选收款卡 —— 点卡面上第一张银行卡再继续。"""
    candidates = [button for button in app.button if _is_pick(button)]
    if candidates:
        candidates[0].click().run()


def _app(monkeypatch: pytest.MonkeyPatch, intent: str, slots: dict) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_edit_after_picking_a_same_name_payee_does_not_reopen_the_picker(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """同名选完 → 出确认卡 → 点「✏️ 修改」→ 收款人字段已是脱敏号 → 预检不再弹解析卡。"""
    app = _app(monkeypatch, "transfer_single", {"payee": "李四", "amount": "500"})
    app.chat_input[0].set_value("给李四转 500 元").run()
    _pick_card_if_asked(app)
    assert not app.exception, app.exception
    next(button for button in app.button if button.label == "选这位").click().run()

    #: 2026-10-31：转账先出「选择银行卡」卡面 → 点第一张再继续
    _cards = [button for button in app.button if _is_pick(button)]
    if _cards:
        _cards[0].click().run()
    assert not app.exception, app.exception
    assert not [button for button in app.button if button.label == "选这位"]

    toggle = next((button for button in app.button
                   if button.label in ("✏️ 修改", "✏️ 收起修改")), None)
    if toggle is not None and toggle.label == "✏️ 修改":
        toggle.click().run()
    assert not app.exception, app.exception
    payee_box = next(box for box in app.text_input if box.label == "收款人")
    #: 用户要求：框里显示**姓名**（人看得懂）；重检时编排层自己用手机号去歧义。
    assert payee_box.value == "李四", f"收款人应显示姓名，实际 {payee_box.value!r}"

    #: 只改金额 → 重新预检 → 不该再弹解析卡（也不该换人）
    amount_box = next(box for box in app.text_input if "金额" in box.label)
    amount_box.set_value("120").run()
    next(button for button in app.button if button.label == "按修改重新预检").click().run()
    assert not app.exception, app.exception
    assert not [button for button in app.button if button.label == "选这位"], "改金额不该再解析"
    reply = str(app.session_state["messages"][-1]["text"])
    assert "120.00" in reply and "139****1001" in reply


def test_group_collect_has_no_collapse_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """群收款展开后不该再有「收起新增群收款」（那按钮点了没用，已去掉）。"""
    app = _app(monkeypatch, "aa_collect", {})
    app.chat_input[0].set_value("发起群收款").run()
    _pick_card_if_asked(app)
    assert not app.exception, app.exception
    labels = [button.label for button in app.button]
    assert "收起新增群收款" not in labels
    assert any("总金额" in box.label for box in app.number_input)   # 表单是展开的
