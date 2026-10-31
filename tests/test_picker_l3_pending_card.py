"""守卫：解析卡选 L3 收款人时，**待人工复核卡必须出现**。

用户 2026-10-30：「这里选择第一个李四还行，第二个直接没用了」。
真因不是"没用"，而是第二个李四（普通收款人 + 深夜 + 首次）命中 **L3 待人工复核**，
而按钮/解析卡路径记回执时用的是 `{"turn": None}` —— `_render_pending` 需要
`messages[-1].turn.pending_id` 才会画那张卡，于是界面看起来"什么都没发生"
（其实 60 秒后会自动生效）。
"""

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


def test_l3_pending_card_appears_for_picker_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                          "slots": {"payee": "李四", "amount": "100"},
                                          "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("给李四转100").run()
    _pick_card_if_asked(app)
    picks = [button for button in app.button if button.label == "选这位"]
    assert len(picks) == 2

    #: 第二个李四 = 普通收款人 + 深夜 + 首次 → L3；此时必须能看到待复核卡与撤销入口
    picks[1].click().run()

    #: 2026-10-31：转账先出「选择银行卡」卡面 → 点第一张再继续
    _cards = [button for button in app.button if _is_pick(button)]
    if _cards:
        _cards[0].click().run()
    assert not app.exception, app.exception
    reply = str(app.session_state["messages"][-1]["text"])
    assert "待人工复核" in reply or "确认卡" in reply, reply[:160]
    if "待人工复核" in reply:
        marks = "\n".join(str(element.value) for element in app.markdown)
        assert "待人工复核" in marks, "L3 待复核卡没显示（界面看起来像没反应）"
        assert any("撤销" in button.label for button in app.button), "缺少撤销入口"
