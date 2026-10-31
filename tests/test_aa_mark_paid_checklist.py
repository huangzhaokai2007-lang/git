"""「登记已付」改成勾选式（用户 2026-10-31）。

原交互：下拉选一个人 → 点「登记已付」，只能一个一个来。
新交互：点「登记已付」→ 放出未付的人（多选勾选）→「确认登记 / 取消」；
勾了多位时按顺序逐个登记（工具层一次一人、各自走确认卡），第一张确认完自动接下一张。
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


def _create_group_collect(monkeypatch: pytest.MonkeyPatch) -> AppTest:
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
    app.number_input[0].set_value(30.0)
    next(button for button in app.button if "修改参与人" in button.label).click().run()
    app.multiselect[0].set_value(list(app.multiselect[0].options)[:2]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    next(button for button in app.button if button.label == "发起群收款").click().run()
    next(button for button in app.button if button.label == "✅ 确认").click().run()
    return app


def test_mark_paid_opens_a_checklist_with_confirm_and_cancel(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """点「登记已付」→ 出现勾选控件 + 「确认登记 / 取消」，不再是下拉一个一个选。"""
    app = _create_group_collect(monkeypatch)
    next(button for button in app.button if button.label == "登记已付").click().run()
    assert not app.exception, app.exception
    assert any("勾选已经付给你的人" in box.label for box in app.multiselect)
    labels = [button.label for button in app.button]
    assert "确认登记" in labels and "取消" in labels
    ok = next(button for button in app.button if button.label == "确认登记")
    assert ok.disabled is True, "还没勾人时不能确认"


def test_勾了多个人会排队逐个登记(monkeypatch: pytest.MonkeyPatch) -> None:
    """勾两个人 → 第一张确认卡 → 确认 → 自动接上第二张 → 全部付清。"""
    app = _create_group_collect(monkeypatch)
    next(button for button in app.button if button.label == "登记已付").click().run()
    app.multiselect[0].set_value(list(app.multiselect[0].options)).run()
    next(button for button in app.button if button.label == "确认登记").click().run()
    assert not app.exception, app.exception

    first = next((button for button in app.button if button.label == "✅ 确认"), None)
    assert first is not None, "第一个人应出确认卡"
    first.click().run()

    second = next((button for button in app.button if button.label == "✅ 确认"), None)
    assert second is not None, "确认完第一个人后应自动接上第二张确认卡"
    second.click().run()

    reply = str(app.session_state["messages"][-1]["text"])
    assert "全部付清" in reply, reply[:160]
