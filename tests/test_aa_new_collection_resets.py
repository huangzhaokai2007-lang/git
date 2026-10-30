"""守卫：重新发起群收款时表单要归零（用户 2026-10-31：「总金额还保留上次数字，应该刷新为 0」）。

两条入口都要管：
1. 点「＋ 新增群收款」按钮（`_reset_form()`）；
2. 聊天里重新说一句「我要 aa 收款」（app 挂 `aa-reset-form-pending`，卡面下一轮开头清）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402

#: 界面模块是同目录裸 import（streamlit 运行时 cwd 在 interfaces/web），用例里补进 sys.path
sys.path.insert(0, str(REPO / "interfaces" / "web"))
from interfaces.web import aa_card_ui                             # noqa: E402


def test_reset_form_clears_amounts_and_people(monkeypatch) -> None:
    """`_reset_form()` 要把金额/备注/参与人/按人金额一律清掉。"""
    state = {"aa-total": 390.0, "aa-title": "上次的", "aa-people": ["payee_0003"],
             "aa-amt-0": "130.00", "aa-mine": "130.00", "aa-amount-signature": "x",
             "aa-create-open": True, "其他": "别动我"}
    monkeypatch.setattr(aa_card_ui, "st", SimpleNamespace(session_state=state))
    aa_card_ui._reset_form()
    assert state["其他"] == "别动我"
    for key in ("aa-total", "aa-title", "aa-people", "aa-amt-0", "aa-mine",
                "aa-amount-signature", "aa-create-open"):
        assert key not in state, f"{key} 应被清掉"


def test_saying_start_group_collect_again_resets_the_total(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """先填 390，再重新说一句「我要 aa 收款」→ 总金额回到 0.00。"""
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
    app.number_input[0].set_value(390.0).run()
    assert app.number_input[0].value == 390.0

    app.chat_input[0].set_value("我要 aa 收款").run()
    assert not app.exception, app.exception
    assert app.number_input[0].value == 0.0, "重新发起时总金额应刷新为 0"
