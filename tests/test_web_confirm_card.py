"""网页端写路径卡片渲染测试（回归守卫）：`components.confirm_card` / `otp_card`。

**为什么单独一个文件**：此前**没有任何用例渲染过这两张卡** —— `test_web_layering.py` 只测
`parse_bill` / `_yuan` 与分层，`test_web_payee_form.py` 只测收款人表单。于是下面这个真实回归
在 1200+ 条用例全绿的情况下溜了过去：

    莫的 card-24 泛化写路径时，把 `agent/confirm_card.Confirmation.payee_name` 改名成通用的
    `target_name`（转账=收款人、订阅取消=商户），`write_flow.py` 跟着改了，但
    `interfaces/web/components.py` **漏改** → 网页端确认卡与 OTP 卡一渲染就

        AttributeError: 'Confirmation' object has no attribute 'payee_name'

    表现：侧边栏点「转账」当场报错，整条写路径在界面上不可用。

本文件用真实 `AppTest` 跑 `interfaces/web/app.py`，把这两条渲染路径钉住。

**必须钉假时钟**（`clock` fixture）：转账档位是**纯代码**按规格 §5 算的，其中含 `night`
因子（`23:00–06:00` 升一档）。本例的「张小美」（非白名单）**白天是 L2（出确认卡 + OTP），
深夜会被顶到 L3（转待复核，卡上没有「确认」按钮）** —— 不钉时钟，这条用例只在白天绿、
夜间必挂（`StopIteration`）。`_app()` 因此显式接收 `clock`，把时间钉在 conftest 的
`2026-09-12 12:00`（白天、无凌晨因子）。
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


def _is_pick(widget) -> bool:
    """选卡按钮：新版是「选择这张卡」，收款人卡列表里的是「银行名 + 6222 掩码」。"""
    label = str(getattr(widget, "label", ""))
    return "6222" in label or "选择这张卡" in label


def _pick_card_if_asked(app) -> None:
    """2026-10-31：转账/定时转账现在先出「选择收款人的银行卡」卡面 —— 点第一张再继续。"""
    cards = [button for button in app.button if _is_pick(button)]
    if cards:
        cards[0].click().run()


def _pick_first_card(app) -> None:
    """2026-10-31：转账必须选收款卡 —— 点卡面上第一张银行卡再继续。"""
    candidates = [button for button in app.button if _is_pick(button)]
    if candidates:
        candidates[0].click().run()

MERCHANT = "云音乐"          # sub_0001（data/seed.py）：active、15.00 元／月、唯一命中


def _app(monkeypatch: pytest.MonkeyPatch, verdict: dict, clock: object) -> AppTest:
    """起一份临时合成库（AppTest 在进程内跑 app.py，故 `DB_PATH` 指向它）+ 假分类器。

    `clock` 不是这里用的，而是**依赖它把 `transfer._now` 钉在白天**：AppTest 进程内执行
    `app.py`，会看到 conftest 已 patch 的同一个 `tools.transfer` 模块对象。不传它则测试读
    真实系统时间，深夜的 `night` 因子会把档位顶高一档（见模块 docstring）。
    """
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


def _say(at: AppTest, text: str) -> AppTest:
    """说一句话并断言这一轮没抛异常（回归判据就在这个断言上）。"""
    at.chat_input[0].set_value(text).run()
    assert not at.exception, at.exception
    return at


def _markdown(at: AppTest) -> str:
    return "\n".join(str(element.value) for element in at.markdown)


def _caption(at: AppTest) -> str:
    return "\n".join(str(element.value) for element in at.caption)


def _metric_labels(at: AppTest) -> list[str]:
    """页面上的 `st.metric` 标签。确认卡那 4 格是页面上唯一的 metric（侧边栏只用 caption/button）。"""
    return [str(element.label) for element in at.metric]


def test_transfer_confirm_card_renders(monkeypatch: pytest.MonkeyPatch, clock: object) -> None:
    """回归（用户实测的崩溃点）：转账走到确认卡必须能渲染。

    2026-10-31 新交互：先出「选择收款人的银行卡」卡面 → 点一张卡后才出确认卡。
    """
    at = _say(_app(monkeypatch, {"intent": "transfer_single", "confidence": 0.95,
                                 "slots": {"payee": "王五", "amount": "100.00"}}, clock),
              "给王五转 100 元")
    cards = [button for button in at.button if _is_pick(button)]
    assert cards, "应先出选择银行卡卡面"
    cards[0].click().run()
    assert "转账确认卡" in _markdown(at)
    assert _metric_labels(at) == ["金额（元）", "收款人", "权限档", "验证码"]
    assert "王五" in _markdown(at) or "王五" in _caption(at)


def test_transfer_otp_card_renders(monkeypatch: pytest.MonkeyPatch, clock: object) -> None:
    """同族的第二个崩溃点：点「确认」进入 OTP 阶段，`otp_card` 也读了 `payee_name`。

    「张小美」非白名单 → 基础档 L2；**白天无升档因子**，正是本例要的「L2 + OTP」。
    （深夜的 `night` 因子会把它顶到 L3，卡上就没有「确认」按钮了 —— 故必须钉 `clock`。）
    """
    at = _say(_app(monkeypatch, {"intent": "transfer_single", "confidence": 0.95,
                                 "slots": {"payee": "张小美", "amount": "100.00"}}, clock),   # 非白名单 → L2
              "给张小美转 100 元")
    #: 2026-10-31：先选收款卡，再点确认
    cards = [button for button in at.button if _is_pick(button)]
    assert cards, "应先出选择银行卡卡面"
    cards[0].click().run()
    confirm = next(b for b in at.button if str(getattr(b, "key", "")) == "confirm-ok")
    confirm.click().run()
    assert not at.exception, at.exception
    assert any(str(element.label) == "验证码" for element in at.text_input)
    assert "张小美" in _caption(at)                                   # OTP 卡也要正确显示主对象


def test_subscription_cancel_card_uses_the_generic_label(monkeypatch: pytest.MonkeyPatch,
                                                         clock: object) -> None:
    """card-24 泛化的正面用例：同一张卡对订阅取消要显示「商户」，不能写死「收款人」。"""
    at = _say(_app(monkeypatch, {"intent": "subscription_cancel", "confidence": 0.95,
                                 "slots": {"merchant": MERCHANT}}, clock),
              f"取消{MERCHANT}订阅")
    assert "订阅取消确认卡" in _markdown(at)
    labels = _metric_labels(at)
    assert "商户" in labels and "收款人" not in labels
    assert "收款账号" not in _caption(at)                             # 订阅取消没有手机号，那一截不印
