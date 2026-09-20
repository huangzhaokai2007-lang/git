"""卡 20 单测：网页端「添加收款人」表单（Streamlit `AppTest` 真跑 `interfaces/web/app.py`）。

盖三件事：
① 只有 `turn.intent == "payee_add"` 时才弹表单（别的意图不弹）；
② 提交**经 agent 层**落库 —— 临时库里出现脱敏后的收款人，回执里出现"现在可以给他转账了"；
③ 界面仍然不越层：`app.py` / `components.py` 只 import `agent/`（AST 守卫见 `tests/test_web_layering.py`）。
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

from tests.conftest import count, raw                             # noqa: E402
from tests.test_tools_payee import FULL, MASKED                   # noqa: E402


def _app(monkeypatch: pytest.MonkeyPatch, verdict: dict) -> AppTest:
    """起一份临时合成库（AppTest 在进程内跑 app.py，故 `DB_PATH` 与 DAO 单例都指向它）+ 假分类器。"""
    path = demo.build_temp_db()
    monkeypatch.setenv("DB_PATH", str(path))

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate(verdict)
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    at = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=240)
    at.run()
    return at


def _labels(at: AppTest) -> list[str]:
    return [getattr(element, "label", "") for element in at.text_input]


def _db_path() -> Path:
    """当前进程指向的库（`demo.build_temp_db()` 已把它写进 `DB_PATH`）。"""
    import os

    return Path(os.environ["DB_PATH"])


def test_form_is_not_shown_for_read_only_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _app(monkeypatch, {"intent": "balance_query", "confidence": 0.95, "slots": {}})
    assert not at.exception
    at.chat_input[0].set_value("查一下余额").run()
    assert "姓名" not in _labels(at) and "手机号" not in _labels(at)


def test_form_appears_on_payee_add_and_submits_through_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _app(monkeypatch, {"intent": "payee_add", "confidence": 0.95, "slots": {}})
    at.chat_input[0].set_value("加个收款人").run()
    assert not at.exception
    assert "姓名" in _labels(at) and "手机号" in _labels(at)          # 弹表单，不是干等一句话

    before = count(_db_path(), "payee")
    for element in at.text_input:
        element.set_value("王小明" if element.label == "姓名" else FULL)
    submit = next(b for b in at.button if str(getattr(b, "key", "")).startswith("FormSubmitter:payee-form"))
    submit.click().run()                                              # 表单的提交按钮（按 key 找，别按位置）
    assert not at.exception
    assert count(_db_path(), "payee") == before + 1                    # 真的落库了
    row = raw(_db_path(), "SELECT * FROM payee WHERE name = ?", ("王小明",))[0]
    assert row["phone"] == MASKED and FULL not in str(dict(row))
    replies = [element.value for element in at.markdown]
    assert any("现在可以给他转账了" in (text or "") for text in replies)
    assert all(FULL not in (text or "") for text in replies)           # 完整号绝不回显
    assert "姓名" not in _labels(at)                                   # 提交成功后表单收起（避免重复提交）


def test_form_stays_when_the_submit_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """提交失败（未被接受）时表单保留，用户可以改完再提交。"""
    at = _app(monkeypatch, {"intent": "payee_add", "confidence": 0.95, "slots": {}})
    at.chat_input[0].set_value("加个收款人").run()
    before = count(_db_path(), "payee")
    for element in at.text_input:
        element.set_value("王小明" if element.label == "姓名" else "1381234567")     # 10 位 → 非法
    submit = next(b for b in at.button if str(getattr(b, "key", "")).startswith("FormSubmitter:payee-form"))
    submit.click().run()
    assert not at.exception and count(_db_path(), "payee") == before   # 没写库
    assert "姓名" in _labels(at) and "手机号" in _labels(at)             # 表单还在，可重试
