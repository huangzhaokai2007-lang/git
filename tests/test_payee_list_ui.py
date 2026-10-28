"""收款人列表 v3（列表 + 行内展开）的回归守卫。

用户 2026-10-28：
- 「像图一的这种列表，保存」→ 一长条列表（头像 + 姓名 + 脱敏手机 + 银行 + 类型徽章）；
- 「点击某个人的选项后会拉开一条展示详细信息」→ 行尾 ▸ 在**该行下方**展开，带动画；
- 「编辑资料的选项放在名字之下，并放大一点」→ 详情第一块就是大号「编辑资料」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _premium_css():
    """按文件加载（**不要**把 interfaces/web 加进 sys.path —— 会遮蔽 `app/` 包）。"""
    spec = importlib.util.spec_from_file_location("premium_css", REPO / "interfaces/web/premium_css.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_list_renders_one_row_per_person(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个收款人一行（头像/姓名/脱敏号/银行），行尾都有 ▸。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的收款人有哪些").run()
    assert not app.exception, app.exception
    rows = [str(element.value) for element in app.markdown
            if '<div class="dsh-payee-row"' in str(element.value)]
    assert len(rows) == 5, f"应有 5 行，实际 {len(rows)}"
    assert "139****1001" in "\n".join(rows) and "招商银行" in "\n".join(rows)
    assert len([button for button in app.button if button.label == "▸"]) == 5


def test_clicking_the_row_expands_the_detail_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """点 ▸ → 展开详情（名字色条 + 编辑资料 + 操作按钮）；再点 ▾ → 收起。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的收款人有哪些").run()
    next(button for button in app.button if button.label == "▸").click().run()
    assert not app.exception, app.exception
    marks = "\n".join(str(element.value)
                      for element in app.markdown if "<style>" not in str(element.value))
    assert 'class="dsh-head' in marks                           # 详情卡头（色条）
    labels = [box.label for box in app.text_input]
    assert any("备注" in label for label in labels)              # 编辑资料在名字之下、已展开
    action_labels = [button.label for button in app.button]
    for expect in ("转账", "拉黑", "删除"):
        assert expect in action_labels

    next(button for button in app.button if button.label == "▾").click().run()
    labels = [box.label for box in app.text_input]
    assert not any("备注" in label for label in labels), "收起后编辑资料应消失"
    assert len([button for button in app.button if button.label == "▸"]) == 5


def test_list_styles_and_expand_animation_exist() -> None:
    """列表与展开动画的 CSS 必须在（含「减少动效」时会被统一关掉）。"""
    css = _premium_css().CSS
    assert ".dsh-payee-row" in css and ".dsh-payee-avatar" in css
    assert "@keyframes dsh-expand" in css and ".st-key-payee-detail" in css
    assert 'class*="st-key-payee-row-"' in css                    # 行 hover
    assert "prefers-reduced-motion" in css
