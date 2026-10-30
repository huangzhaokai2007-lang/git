"""守卫：成功发起并确认群收款后，**发起表单不该再自己弹出来**。

用户 2026-10-30：「我有时候成功发起群收款后，这个卡面不会消失」。
根因：成功后没清 `pending_slots` 里的 aa_collect 记录 → 下一轮被"补槽中"逻辑自动展开并回填旧值。
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


def test_form_disappears_after_group_collect_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """发起 → 确认 → 办完后：只剩「＋ 新增群收款」按钮，不再有金额/参与人/发起按钮。"""
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五"], "amount": "300"},
                                          "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("我和王五群收款 300 元").run()
    assert not app.exception, app.exception
    #: 2026-10-31：网页里先出**发起卡**，点发起才进确认卡
    next(button for button in app.button if button.label == "发起群收款").click().run()
    next(button for button in app.button if button.label == "✅ 确认").click().run()
    assert not app.exception, app.exception

    # 办完后再渲染：发起表单必须收起
    labels = [button.label for button in app.button]
    assert "发起群收款" not in labels, "办完不该还留着发起按钮"
    assert not [box for box in app.number_input if "总金额" in box.label], "办完不该还有金额输入"
    assert any(label == "＋ 新增群收款" for label in labels)
