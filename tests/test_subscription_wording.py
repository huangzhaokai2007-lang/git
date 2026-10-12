"""卡 20 追加单测：订阅回执的**用户可见措辞**（去行话 + 模板层与工具层逐字一致）。
F19 追加单测：回执必须带**明细列表**（商户/金额/周期/下次扣费日），且非 active 状态不得说"进行中"。

人类口径：问订阅情况时**不要**直接说"僵尸订阅"；要说清"有一个订阅仍在『进行中』，
但最近 W 个月没有任何扣费，怀疑是你忘了取消"。W 必须来自 facts（`zombie_window_months`），不许硬编码。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ---------------- F19：明细列表（验收清单第 1 条） ----------------

def test_receipt_lists_every_subscription_with_all_four_fields(seeded: Path) -> None:
    """F19 验收点：回执要展示**商户、金额、周期、下次扣费日**，一条不漏。

    修复前回执只有一句汇总，用户看不到任何一个订阅 —— 这条测试钉住"必须逐条列出"。
    """
    result = subscription.list_subscriptions("active")
    rendered = templates.render("subscription_list", result.facts)
    for item in result.facts["items"]:
        assert item["merchant"] in rendered, f"缺商户 {item['merchant']}"
        assert item["amount_yuan"] in rendered, f"缺金额 {item['merchant']}"
        assert item["next_charge_date"] in rendered, f"缺下次扣费日 {item['merchant']}"
    # 周期用中文展示（工具层 CYCLE_LABELS），不把内部枚举名丢给用户
    assert "每月" in rendered and "每年" in rendered
    assert "monthly" not in rendered and "yearly" not in rendered


def test_receipt_line_count_matches_subscription_count(seeded: Path) -> None:
    """明细行数 == 订阅数（`- ` 开头的行）—— 不多不少，防止列表被截断。"""
    result = subscription.list_subscriptions("active")
    rendered = templates.render("subscription_list", result.facts)
    lines = [line for line in rendered.splitlines() if line.startswith("- ")]
    assert len(lines) == result.facts["subscription_count"] == 4


def test_cycle_label_source_is_shared_not_copied() -> None:
    """周期文案只有一份来源：模板层直接引工具层常量，不得复制副本（漂移即红）。"""
    assert templates.CYCLE_LABELS is subscription.CYCLE_LABELS


# ---------------- F19：状态措辞（修复"已取消却说仍在进行中"） ----------------

@pytest.mark.parametrize("status,label", [("cancelled", "已取消"), ("paused", "已暂停")])
def test_non_active_status_never_claims_it_is_ongoing(seeded: Path, status: str, label: str) -> None:
    """实测缺陷：查已取消的订阅，回执却说「其中 2 个仍在『进行中』」—— 自相矛盾。

    非 active 状态的回执必须说出真实状态，且**不得**出现「进行中」与「忘了取消」的措辞。
    """
    result = subscription.list_subscriptions(status)
    rendered = templates.render("subscription_list", result.facts)
    assert rendered == result.message
    assert "进行中" not in rendered and "忘了取消" not in rendered
    if result.facts["subscription_count"]:
        assert label in rendered


def test_empty_result_has_its_own_sentence_without_a_dangling_head(seeded: Path) -> None:
    """空结果走自己的句子，不套列表头（避免「当前有 0 个订阅：」这种残句）。"""
    result = subscription.list_subscriptions("paused")
    assert result.facts["items"] == []
    rendered = templates.render("subscription_list", result.facts)
    assert rendered == result.message == subscription.EMPTY_MESSAGE
    assert not rendered.endswith("：")
