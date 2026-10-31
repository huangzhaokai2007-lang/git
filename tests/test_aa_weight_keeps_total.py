"""守卫：点「按份数均摊」不能把总金额清零（用户 2026-10-31）。

根因：按人金额输入框还空着时，`_sync_total()` 把 `总额 = 0 + 0` 写回总额 → 直接清零。
现在「算出来 <= 0」就**不动**总额。
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


def test_weight_panel_does_not_reset_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    """填 200 → 加人 → 展开按人金额 → 点「按份数均摊」：总额仍是 200。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("发起群收款").run()
    app.number_input[0].set_value(200.0).run()
    next(button for button in app.button if "修改参与人" in button.label).click().run()
    app.multiselect[0].set_value(list(app.multiselect[0].options)[:1]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    assert app.number_input[0].value == 200.0

    next(button for button in app.button if "按人设置金额" in button.label).click().run()
    next(button for button in app.button if button.label == "按份数均摊").click().run()
    assert not app.exception, app.exception
    assert app.number_input[0].value == 200.0, "点按份数均摊不该清零总额"
    assert any("份数" in box.label for box in app.number_input), "应出现份数输入"
