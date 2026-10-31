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
    rows = [button.label for button in app.button if button.label.startswith("**李**")
            or "**" in button.label]
    row_buttons = [button for button in app.button if "****" in button.label]   # 每行一个按钮
    assert len(row_buttons) == 5, f"应有 5 个整行按钮，实际 {len(row_buttons)}"
    labels = "\n".join(button.label for button in row_buttons)
    assert "139****1001" in labels and "招商银行" in labels and "李四" in labels


def test_clicking_the_row_expands_the_detail_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """点 ▸ → 展开详情（名字色条 + 编辑资料 + 操作按钮）；再点 ▾ → 收起。"""
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的收款人有哪些").run()
    next(button for button in app.button if "****" in button.label).click().run()
    assert not app.exception, app.exception
    marks = "\n".join(str(element.value)
                      for element in app.markdown if "<style>" not in str(element.value))
    assert 'class="dsh-head' in marks                           # 详情卡头（色条）
    labels = [box.label for box in app.text_input]
    assert any("备注" in label for label in labels)              # 编辑资料在名字之下、已展开
    action_labels = [button.label for button in app.button]
    for expect in ("转账", "拉黑", "删除"):
        assert expect in action_labels

    next(button for button in app.button if "****" in button.label).click().run()
    labels = [box.label for box in app.text_input]
    assert not any("备注" in label for label in labels), "收起后编辑资料应消失"
    assert len([button for button in app.button if "****" in button.label]) == 5


def test_list_styles_and_expand_animation_exist() -> None:
    """列表与展开动画的 CSS 必须在（含「减少动效」时会被统一关掉）。"""
    css = _premium_css().CSS
    assert ".dsh-payee-row" in css and ".dsh-payee-avatar" in css
    assert "@keyframes dsh-expand" in css and ".st-key-payee-detail" in css
    assert 'class*="st-key-payee-row-"' in css                    # 行 hover
    assert "prefers-reduced-motion" in css


def test_editor_and_verification_stay_beside_selected_payee(monkeypatch):
    from agent import confirm_card
    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的收款人有哪些").run()
    next(b for b in app.button if "139****1001" in b.label).click().run()
    assert not any(b.proto.id.endswith("-payee-verification") for b in app.get("flex_container"))
    next(t for t in app.text_input if "新的专属额度" in t.label).set_value("2000").run()
    next(b for b in app.button if b.label == "保存白名单信息（需验证码）").click().run()
    assert not app.exception
    panel = next(b for b in app.get("flex_container") if b.proto.id.endswith("-payee-verification"))
    assert len([b for b in panel.button if b.key == "confirm-ok"]) == 1
    assert len([b for b in app.button if b.key == "confirm-ok"]) == 1
    next(b for b in app.button if b.key == "confirm-ok").click().run()
    assert not app.exception
    assert app.session_state['workspace-result']['scene'] == 'payee_list'
    panel = next(b for b in app.get("flex_container") if b.proto.id.endswith("-payee-verification"))
    assert any(t.key == "otp-code" for t in panel.text_input)
    assert any("139****1001" in b.label for b in app.button)
    app.text_input(key="otp-code").set_value("000000")
    next(b for b in app.button if b.key == "otp-submit").click().run()
    assert not app.exception
    assert confirm_card.current(app.session_state['session_id']).stage == 'otp'
    assert app.session_state['workspace-result']['scene'] == 'payee_list'
    next(b for b in app.button if b.key == "otp-cancel").click().run()
    assert not app.exception
    assert confirm_card.current(app.session_state['session_id']) is None
    assert app.session_state['workspace-result']['scene'] == 'payee_list'

    import os
    from tests.conftest import raw
    next(b for b in app.button if b.label == "保存白名单信息（需验证码）").click().run()
    next(b for b in app.button if b.key == "confirm-ok").click().run()
    before = raw(os.environ['DB_PATH'], "SELECT whitelist_quota FROM payee WHERE name = '李四'")[0]['whitelist_quota']
    assert before != 200000
    app.text_input(key="otp-code").set_value("123456")
    next(b for b in app.button if b.key == "otp-submit").click().run()
    assert not app.exception
    assert raw(os.environ['DB_PATH'], "SELECT whitelist_quota FROM payee WHERE name = '李四'")[0]['whitelist_quota'] == 200000
    assert app.session_state['workspace-result']['scene'] == 'payee_list'
    assert confirm_card.current(app.session_state['session_id']) is None
    assert any(b.proto.id.endswith('-payee-verification') for b in app.get('flex_container'))


def test_transfer_amount_opens_beside_payee_without_generic_slot_card(monkeypatch):
    """收款人卡内转账：金额就在卡里填，**不弹通用填表卡**；选完收款卡才出确认卡。

    2026-10-31 交互升级：卡内「核对转账」之后插入了一步「选择收款人的银行卡」，
    因此不再断言"一步到确认卡"，改为断言这条链路的三个关键点。
    """
    from agent import pending_slots

    app = _app(monkeypatch)
    app.chat_input[0].set_value("我的收款人有哪些").run()
    next(b for b in app.button if "139****1001" in b.label).click().run()
    next(b for b in app.button if b.label == "转账").click().run()
    assert not app.exception
    panel = next(b for b in app.get('flex_container') if b.proto.id.endswith('-payee-verification'))
    assert any(t.label == '转账金额（元）' for t in panel.text_input), "卡内应有金额输入"
    assert not any(f.key == 'slot-form' for f in app.get('form')), "不该弹通用填表卡"
    assert pending_slots.peek(app.session_state['session_id']) is None

    next(t for t in app.text_input if t.label == '转账金额（元）').set_value('100')
    next(b for b in app.button if b.label == '核对转账').click().run()
    cards = [b for b in app.button if "6222" in b.label]
    assert cards, "应出「选择收款人的银行卡」卡面"
    cards[0].click().run()
    assert not app.exception
    assert [b for b in app.button if b.key == 'confirm-ok'], "选完卡应出确认卡"
