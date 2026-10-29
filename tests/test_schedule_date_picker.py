"""改预约时间：日历 + 时刻选择器（用户 2026-10-29 要求）。

原来是单个文本框（要手敲 `2026-11-05 09:00`，容易输错月/日）。现在：
- 日期用 `st.date_input`（点开是日历，年月日分开选，非法日期选不出来）；
- 时刻用 `st.time_input`；
- **必须晚于此刻**，否则「改到这个时间」按钮是灰的。
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


def _pending_schedule() -> None:
    """往临时库里插一条**待执行**预约（改时间卡只在有预约时渲染）。"""
    import sqlite3

    conn = sqlite3.connect(str(demo.build_temp_db()))
    with conn:
        conn.execute(
            "INSERT INTO scheduled_transfer (id, user_id, payee_id, amount, execute_at, status,"
            " tier, token, created_at, repeat_cycle, repeat_until)"
            " VALUES ('sch-ui-1', 'u_zhangsan_0001', 'payee_0003', 50000, '2030-01-01T09:00',"
            " 'pending', 'L1', NULL, '2026-10-29T09:00', '', '')")
    conn.close()


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    _pending_schedule()

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "scheduled_list", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_reschedule_uses_date_and_time_widgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """表单里是「新日期」（日历）+「新时刻」，不是原来的单行文本框。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的预约有哪些").run()
    assert not app.exception, app.exception
    assert [box.label for box in app.date_input][:1] == ["新日期"]
    assert [box.label for box in app.time_input][:1] == ["新时刻"]
    assert "新时间" not in [box.label for box in app.text_input]


def test_calendar_cannot_pick_the_past(monkeypatch: pytest.MonkeyPatch) -> None:
    """日历的下限是**今天**（选不到过去日期），默认给明天 09:00 → 按钮可用。"""
    import datetime

    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的预约有哪些").run()
    assert not app.exception, app.exception
    widget = app.date_input[0]
    assert widget.min == datetime.date.today(), "日历下限必须是今天"
    assert widget.value > datetime.date.today() or widget.value == widget.min
    assert next(b for b in app.button if b.label == "改到这个时间").disabled is False


def test_is_too_early_boundaries() -> None:
    """纯函数：过去 → 禁用；正好现在 → 禁用；未来 → 可用。"""
    import datetime
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "schedule_card_ui", REPO / "interfaces/web/schedule_card_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    now = datetime.datetime(2026, 10, 29, 18, 0)
    assert module.is_too_early(datetime.date(2026, 10, 28), datetime.time(9, 0), now) is True
    assert module.is_too_early(datetime.date(2026, 10, 29), datetime.time(18, 0), now) is True
    assert module.is_too_early(datetime.date(2026, 10, 29), datetime.time(18, 1), now) is False
    assert module.is_too_early(datetime.date(2026, 12, 11), datetime.time(0, 0), now) is False
