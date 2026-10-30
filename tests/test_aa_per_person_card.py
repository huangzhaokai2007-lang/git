"""守卫：群收款「按人设置金额」要真的生效，标签要人名不是 payee_id。

用户 2026-10-30：「按人设置收款人重新设置后没有任何效果，修改后也无法正常进行群收款」。
根因：参与人改 id 后，按人行标签印成了 `payee_0001`，而下发的 amounts 也用 id 作键，
`_parse_per_person` 只认姓名/别名 → 判「不在名单里」。
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


def test_per_person_amounts_take_effect_and_labels_are_names(
        monkeypatch: pytest.MonkeyPatch) -> None:
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
    app.chat_input[0].set_value("发起群收款").run()
    app.number_input[0].set_value(300.0)
    next(button for button in app.button if "修改参与人" in button.label).click().run()
    options = list(app.multiselect[0].options)
    app.multiselect[0].set_value(options[:2]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    assert not app.exception, app.exception

    labels = [box.label for box in app.text_input if "应收" in box.label]
    assert labels, "应该有按人金额输入框"
    assert not any("payee_" in label for label in labels), f"标签不该是 id：{labels}"

    boxes = [box for box in app.text_input if "应收" in box.label]
    boxes[0].set_value("100").run()
    boxes = [box for box in app.text_input if "应收" in box.label]
    boxes[1].set_value("150").run()
    next(button for button in app.button if button.label == "发起群收款").click().run()
    assert not app.exception, app.exception

    reply = str(app.session_state["messages"][-1]["text"])
    assert "群收款确认卡" in reply, reply[:200]
    assert "按人金额" in reply
    assert "100.00 元" in reply and "150.00 元" in reply, reply
