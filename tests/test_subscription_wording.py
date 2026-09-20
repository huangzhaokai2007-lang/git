"""卡 20 追加单测：订阅回执的**用户可见措辞**（去行话 + 模板层与工具层逐字一致）。

人类口径：问订阅情况时**不要**直接说"僵尸订阅"；要说清"有一个订阅仍在『进行中』，
但最近 W 个月没有任何扣费，怀疑是你忘了取消"。W 必须来自 facts（`zombie_window_months`），不许硬编码。
"""

from __future__ import annotations

from pathlib import Path

from agent import templates
from tools import subscription


def test_template_and_tool_message_are_word_for_word_identical(seeded: Path) -> None:
    """卡里的硬要求：`agent/templates.py` 与 `tools/subscription.py` 的措辞必须一致（免得两处漂移）。"""
    result = subscription.list_subscriptions("active")
    assert result.ok is True
    rendered = templates.render("subscription_list", result.facts)
    assert rendered == result.message


def test_user_facing_receipt_avoids_jargon_and_uses_facts_window(seeded: Path) -> None:
    result = subscription.list_subscriptions("active")
    assert result.facts["zombie_count"] >= 1                      # 合成数据里确实有"忘了取消"的订阅
    rendered = templates.render("subscription_list", result.facts)
    assert "僵尸" not in rendered and "僵尸" not in result.message   # 面向用户不再出现行话
    assert str(result.facts["zombie_window_months"]) in rendered    # W 来自 facts，不是硬编码
    assert "进行中" in rendered and "扣费" in rendered              # 说清"仍在进行中但没扣费"


def test_clean_branch_when_nothing_looks_forgotten(seeded: Path) -> None:
    """`zombie_count == 0` 的那一支：只报订阅数与窗口，不提"忘了取消"。"""
    facts = {**subscription.list_subscriptions("active").facts, "zombie_count": 0}
    rendered = templates.render("subscription_list", facts)
    assert "僵尸" not in rendered and "忘了取消" not in rendered
    assert str(facts["zombie_window_months"]) in rendered
    assert str(facts["subscription_count"]) in rendered
