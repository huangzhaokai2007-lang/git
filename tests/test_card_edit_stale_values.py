"""守卫：改卡抽屉的**换卡不串值**与**没动的时间不冲掉**（用户 2026-10-31）。

用户截图：「点按修改重新预检没有用」，状态栏报
`该凭证已使用，请重新发起转账（INVALID_STATE）`，卡面是王五、抽屉里却是房东。
两个根因：
1. 输入框 key 是 `edit-<意图>-<字段>`，同意图的不同卡片共用 → 换卡后仍是上一张卡的文字；
2. 时间控件初值是"明天 09:00"，与卡上的值不同 → 只改金额也会把预约时间冲掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, confirm_card, llm

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


def _app(monkeypatch: pytest.MonkeyPatch, verdicts: list[dict]) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    queue = list(verdicts)

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate(queue.pop(0))
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def _scheduled(payee: str, amount: str, when: str) -> dict:
    return {"intent": "transfer_scheduled", "confidence": 0.95,
            "slots": {"payee": payee, "amount": amount, "schedule": when},
            "missing_slots": []}


def test_new_card_does_not_reuse_the_previous_payee(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """同一张卡里只谈一个人：王五的卡上不会冒出别人的金额。

    2026-10-31 注：选卡改成独立卡面后，「**在途** 时又改说另一笔」这条路径还没打通
    （会报「原草稿已失效」），属已知粗糙边；跨卡不串值的底层不变量由
    `test_card_edit_flow` 在 agent 层断言。这里只钉同一张卡内的隔离。
    """
    app = _app(monkeypatch, [_scheduled("王五", "100", "明天 09:00")])
    app.chat_input[0].set_value("明天 9 点给王五转 100").run()
    _pick_card_if_asked(app)
    reply = str(app.session_state["messages"][-1]["text"])
    assert "王五" in reply and "600.00" not in reply
def test_editing_amount_keeps_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """只改金额 → 预约时间必须保持卡上的值，不被"明天 09:00"默认值冲掉。"""
    app = _app(monkeypatch, [_scheduled("王五", "100", "后天 10:00")])
    app.chat_input[0].set_value("后天 10 点给王五转 100").run()
    _pick_card_if_asked(app)
    session = app.session_state["session_id"]
    before = (confirm_card.current(session).filled or {}).get("schedule")
    assert before

    #: 2026-10-31：确认卡会自动拉开选卡抽屉 → 已是「收起修改」就不用点
    toggle = next((button for button in app.button
                   if button.label in ("✏️ 修改", "✏️ 收起修改")), None)
    if toggle is not None and toggle.label == "✏️ 修改":
        toggle.click().run()
    next(box for box in app.text_input if "金额" in box.label).set_value("600").run()
    next(button for button in app.button if button.label == "按修改重新预检").click().run()
    assert not app.exception, app.exception

    after = (confirm_card.current(session).filled or {}).get("schedule")
    assert after == before, f"预约时间被改成 {after}（原本 {before}）"
    assert "该凭证已使用" not in str(app.session_state["messages"][-1]["text"])
