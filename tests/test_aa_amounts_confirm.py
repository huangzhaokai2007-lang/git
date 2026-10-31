"""守卫：按人金额改完必须点「✅ 确认修改」才生效，且合计/均摊/总额始终自洽。

用户 2026-10-31（急）：「在修改完每个人的金额下面要出现一个确认按钮，很多时候我修改完金额
上面的数字却没有对齐，例如我的均摊金额变成零」。

根因：汇总块直接读输入框的临时值，而输入框的回调只改了总额 —— 三份数字各说各话
（我承担 0 / 合计 112.66 / 总额 169）。现在：汇总只读「已确认的分摊」，输入框是草稿。
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

MONEY = re.compile(r"¥ ([\d,]+\.\d\d)")


def _app(monkeypatch: pytest.MonkeyPatch, total: float) -> AppTest:
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
    app.number_input[0].set_value(total).run()
    next(button for button in app.button if "修改参与人" in button.label).click().run()
    app.multiselect[0].set_value(list(app.multiselect[0].options)[:2]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    next(button for button in app.button if "按人设置金额" in button.label).click().run()
    return app


def _rows(app: AppTest) -> list[str]:
    marks = "\n".join(str(element.value) for element in app.markdown)
    return MONEY.findall(marks)[-4:]


def test_summary_is_consistent_before_any_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    """169 元 / 2 人：两人各 56.33、我 56.34、合计 169.00 —— 不会出现「我承担 0」。"""
    app = _app(monkeypatch, total=169.0)
    assert _rows(app) == ["56.33", "56.33", "56.34", "169.00"]


def test_edit_needs_confirmation_then_everything_realigns(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """改一个人到 60：先显示「有未确认的改动」，点确认后合计/总额一起变成 172.67。"""
    app = _app(monkeypatch, total=169.0)
    next(box for box in app.text_input if "应收" in box.label).set_value("60").run()
    captions = "\n".join(str(element.value) for element in app.caption)
    assert "有未确认的改动" in captions
    assert _rows(app) == ["56.33", "56.33", "56.34", "169.00"], "未确认时汇总不该变"

    next(button for button in app.button if "确认修改" in button.label).click().run()
    assert not app.exception, app.exception
    assert _rows(app) == ["60.00", "56.33", "56.34", "172.67"]
    assert app.number_input[0].value == 172.67, "总额应同步成 172.67"
