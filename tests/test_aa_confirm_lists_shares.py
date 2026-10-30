"""守卫：群收款确认卡必须**逐人列出**应收，不许出现「—」。

用户 2026-10-31：「点击发起群收款后，这里弹出的卡片有问题，应该列出所有人分别需要承担的
金额，显示 — 是什么鬼」。
根因：`_prepare_aa` 只在一键均摊时补 `per_person_amount_yuan / initiator_share_yuan /
collect_yuan`；按人金额 / 按份数均摊时这些键不存在 → 卡面全是「—」。
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


def test_confirm_card_lists_each_person_share(monkeypatch: pytest.MonkeyPatch) -> None:
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
    app.number_input[0].set_value(1280.0)
    next(button for button in app.button if "修改参与人" in button.label).click().run()
    app.multiselect[0].set_value(list(app.multiselect[0].options)[:2]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()

    next(button for button in app.button if "按人设置金额" in button.label).click().run()
    for index, value in ((0, "500"), (1, "300"), (2, "480")):      # 李四 / 王五 / 我
        boxes = [box for box in app.text_input
                 if "应收" in box.label or "我承担" in box.label]
        boxes[index].set_value(value).run()

    next(button for button in app.button if button.label == "发起群收款").click().run()
    assert not app.exception, app.exception

    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "| 收款人 | 应收（元） |" in marks, "确认卡应逐人列出应收"
    assert "| 李四 | 500.00 |" in marks
    assert "| 王五 | 300.00 |" in marks
    assert "**我承担**　480.00 元" in marks
    assert "**待收**　800.00 元" in marks
    assert "—" not in marks.split("**待收**")[0].split("**金额")[-1]
