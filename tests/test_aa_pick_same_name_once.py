"""两条守卫（用户 2026-10-30）：

- 「我点完其中一个李四后另外一个李四不见了」→ 卡面候选按 **id**，同名两位都在、都能选；
  选的是 id，所以**不需要**解析卡。
- 「我选完李四的收款人解析卡后还是会跳出『还剩这几项』」→ 聊天里只说名字（同名）时
  解析卡出现一次，选完直接出确认卡，**不再弹填表卡**。
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


def _app(monkeypatch: pytest.MonkeyPatch, slots: dict) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_card_candidates_include_both_same_name_people(monkeypatch: pytest.MonkeyPatch) -> None:
    """卡面候选带脱敏号：两个「李四」都在，选一个就出确认卡（无需解析卡）。"""
    app = _app(monkeypatch, {})
    app.chat_input[0].set_value("发起群收款").run()
    assert not app.exception, app.exception
    app.number_input[0].set_value(300.0)
    next(button for button in app.button if "修改参与人" in button.label).click().run()

    labels = list(app.multiselect[0].options)
    li_si = [label for label in labels if "李四" in label]
    assert len(li_si) == 2, f"两个李四都该在候选里：{labels}"

    app.multiselect[0].set_value([li_si[0]]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    next(button for button in app.button if button.label == "发起群收款").click().run()
    assert not app.exception, app.exception
    assert not [button for button in app.button if button.label == "选这位"], "按 id 选不该再解析"
    reply = str(app.session_state["messages"][-1]["text"])
    assert "群收款确认卡" in reply


def test_picker_pick_does_not_reopen_the_slot_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """聊天里只说「李四」（同名）→ 解析卡一次 → 选完出确认卡，**没有**「还差这几项」。"""
    app = _app(monkeypatch, {"payee_ids": ["李四"], "amount": "300"})
    app.chat_input[0].set_value("我和李四群收款 300 元").run()
    assert not app.exception, app.exception
    assert len([button for button in app.button if button.label == "选这位"]) == 2

    next(button for button in app.button if button.label == "选这位").click().run()
    assert not app.exception, app.exception
    assert "群收款确认卡" in str(app.session_state["messages"][-1]["text"])
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "还差这几项" not in marks, "选完解析卡不该再弹填表卡"
