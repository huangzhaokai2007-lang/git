"""缺槽「填表卡」（`interfaces/web/slot_form_ui.py`）的回归守卫。

用户 2026-10-27 建议「或许可以直接弹出卡片来填写呢」：缺槽时弹一张表单卡，
填完点「继续」= 与文字补槽完全同一条路径（合并槽位 → 重跑 `write_flow.start`）。
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

FILLS = {"联系人": "王五", "日期": "2026-11-20", "预算": "300",
         "平台订单号": "JD20261024001", "收货地址": "上海市某某路 1 号"}


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "gift_plan", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_missing_slots_open_a_form_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """说一句缺槽的话 → 直接出现填表卡（字段 = 缺的槽位，平台是下拉）。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我要送礼").run()
    assert not app.exception, app.exception
    labels = [box.label for box in app.text_input]
    for expect in ("联系人", "日期", "预算", "收货地址", "平台订单号"):
        assert expect in labels, f"填表卡缺字段：{expect}；实际 {labels}"
    assert [box.label for box in app.selectbox][:1] == ["平台"]
    assert any(button.label == "继续" for button in app.button)


def test_submitting_the_form_creates_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """填完点「继续」→ 出确认卡，且填表卡消失（登记被清）。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我要送礼").run()
    for box in app.text_input:
        if box.label in FILLS:
            box.set_value(FILLS[box.label])
    app.selectbox[0].set_value("京东")
    next(button for button in app.button if button.label == "继续").click().run()

    assert not app.exception, app.exception
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "确认卡" in marks and "王五" in marks and "JD20261024001" in marks
    assert not any(button.label == "继续" for button in app.button), "补齐后不该还有填表卡"
