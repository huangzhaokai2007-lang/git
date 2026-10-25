"""回归：**缺槽时 `Step.reply` 不能为空**（界面按钮/解析卡路径只读 reply）。

2026-10-24 实测 bug：「向王五，赵六，张小美，李四 aa 收款」→ 解析卡选完李四后
界面**什么都不显示**（还缺总金额），用户以为卡住了。根因是 `write_flow.start`
在缺槽分支只设了 `ask`、没设 `reply`。
"""

from __future__ import annotations

import pytest

from agent import write_flow


@pytest.mark.parametrize("intent,slots,expect", [
    ("aa_collect", {"payee_ids": ["王五"]}, "金额"),
    ("transfer_single", {}, "收款人"),
    ("payee_tags", {}, "收款人"),
])
def test_missing_slots_always_produce_a_reply(seeded, intent: str, slots: dict,
                                              expect: str) -> None:
    step = write_flow.start(intent, slots, f"slot-{intent}")
    assert step.missing, "这些入参本来就缺槽"
    assert step.reply, f"缺槽时 reply 不能为空（{intent}）"
    assert expect in step.reply, f"追问应提到「{expect}」，实际：{step.reply}"


def test_missing_hint_is_empty_only_when_nothing_missing() -> None:
    assert write_flow.missing_hint([]) == ""
    assert "总金额" in write_flow.missing_hint(["amount"])
    assert write_flow.missing_hint(["platform", "address"]).count("、") == 1
