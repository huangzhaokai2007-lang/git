"""群收款：聊天里说全了也**先出编辑卡，再出确认卡**（用户 2026-10-31）。

用户：「这里直接跳入确认卡面，而不是编译卡面（编辑卡面）」——
说了「向王五、赵六群收款 300」不该直接怼确认卡，而应铺好发起卡（带人、带金额、可改），
点「发起群收款」后才进确认卡。
"""

from __future__ import annotations

import re
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
                                          "slots": {"payee_ids": ["王五", "赵六"],
                                                    "amount": "300"},
                                          "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_chat_opens_the_edit_card_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """聊天说全 → 先出**发起卡**（金额/参与人已预填），确认卡还不出现。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("向王五、赵六群收款300").run()
    assert not app.exception, app.exception

    assert app.number_input[0].value == 300.0, "总额应预填 300"
    marks = "\n".join(str(element.value) for element in app.markdown)
    chips = re.findall(r'aa-chip">([^<]+)<', marks)
    assert "王五" in chips and "赵六" in chips, chips
    assert not any("确认这一笔" in str(element.value) for element in app.markdown), \
        "聊天里不该直接跳到确认卡"
    assert any(button.label == "发起群收款" for button in app.button)


def test_clicking_start_goes_to_the_confirm_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """点「发起群收款」→ 这时才出确认卡，且每人 100（300 ÷ 3 份）。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("向王五、赵六群收款300").run()
    next(button for button in app.button if button.label == "发起群收款").click().run()
    assert not app.exception, app.exception
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "确认这一笔" in marks
    assert any(button.label == "✅ 确认" for button in app.button)
