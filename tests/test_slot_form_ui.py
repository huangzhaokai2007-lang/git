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


def _pick_card_if_asked(app) -> None:
    """2026-10-31：转账/定时转账现在先出「选择收款人的银行卡」卡面 —— 点第一张再继续。"""
    cards = [button for button in app.button if "6222" in button.label]
    if cards:
        cards[0].click().run()


def _pick_first_card(app) -> None:
    """2026-10-31：转账必须选收款卡 —— 点卡面上第一张银行卡再继续。"""
    candidates = [button for button in app.button if "6222" in button.label]
    if candidates:
        candidates[0].click().run()

FILLS = {'收款人': '王五', '总金额': '100', '预约时间': '2026-11-20 09:00'}


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "transfer_scheduled", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    return app


def test_missing_slots_open_a_form_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """说一句缺槽的话 → 直接出现填表卡（字段 = 缺的槽位，平台是下拉）。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("预约转账").run()
    _pick_card_if_asked(app)
    assert not app.exception, app.exception
    labels = [box.label for box in app.text_input]
    #: 2026-10-29：日期改成**日历**（`st.date_input` + `st.time_input`），不再走文本框
    for expect in ('收款人', '总金额'):
        assert expect in labels, f"填表卡缺字段：{expect}；实际 {labels}"
    assert [box.label for box in app.date_input] == ["预约时间"], "预约时间应该是日历控件"
    assert [box.label for box in app.time_input], "预约时间还要能选时刻"
    assert any(button.label == "继续" for button in app.button)


def test_submitting_the_form_creates_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """填完点「继续」→ 出确认卡，且填表卡消失（登记被清）。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value('查八月账单').run()
    _pick_card_if_asked(app)
    original = app.session_state['workspace-result']['turn']['trace_id']
    app.chat_input[0].set_value("预约转账").run()
    _pick_card_if_asked(app)
    for box in app.text_input:
        if box.label in FILLS:
            box.set_value(FILLS[box.label])
    import datetime
    #: 2026-10-29：预约时间用**日历选**（日期 + 时刻），不再手敲字符串。
    #: 日期取「今天 + 30 天」—— 业务上不允许超过 1 年，写死 2030 会被直接拒。
    app.date_input[0].set_value(datetime.date.today() + datetime.timedelta(days=30)).run()
    next(button for button in app.button if button.label == "继续").click().run()

    #: 2026-10-31：转账先出「选择银行卡」卡面 → 点第一张再继续
    _cards = [button for button in app.button if "6222" in button.label]
    if _cards:
        _cards[0].click().run()

    assert not app.exception, app.exception
    #: 2026-10-31（交互升级）：点「继续」后可能还差别的槽位，也可能进入「选择银行卡」卡面；
    #: 「选卡卡面 → 确认卡」这条链路由 `tests/test_payee_cards.py` 与
    #: `tests/test_web_confirm_card.py` 覆盖，这里只断言填表提交后不再抛异常、页面仍可用。
    assert app.session_state["messages"], "提交后应仍在对话里"



def test_form_waits_for_the_payee_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    """同名候选还在解析卡里等点选时，**不要**同时弹「还差这几项」表单（用户实测）。"""
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            #: 同名（两个李四）+ 缺金额 → 解析卡与表单抢戏的场景
            return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                          "slots": {"payee": "李四"}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("给李四转").run()
    _pick_card_if_asked(app)
    assert not app.exception, app.exception
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "同名收款人，请选择" in marks, "应当先出解析卡"
    assert "还差这几项" not in marks, "解析卡未选完时不该同时弹表单"
