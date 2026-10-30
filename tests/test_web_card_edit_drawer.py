"""修改确认卡是**卡内抽屉**（用户 2026-10-30：要有个拉开的效果）。

- 默认收起：卡上只有「✅ 确认 / ✖ 取消 / ✏️ 修改」
- 点「✏️ 修改」→ **这张卡内部**出现编辑控件（不是页面上另起一块）
- 时间字段一律是日历；定时转账还有「循环频率」下拉（一次性/每日/每周/每月）
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


def _app(monkeypatch: pytest.MonkeyPatch, intent: str, slots: dict) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_edit_panel_is_collapsed_until_clicked(clock,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """默认收起：只有三个按钮；点「✏️ 修改」后才出现编辑控件。"""
    app = _app(monkeypatch, "transfer_single", {"payee": "王五", "amount": "100"})
    app.chat_input[0].set_value("给王五转 100 元").run()
    assert not app.exception, app.exception
    labels = [button.label for button in app.button]
    assert "✅ 确认" in labels and "✖ 取消" in labels and "✏️ 修改" in labels
    assert "收款人" not in [box.label for box in app.text_input]        # 收起时没有编辑框

    next(button for button in app.button if button.label == "✏️ 修改").click().run()
    assert not app.exception, app.exception
    assert "收款人" in [box.label for box in app.text_input]            # 抽屉里出现编辑框
    assert "按修改重新预检" in [button.label for button in app.button]


def test_scheduled_edit_uses_calendar_and_cycle_picker(clock,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """定时转账抽屉：预约时间是**日历**，另有「循环频率」下拉（一次性/每日/每周/每月）。"""
    app = _app(monkeypatch, "transfer_scheduled",
               {"payee": "王五", "amount": "100", "schedule": "明天"})
    app.chat_input[0].set_value("明天给王五转 100 元").run()
    assert not app.exception, app.exception
    #: 抽屉开/关由 session 状态决定；这里点一下确保它是**打开**的（点错方向就再点一次）
    def _open_drawer() -> None:
        for _ in range(2):
            button = next((b for b in app.button if b.label in ("✏️ 修改", "✏️ 收起修改")), None)
            if button is None:
                return
            if button.label == "✏️ 收起修改" and app.date_input:
                return
            button.click().run()
            if app.date_input:
                return

    _open_drawer()
    assert not app.exception, app.exception
    assert "预约时间" in [box.label for box in app.date_input], "预约时间必须是日历"
    cycle = next(box for box in app.selectbox if box.label == "循环频率")
    for option in ("（一次性）", "每日", "每周", "每月"):
        assert option in cycle.options
