"""群收款分摊的两种快捷 + 明细表（用户 2026-10-31）。

- **一键均摊**：全员（含我）拉平，合计不变（零头记我这边，不会出现 999.99）；
- **按份数均摊**：每份 = 总额 ÷ 总份数，份数可不同（1/2/2），确认后写进「按人设置金额」；
- 有人被单独设过金额时，原来那个「均摊金额」大字换成**每人应摊明细表**。
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

AMOUNT_LABELS = ("应收", "我承担")


def _app(monkeypatch: pytest.MonkeyPatch, *, total: float, people: int) -> AppTest:
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
    app.number_input[0].set_value(total)
    next(button for button in app.button if "修改参与人" in button.label).click().run()
    app.multiselect[0].set_value(list(app.multiselect[0].options)[:people]).run()
    next(button for button in app.button if button.label == "添加所选").click().run()
    return app


def _amounts(app: AppTest) -> list[str]:
    return [box.value for box in app.text_input if any(word in box.label for word in AMOUNT_LABELS)]


def test_weight_split_fills_per_person_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """1000 元 · 3 人 · 份数 1/2/2 → 200 / 400 / 400，合计仍是 1000。"""
    app = _app(monkeypatch, total=1000.0, people=2)
    next(button for button in app.button if "按人设置金额" in button.label).click().run()
    next(button for button in app.button if button.label == "按份数均摊").click().run()
    assert not app.exception, app.exception
    for index, weight in enumerate((1, 2, 2)):
        boxes = [box for box in app.number_input if "份数" in box.label]
        boxes[index].set_value(weight).run()

    next(button for button in app.button if button.label == "确认按份数均摊").click().run()
    assert not app.exception, app.exception
    assert _amounts(app) == ["200.00", "400.00", "400.00"]
    assert app.number_input[0].value == 1000.0

    marks = "\n".join(str(element.value) for element in app.markdown)
    #: 2026-10-31：明细并进「参与人」块（HTML 行），不再用 markdown 表格
    assert "aa-split-row" in marks, "按人设置后应显示每人应摊明细"
    assert "¥ 1,000.00" in marks, "合计应显示 1,000.00"


def test_even_split_restores_equal_shares_without_changing_total(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """先手改成 100/200/700，再点「一键均摊」→ 拉平且合计仍是 1000.00。"""
    app = _app(monkeypatch, total=1000.0, people=2)
    next(button for button in app.button if "按人设置金额" in button.label).click().run()
    for index, value in enumerate(("100", "200", "700")):
        boxes = [box for box in app.text_input if any(word in box.label for word in AMOUNT_LABELS)]
        boxes[index].set_value(value).run()
    assert _amounts(app) == ["100", "200", "700"]
    next(button for button in app.button if "确认修改" in button.label).click().run()

    next(button for button in app.button if button.label == "一键均摊").click().run()
    assert not app.exception, app.exception
    assert app.number_input[0].value == 1000.0
    shares = [float(value) for value in _amounts(app)]
    assert sum(shares) == pytest.approx(1000.0), shares
    assert max(shares) - min(shares) < 0.02, shares
