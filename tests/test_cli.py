"""卡 19b 单测：命令行入口（`app/cli.py`）—— 离线替身、渲染、入口返回值。

为什么补这份：`--offline` 是**演示与验收**的确定路径（`scripts/verify.sh` 第 4 段、`scripts/demo.py`
都依赖它），而它此前只有"跑起来看输出"这种人工证据。这里把替身规则（7 条）、渲染字段、入口行为钉住；
替身只替代 LLM 的"理解意图"这一步（铁律 1），所以断言的都是"意图/槽位"，不涉及权限与金额判定。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import cli
from agent.orchestrator import Turn

from tests.conftest import balance, money

# ---------------- ① 离线替身：规则表 ----------------


@pytest.mark.parametrize(("text", "intent"), [
    ("查一下余额", "balance_query"),
    ("我的储蓄卡还有多少钱", "balance_query"),
    ("帮我看看上个月花了多少", "bill_analysis"),
    ("给我出一份账单", "bill_analysis"),
    ("我有哪些订阅？", "subscription_list"),
    ("看看最近的流水", "txn_query"),
    ("有没有异常交易？", "anomaly_check"),
    ("推荐点理财产品", "wealth_recommend"),
    ("给王五转100元", "transfer_single"),
])
def test_offline_verdict_maps_keywords_to_intents(text: str, intent: str) -> None:
    """关键词 → 意图：9 句常用话术（覆盖除 out_of_scope 外的全部规则）。"""
    assert cli.offline_verdict(text)[0] == intent


@pytest.mark.parametrize(("text", "period"), [
    ("帮我看看上个月花了多少", "上个月"),
    ("这个月花了多少", "这个月"),
    ("看看消费", "本月"),                      # 没给时间词 → 本月（归一化仍归 agent/period.py）
])
def test_offline_verdict_fills_the_period_slot(text: str, period: str) -> None:
    """分析类意图要带 `period` 槽位（替身只"抽词"，归一化与缺省口径仍由 `agent/period.py` 决定）。"""
    assert cli.offline_verdict(text)[1] == {"period": period}


def test_offline_verdict_asks_when_nothing_matches() -> None:
    """都不命中 → `out_of_scope`（编排层按低置信度追问），**不猜**。"""
    assert cli.offline_verdict("今天天气怎么样") == ("out_of_scope", {})


def test_every_offline_rule_is_reachable() -> None:
    """自证规则表不是摆设：每条规则至少能被自己的一个关键词命中（防表里躺着手写错的死规则）。"""
    for keywords, intent, _slots in cli.OFFLINE_RULES:
        assert any(cli.offline_verdict(word)[0] == intent for word in keywords), f"规则没生效：{intent}"


# ---------------- ② 渲染与入口 ----------------


def test_render_shows_reply_and_trail() -> None:
    """终端渲染：回执 + 一行轨迹（字段与 `POST /api/chat` 同源）。"""
    turn = Turn(trace_id="trace-x", intent="balance_query", reply="您好，余额是 …。",
                tool_calls=["get_balance"], tier=None, executed=False)
    text = cli.render(turn)
    assert "您好，余额是 …。" in text
    assert "意图=balance_query" in text and "工具=get_balance" in text
    assert "权限档=-" in text and "已执行=否" in text and "trace=trace-x" in text


def test_render_flags_the_error_code() -> None:
    """有错误码时多打一行（写操作没执行 → 明示"未执行任何写操作"）。"""
    text = cli.render(Turn(trace_id="t1", intent="transfer_single", reply="没能完成",
                           tool_calls=["preview_transfer"], error_code="OVER_LIMIT"))
    assert "OVER_LIMIT" in text and "未执行任何写操作" in text


def test_main_offline_uses_the_real_tool_and_exits_zero(seeded: Path,
                                                        capsys: pytest.CaptureFixture) -> None:
    """入口端到端：`--offline "查一下余额"` → rc=0、回执数字来自**事实包**（独立复算比对）。"""
    assert cli.main(["--offline", "查一下余额"]) == 0
    out = capsys.readouterr().out
    assert "意图=balance_query" in out and "工具=get_balance" in out
    assert money(balance(seeded)) in out, "回执里的余额必须与独立复算一致（不许界面/CLI 自己算）"


def test_main_offline_write_path_needs_confirmation(seeded: Path, capsys: pytest.CaptureFixture) -> None:
    """写路径在 CLI 上同样"不确认不执行"：第一轮只出确认卡。

    **不断言具体档位**：档位由 `guard/permission.py` 按该库的收款人白名单/额度判定
    （同一个收款人在演示库与测试库可能是 L1 或 L2）—— 这里钉的是不变量：出了确认卡、没执行。
    """
    assert cli.main(["--offline", "给王五转100元", "--session", "cli-test"]) == 0
    out = capsys.readouterr().out
    assert "【转账确认卡】" in out and "preview_transfer" in out
    assert any(f"权限档={tier}" in out for tier in ("L1", "L2", "L3"))
    assert "已执行=否" in out and "execute_transfer" not in out
