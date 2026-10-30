"""守卫：群收款卡面选同名 → 解析卡只出**一次**，选完直接进确认卡。

用户 2026-10-30：「在我发起群收款后选择了李四后，还是会触发收款人解析，这不应该」。

根因：卡面把**默认均摊值**当「按人金额」发下去（含我口径下 Σ每人 ≠ 含我总额）→
INVALID_ARGUMENT → 卡面停在发起态 → 再点「发起群收款」又触发解析。
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


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_picking_a_same_name_payee_once_goes_straight_to_confirm(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """卡面选「李四」→ 发起 → 解析卡出现一次 → 选一位 → **直接出确认卡**，不再弹解析。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("发起群收款").run()
    assert not app.exception, app.exception
    app.number_input[0].set_value(300.0)
    app.multiselect[0].set_value(["李四"]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    next(button for button in app.button if button.label == "发起群收款").click().run()
    assert not app.exception, app.exception
    assert len([button for button in app.button if button.label == "选这位"]) == 2   # 同名两位

    next(button for button in app.button if button.label == "选这位").click().run()
    assert not app.exception, app.exception
    assert not [button for button in app.button if button.label == "选这位"], "选完不该再弹解析卡"
    reply = str(app.session_state["messages"][-1]["text"])
    assert "群收款确认卡" in reply
    assert "每人：150.00 元" in reply        # 300 ÷ (1 人 + 我)
    assert "我承担：150.00 元" in reply
