"""新增群收款时，不再铺开历史群收款卡（用户 2026-10-31）。

「我这里要新增群收款，这里理解为新增群收款，下面的查看之前的收款可以不用展示出来」——
新增态目标单一：填金额、选人、发起；旧单子等发起/确认后再出现。
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


def _app(monkeypatch: pytest.MonkeyPatch, amount: str = "200") -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五", "赵六"],
                                                    "amount": amount},
                                          "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_existing_collection_hides_while_creating_a_new_one(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """先发起一笔 → 再点「＋ 新增群收款」→ 历史群收款卡不出现，只剩发起表单。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("向王五、赵六群收款200").run()
    next(button for button in app.button if button.label == "发起群收款").click().run()
    next(button for button in app.button if button.label == "✅ 确认").click().run()
    app.run()
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "已付 0/2" in marks, "办完之后应看得到这笔的状态卡"

    next(button for button in app.button if "新增群收款" in button.label).click().run()
    assert not app.exception, app.exception
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "已付 0/2" not in marks, "新增态不该再铺历史群收款卡"
    assert "登记已付" not in [button.label for button in app.button]
    assert app.number_input[0].value == 0.0, "新起一笔金额应归零"
