"""守卫：从收款人卡点「加白名单」→ 填表卡（额度 + **日历**有效期）→ 出白名单确认卡。

用户 2026-10-28/29 两条要求串起来：
- 每次新增白名单都要问**额度**与**有效期**；
- 所有填日期的地方都用**日历**。
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
            return schema.model_validate({"intent": "payee_list", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_add_whitelist_asks_quota_and_expire_via_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """普通收款人 → 加白名单 → 填表卡里有「专属额度」文本框与「有效期」**日历**，填完出确认卡。"""
    import datetime

    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的收款人有哪些").run()
    assert not app.exception, app.exception

    # 展开一位「普通」收款人（白名单那位只会给「移出白名单」）
    normal = next(button for button in app.button
                  if "****" in button.label and "普通" in button.label)
    normal.click().run()
    assert not app.exception, app.exception

    next(button for button in app.button if button.label == "加白名单").click().run()
    assert not app.exception, app.exception

    labels = [box.label for box in app.text_input]
    assert any("专属额度" in label for label in labels), f"没问额度：{labels}"
    assert [box.label for box in app.date_input] == ["有效期"], "有效期必须是日历控件"

    for box in app.text_input:
        if "专属额度" in box.label:
            box.set_value("500")
    app.date_input[0].set_value(datetime.date.today() + datetime.timedelta(days=60)).run()

    next(button for button in app.button if button.label == "继续").click().run()
    assert not app.exception, app.exception
    reply = str(app.session_state["messages"][-1]["text"])
    assert "白名单确认卡" in reply
    assert "500.00 元/月" in reply                     # 额度进了卡面
    assert "有效期至" in reply                          # 有效期也进了卡面
