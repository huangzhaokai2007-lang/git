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
    assert any(button.label.startswith("修改参与人") for button in app.button)
    next(button for button in app.button if button.label.startswith("修改参与人")).click().run()
    #: 2026-10-29：「选择参与人」→「修改参与人」；没挑人时「添加所选」不可按
    add = next(button for button in app.button if button.label == "添加所选")
    assert add.disabled is True, "没挑人时「添加所选」必须不可按"
    captions = "\n".join(str(element.value) for element in app.caption)
    assert "还没有参与人" in captions


def test_picking_people_shows_the_split_and_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """选人 + 确认后：显示「含我」的分摊摘要，并出现「按人设置金额」与「发起群收款」。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("发起群收款").run()
    app.number_input[0].set_value(300.0)
    next(button for button in app.button if button.label.startswith("修改参与人")).click().run()
    app.multiselect[0].set_value(["王五"]).run()          # 先 rerun，添加按钮才会解禁
    next(button for button in app.button if button.label == "添加所选").click().run()
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


def test_confirm_button_actually_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户实测：「点击确认后直接卡 bug」—— 确认按钮必须真的走完执行，且不抛异常。

    根因：卡面里调了**不存在**的 `write_flow.confirm`（AttributeError）。
    """
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五"], "amount": "300"},
                                          "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("我和王五群收款 300 元").run()
    assert not app.exception, app.exception

    button = next(b for b in app.button if b.label == "✅ 确认")
    button.click().run()
    assert not app.exception, app.exception
    captions = "\n".join(str(element.value) for element in app.caption)
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "已发起" in captions or "已发起" in marks or "待收" in marks


def test_ambiguous_name_does_not_show_empty_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """同名多命中 → 上方已出解析卡，群收款卡面**不得**再画「还没有确认参与人」。

    用户实测「这又是什么」：两张卡叠着 —— 一张让选人、一张说还没选人。
    """
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["李四"], "amount": "100"},
                                          "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("向李四群收款 100 元").run()
    assert not app.exception, app.exception
    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "同名收款人，请选择" in marks          # 解析卡在
    assert "还没有确认参与人" not in marks         # 群收款卡面不再抢戏


def test_new_person_adder_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """「参与人数」展开后有「新增收款人」入口（姓名 + 手机号）。"""
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            #: 缺金额 → 停在"发起态"（参与人数/新增收款人都在这）；给全了会直接进确认态
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五"]},
                                          "missing_slots": ["amount"]})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("向王五群收款").run()
    assert not app.exception, app.exception
    next(b for b in app.button if b.label.startswith("修改参与人")).click().run()
    assert not app.exception, app.exception
    expanders = [str(element.label) for element in app.expander]
    assert any("新增收款人" in label for label in expanders)
    labels = [box.label for box in app.text_input]
    assert "姓名" in labels and "手机号" in labels


def test_remove_participant(monkeypatch: pytest.MonkeyPatch) -> None:
    """「修改参与人」里可以对已选的人点「删除」，删掉后回到空状态。"""
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            # 缺金额 -> 停在发起态（参与人编辑器在这一屏）
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五"]},
                                          "missing_slots": ["amount"]})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("我和王五群收款").run()
    assert not app.exception, app.exception
    next(b for b in app.button if b.label.startswith("修改参与人")).click().run()
    assert not app.exception, app.exception
    delete = next(b for b in app.button if b.label == "删除")
    delete.click().run()
    assert not app.exception, app.exception
    captions = "\n".join(str(element.value) for element in app.caption)
    assert "还没有参与人" in captions, "删除后应回到空状态"


def test_create_form_is_collapsed_behind_a_button(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户 2026-10-29：查看群收款时别直接铺开表单 —— 先给「＋ 新增群收款」按钮，点了再展开。"""
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_list", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())
    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("查看我的群收款").run()
    assert not app.exception, app.exception

    marks = "\n".join(str(element.value) for element in app.markdown)
    assert "群收款总金额" not in marks, "查看列表时不该直接铺开发起表单"
    assert any(button.label == "＋ 新增群收款" for button in app.button)

    next(button for button in app.button if button.label == "＋ 新增群收款").click().run()
    assert not app.exception, app.exception
    assert any(box.label == "群收款总金额（元）" for box in app.number_input), "点了按钮才展开表单"
