"""闲聊也要往业务上靠（用户 2026-10-31）。

原来「你好」→「「闲聊」这个功能还没接通，本版本暂时只支持查询类操作。」—— 冷冰冰、没引导。
现在：一句热络（或客气婉拒）+ 一句**业务入口**，且**不含任何业务数字**（守铁律 2）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent import smalltalk

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


@pytest.mark.parametrize("text", ["你好", "在吗", "hi", "早上好", "谢谢", "你是谁", "再见"])
def test_greetings_are_warm_and_point_at_business(text: str) -> None:
    reply = smalltalk.reply_for(text)
    assert "还没接通" not in reply
    # 一句回应 + 一句业务引导
    assert "\n\n" in reply
    assert any(word in reply for word in ("余额", "账单", "转账", "群收款", "收款人")), reply


def test_unrelated_topics_are_declined_politely_with_a_nudge() -> None:
    reply = smalltalk.reply_for("帮我订一张去上海的机票", intent="out_of_scope")
    assert "帮不上" in reply
    assert "银行业务" in reply


def test_no_business_numbers_are_invented() -> None:
    """铁律 2：闲聊回复里不能出现任何金额/余额数字（只有业务名词）。"""
    for text in ("你好", "谢谢", "随便聊聊", "今天天气不错"):
        reply = smalltalk.reply_for(text)
        assert not any(ch.isdigit() for ch in reply), reply


def test_smalltalk_turn_is_success_and_points_to_business(seeded, monkeypatch) -> None:
    """端到端：走编排层时也是「热络 + 业务入口」，且 result 正常（不再是 rejected）。"""
    import os
    from agent import classifier, llm, orchestrator

    monkeypatch.setenv("DB_PATH", str(demo.build_temp_db()))

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "smalltalk", "confidence": 0.9,
                                          "slots": {}, "missing_slots": []})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    turn = orchestrator.handle("你好", session_id="smalltalk-e2e")
    assert turn.intent == "smalltalk"
    assert "还没接通" not in (turn.reply or "")
    assert "转账" in turn.reply or "余额" in turn.reply
