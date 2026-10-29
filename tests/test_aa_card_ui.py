"""群收款卡面的回归守卫（2026-10-28 重做）。

用户要求：
- 卡面内有**总金额**；「选择收款人」→ 列表多选、**没选时确认按钮不可按**；
- 另一个按钮拉开后可设**按人金额**；
- 有**付款状态**卡；
- 群收款**包含我**（我和王五 300 → 王五给我 150，我承担 150）；
- 卡面**不显示**「权限档 / 短信验证码」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import aa_flow, classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
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
    return app


def test_group_plan_includes_me(seeded) -> None:
    """我和王五收 300 → 每人 150、我承担 150、待收 150；三人 300 → 每 100、待收 200。"""
    one = aa_flow.group_plan(["王五"], 300)
    assert (one["count"], one["per_yuan"], one["mine_yuan"], one["collect_yuan"]) == \
        (2, "150.00", "150.00", "150.00")
    two = aa_flow.group_plan(["王五", "赵六"], 300)
    assert (two["count"], two["per_yuan"], two["mine_yuan"], two["collect_yuan"]) == \
        (3, "100.00", "100.00", "200.00")
    assert int(round(float(two["per_yuan"]) * 2 * 100)) == two["collect_cents"]


def test_confirm_button_disabled_until_people_picked(monkeypatch: pytest.MonkeyPatch) -> None:
    """「确认」在没选人时不可按；卡面有总金额输入与「选择收款人」按钮。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("发起群收款").run()
    assert not app.exception, app.exception
    labels = [box.label for box in app.number_input]
    assert any("群收款总金额" in label for label in labels)
    assert any(button.label.startswith("参与人数") for button in app.button)
    next(button for button in app.button if button.label.startswith("参与人数")).click().run()
    confirm = next(button for button in app.button if button.label == "确认参与人")
    assert confirm.disabled is True, "没选人时「确认参与人」必须不可按"


def test_picking_people_shows_the_split_and_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """选人 + 确认后：显示「含我」的分摊摘要，并出现「按人设置金额」与「发起群收款」。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("发起群收款").run()
    app.number_input[0].set_value(300.0)
    next(button for button in app.button if button.label.startswith("参与人数")).click().run()
    app.multiselect[0].set_value(["王五"]).run()          # 先 rerun，确认按钮才会解禁
    next(button for button in app.button if button.label == "确认参与人").click().run()
    assert not app.exception, app.exception
    captions = "\n".join(str(element.value) for element in app.caption)
    assert "含我" in captions and "我承担" in captions and "待收" in captions
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "均摊金额" in marks and "¥ 150.00" in marks      # 大字均摊金额（300 / 2 份）
    labels = [button.label for button in app.button]
    assert any(label.startswith("按人设置金额") for label in labels)
    assert "发起群收款" in labels


def test_aa_card_has_no_policy_lines(seeded, monkeypatch: pytest.MonkeyPatch) -> None:
    """群收款确认卡不出现「权限档 / 短信验证码」。"""
    from agent import confirm_card

    card = confirm_card.CardInput(title="群收款确认卡", intent_cn="群收款",
                                  rows=[("总金额", "300.00 元")], tier="L1",
                                  requires_otp=False, risk_note="常规群收款")
    text = confirm_card.render(card)
    assert "权限档" not in text and "短信验证码" not in text
    assert "总金额" in text and "风险提示" in text


def test_people_parsed_from_speech_are_prefilled(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户实测：说「向李四、张小美、王五群收款」→ 追问总金额，卡面却显示"还没有参与人"。

    人已经从话里解析出来了，就该直接带进卡面（不必再点一遍）。
    """
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五", "赵六"]},
                                          "missing_slots": ["amount"]})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("向王五和赵六群收款").run()
    assert not app.exception, app.exception
    captions = "\n".join(str(element.value) for element in app.caption)
    assert "王五、赵六" in captions, "已解析的参与人没有带进卡面"
    assert "3 人（含我）" in captions                    # 2 人 + 我
