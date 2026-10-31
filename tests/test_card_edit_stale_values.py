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


def test_drawer_does_not_carry_values_to_a_new_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """先改「王五」的卡，再出一张「张小美」的卡 → 抽屉里必须是张小美。"""
    app = _app(monkeypatch, [_scheduled("王五", "100", "明天 09:00"),
                             _scheduled("张小美", "600", "后天 10:00")])
    app.chat_input[0].set_value("明天 9 点给王五转 100").run()
    #: 2026-10-31：确认卡会自动拉开选卡抽屉 → 已是「收起修改」就不用点
    toggle = next((button for button in app.button
                   if button.label in ("✏️ 修改", "✏️ 收起修改")), None)
    if toggle is not None and toggle.label == "✏️ 修改":
        toggle.click().run()
    assert next(box.value for box in app.text_input if box.label == "收款人") == "王五"

    app.chat_input[0].set_value("后天 10 点给张小美转 600").run()
    #: 抽屉可能还开着（按钮文案变成「收起修改」）→ 两种文案都点一下，确保是展开态
    toggle = next((button for button in app.button
                   if button.label in ("✏️ 修改", "✏️ 收起修改")), None)
    if toggle is not None and "收起" in toggle.label:
        pass                     # 已经展开
    elif toggle is not None:
        toggle.click().run()
    assert next(box.value for box in app.text_input if box.label == "收款人") == "张小美"


def test_editing_amount_keeps_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """只改金额 → 预约时间必须保持卡上的值，不被"明天 09:00"默认值冲掉。"""
    app = _app(monkeypatch, [_scheduled("王五", "100", "后天 10:00")])
    app.chat_input[0].set_value("后天 10 点给王五转 100").run()
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
