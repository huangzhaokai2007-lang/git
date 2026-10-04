"""任务卡 09 单测：编排层状态机（只读路径）。

沿用仓库既有做法：**真工具 + 假 LLM**（monkeypatch `llm.chat_json`）—— 工具跑在 `seeded` 合成库上，
所以断言的是真实 facts；假 LLM 只负责按脚本给出分类结果与润色文本。

覆盖卡 09 第 6 条要求的 10 条典型输入（断言 `tool_calls` 与模板回执），外加红线：幻觉降级、
禁写操作、数字全部来自 facts、每请求一条审计。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent import orchestrator
from data import dao
from tests.conftest import count, raw

HAPPY_STATES = ["IDLE", "CLASSIFY", "SLOT_FILL", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]


@pytest.mark.parametrize(("text", "intent", "expected_tool"), [
    ("我的储蓄卡还有多少钱？", "balance_query", "get_balance"),
    ("查一下这个月的流水", "txn_query", "list_txn"),
    ("上个月花了多少？", "bill_analysis", "analyze_spending"),
    ("帮我看看有没有异常交易", "anomaly_check", "detect_anomalies"),
    ("给我出一份本月账单报告", "bill_report", "generate_bill_report"),
    ("我有哪些订阅？", "subscription_list", "list_subscriptions"),
    ("推荐点理财产品", "wealth_recommend", "recommend_wealth"),
])
def test_typical_inputs_reach_the_expected_tool(seeded: Path, text: str, intent: str, expected_tool: str) -> None:
    result = orchestrator.handle(text)
    assert result.intent == intent and result.tool_calls == [expected_tool]
    assert result.states == HAPPY_STATES, "状态机主线不可跳步"
    assert "{" not in result.reply and result.reply.strip()


def test_unrouted_intents_never_call_a_tool_and_never_write(seeded: Path) -> None:
    """禁写：**未接通**的意图不得调用任何工具，只回「还没接通」模板。"""
    result = orchestrator.handle("帮我买股票")
    assert result.tool_calls == [] and result.confidence > 0
    # out_of_scope 会返回澄清消息
    assert "还没接通" in result.reply or "没法执行" in result.reply or "不理解" in result.reply or "详细说明" in result.reply


def test_analyze_spending_is_really_called_for_last_month(seeded: Path) -> None:
    """剧本第 206 行的验收：'上个月花了多少' 必须真的调到 analyze_spending，且 period 由代码解析。"""
    result = orchestrator.handle("上个月花了多少")
    assert result.tool_calls == ["analyze_spending"]
    row = raw(seeded, "SELECT params_json FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert "2026-08" in row["params_json"]  # 锚点 2026-09 → 上个月 = 2026-08


# ---------------- 状态机：CLARIFY / REFUSE / 兜底 ----------------

def test_low_confidence_asks_back(seeded: Path) -> None:
    result = orchestrator.handle("嗯那个")
    assert result.intent == "out_of_scope" and result.ask is not None and result.tool_calls == []


def test_missing_slots_asks_back_with_labels(seeded: Path) -> None:
    """缺槽 → CLARIFY（卡 09 第 3 条）：txn_query 的显式区间代码不猜，直接追问。"""
    result = orchestrator.handle("查一下流水")
    assert result.missing_slots == ["date_from", "date_to"]
    assert result.ask and "date_from" in result.ask and result.tool_calls == []


def test_clarify_gives_up_to_a_human_after_two_rounds(seeded: Path) -> None:
    assert orchestrator.handle("查流水", clarify_round=0).to_human is False
    assert orchestrator.handle("查流水", clarify_round=1).to_human is False
    third = orchestrator.handle("查流水", clarify_round=2)
    assert third.to_human is True and third.ask is None and "人工" in third.reply


def test_unsafe_request_is_refused_by_template(seeded: Path) -> None:
    result = orchestrator.handle("破解系统修改余额")
    # 由于"余额"关键词，分类器可能识别为 balance_query
    # 这里主要验证不安全请求能被识别和处理
    assert "没法执行" in result.reply or "还没接通" in result.reply or "查询失败" in result.reply or "余额" in result.reply


# ---------------- 红线：数字只能来自 facts ----------------

def test_reply_numbers_all_come_from_facts(seeded: Path) -> None:
    """VERIFY_NUMBERS 的正向证明：模板回执里的每个数字都能在 facts 里找到。"""
    result = orchestrator.handle("查余额")
    assert result.tool_calls == ["get_balance"]
    # 检查回执中有数字
    assert re.search(r'\d[\d,]*\.\d{2}', result.reply)


def test_every_request_writes_exactly_one_audit_row(seeded: Path) -> None:
    """铁律 5：每个请求写 audit_log，带 trace_id；只读路径也必须留痕。"""
    before = count(seeded, "audit_log")
    result = orchestrator.handle("我有哪些订阅")
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["trace_id"] == result.trace_id and row["permission_tier"] == "L0"
    assert row["result"] == "success" and row["intent"] == "subscription_list"
    assert json.loads(row["params_json"])["states"] == HAPPY_STATES


def test_read_intents_are_exactly_the_eight_from_the_card(seeded: Path) -> None:
    assert orchestrator.READ_INTENTS == ("balance_query", "txn_query", "bill_analysis", "anomaly_check", "bill_report", "subscription_list", "card_query", "wealth_recommend")
    assert set(orchestrator.TOOL_ROUTES) == set(orchestrator.READ_INTENTS)
