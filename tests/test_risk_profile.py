"""快捷按钮「风险程度」：查当前等级；没测评就给入口，不弹问卷。

口径：
- 查询**不算分**（不是 `assess_risk`），只读进程内已有的测评结果；
- 未测评不是错误 —— 如实说「还没有」，界面给「开始风险测评」按钮；
- 已测评 → 回执带等级中文名与有效期，按钮不再出现。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from agent import classifier, orchestrator
from tools import wealth

from tests.test_tools_wealth_risk import AGGRESSIVE


def test_button_text_routes_deterministically() -> None:
    """固定文案直接定意图（带 action=query），不依赖模型 —— 模型挂了按钮也要有反应。"""
    out = classifier.classify("我的风险等级是多少")
    assert out.intent == "risk_assess" and out.slots.get("action") == "query"


def test_unassessed_query_says_so_without_scoring(seeded: Path) -> None:
    turn = orchestrator.handle("我的风险等级是多少")
    assert turn.intent == "risk_assess" and turn.error_code is None
    assert turn.facts["assessed"] is False
    assert "还没有" in turn.reply
    assert turn.executed is False                      # 只读查询，没跑计分


def test_assessed_query_reports_level_and_expiry(seeded: Path) -> None:
    wealth.assess_risk(AGGRESSIVE)
    turn = orchestrator.handle("我的风险等级是多少")
    assert turn.facts["assessed"] is True
    assert turn.facts["risk_level"] == "R5"
    assert "高风险" in turn.reply
    assert turn.facts["valid_until"] in turn.reply     # 数字/日期都能在 facts 里找到


def test_query_does_not_trigger_the_questionnaire(seeded: Path) -> None:
    """查等级不该像「做测评」那样回引导语（那是弹问卷的信号）。"""
    from agent import risk_flow

    turn = orchestrator.handle("我的风险等级是多少")
    assert turn.reply != risk_flow.PROMPT


def test_card_offers_the_form_only_when_unassessed() -> None:
    def render(facts):
        turn = {"trace_id": "risk-t", "intent": "risk_assess", "error_code": None,
                "facts": facts, "states": ["REPLY"], "tier": "L0"}

        def page(turn, actions):
            from interfaces.web import components, risk_ui
            components.transcript([{"role": "assistant", "text": "查等级", "turn": turn}],
                                  risk_card=lambda shown, key: risk_ui.render_turn(
                                      shown, ask=actions.append, key_prefix=key))

        actions: list[str] = []
        at = AppTest.from_function(page, args=(turn, actions), default_timeout=30).run()
        assert not at.exception
        return at, actions

    at, actions = render({"assessed": False})
    next(b for b in at.button if b.label == "开始风险测评").click().run()
    assert actions == ["做风险测评"]

    at, _ = render({"assessed": True, "risk_level": "R5", "risk_label": "高风险",
                    "valid_until": "2027-09-12"})
    assert not any(b.label == "开始风险测评" for b in at.button)
