"""卡 17b 单测：相对时间槽位（`period` / 日期）的**取值归一化** —— 真机实测的"静默答错月份"。

背景（卡 17 现场实测）：分类器即使温度 0 也会返回英文 `period="last_month"`；旧的
`resolve_period` 只认中文相对词，认不出就**回落锚点当月** → 「上个月花了多少」被答成
「2026-09 一共支出 0.00 元」：**静默、偶发**，而且数字来自真实事实包，看着完全像对的。

本卡口径（实现落在 `agent/period.py`，编排层 import 后再导出）：

- 中/英/下划线/连字符变体查 `agent.period.RELATIVE_PERIOD_ALIASES`（**唯一一张表**，
  `resolve_period` 与 `resolve_day` 共用，不搞两份）；
- **给了 `period` 却认不出** → 判缺失 → CLARIFY 追问（不再静默换当月）；
- **完全没给** `period` → 仍取锚点当月（卡 09 口径，未变）。

沿用仓库做法：真工具 + 假 LLM（monkeypatch `llm.chat_json`），断言跑在 `seeded` 合成库上；
期望月份/边界由本文件**独立复算**（不复用被测实现，避免自证）。
"""

from __future__ import annotations

import calendar
import json
import re
from pathlib import Path

import pytest

from agent import llm, orchestrator, period
from data.seed import AS_OF
from tools import query

from tests.conftest import raw

#: 锚点月（`data.seed.AS_OF` = 2026-09-12 → `2026-09`）
ANCHOR = f"{AS_OF.year:04d}-{AS_OF.month:02d}"


def shift(months: int) -> str:
    """独立复算锚点月的偏移（测试自己的口径，不调 `agent.period.month_shift`）。"""
    total = AS_OF.year * 12 + (AS_OF.month - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_edges(period_text: str) -> tuple[str, str]:
    """`YYYY-MM` → 该月首日/末日（独立复算）。"""
    year, month = int(period_text[:4]), int(period_text[5:])
    return f"{period_text}-01", f"{period_text}-{calendar.monthrange(year, month)[1]:02d}"


def verdict(intent: str, *, slots: dict | None = None, confidence: float = 0.9) -> dict:
    """一份假的分类结果（形状同规格 §3 的 `IntentOut`）。"""
    return {"intent": intent, "confidence": confidence, "slots": slots or {},
            "missing_slots": [], "unsafe_reason": None}


def classify_as(monkeypatch: pytest.MonkeyPatch, result: dict) -> None:
    """假 `llm.chat_json`：分类调用给指定结果；润色调用原样返回模板文本（不引入新数字）。"""

    def chat_json(system: str, user: str, schema: type) -> object:
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(result)

    monkeypatch.setattr(llm, "chat_json", chat_json)


# ---------------- ① 归一化表：中/英/下划线/连字符变体 ----------------

@pytest.mark.parametrize(("raw_value", "expected"), [
    ("上个月", shift(-1)), ("上月", shift(-1)),
    ("last_month", shift(-1)), ("Last Month", shift(-1)), ("last-month", shift(-1)),
    ("previous_month", shift(-1)), ("prev_month", shift(-1)),
    ("本月", shift(0)), ("这个月", shift(0)), ("当月", shift(0)),
    ("this_month", shift(0)), ("current_month", shift(0)),
    ("上上个月", shift(-2)), ("last_2_months", shift(-2)), ("last-2-months", shift(-2)),
    ("two_months_ago", shift(-2)),
    ("2026-08", "2026-08"), ("2026", "2026"), ("  2026-08  ", "2026-08"),
])
def test_resolve_period_accepts_aliases(raw_value: str, expected: str) -> None:
    """别名表覆盖的写法都能解析成锚点月的正确偏移（含大小写/连字符/空格变体）。"""
    assert period.resolve_period(raw_value) == expected


@pytest.mark.parametrize("unknown", ["", None, "上一季度", "上个季度", "yesterday", "下个月",
                                    "2026年8月", "Q3", "近半年", "last_year"])
def test_resolve_period_never_guesses_the_anchor_month(unknown: object) -> None:
    """认不出 → `None`（**不再是锚点当月**）—— 这一条就是 17b 要修的根。"""
    assert period.resolve_period(unknown) is None


@pytest.mark.parametrize("alias", ["last_month", "last-month", "Last Month", "上个月", "上月"])
def test_resolve_day_shares_the_same_alias_table(alias: str) -> None:
    """日期槽位（`txn_query` 的 date_from/date_to）共用同一张表，不搞第二份口径。"""
    start, end = month_edges(shift(-1))
    assert period.resolve_day(alias, edge="start") == start
    assert period.resolve_day(alias, edge="end") == end


@pytest.mark.parametrize("unknown", ["上一季度", "yesterday", "", None])
def test_resolve_day_still_refuses_to_guess(unknown: object) -> None:
    """日期槽位认不出 → `None`（本来就不猜，17b 未改这条）。"""
    assert period.resolve_day(unknown, edge="start") is None


def test_orchestrator_re_exports_the_same_implementation() -> None:
    """编排层 import 后再导出：历史调用点 `orchestrator.resolve_period` 指向**同一份**实现。"""
    assert orchestrator.resolve_period is period.resolve_period
    assert orchestrator.resolve_day is period.resolve_day
    assert orchestrator.anchor_period is period.anchor_period


# ---------------- ② 端到端：真模型会给出的 last_month ----------------

def test_last_month_from_the_model_answers_august_not_the_anchor_month(seeded: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """**17b 回归主用例**：模型给 `last_month` → 必须真答 2026-08，而不是锚点当月的 0.00 元。

    期望数字不写死：拿工具层对 2026-08 的事实包做对照（独立口径）。
    """
    classify_as(monkeypatch, verdict("bill_analysis", slots={"period": "last_month"}))
    result = orchestrator.handle("查一下上个月花了多少")
    facts = query.analyze_spending(shift(-1)).facts
    assert result.tool_calls == ["analyze_spending"]
    assert result.intent == "bill_analysis" and result.states[-1] == "AUDIT"
    assert shift(-1) in result.reply, f"应回答 2026-08：{result.reply[:80]!r}"
    assert str(facts["total_yuan"]) in result.reply, "回执数字必须来自 2026-08 的事实包"
    assert ANCHOR not in result.reply, "旧 bug 的症状：答成了锚点当月"
    audit = raw(seeded, "SELECT params_json FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert shift(-1) in audit["params_json"], "解析出的月份也要进审计（可追溯）"


def test_unknown_relative_period_asks_instead_of_answering(seeded: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """认不出的相对时间 → CLARIFY 追问：零工具调用、不拿当月数字糊过去。"""
    classify_as(monkeypatch, verdict("bill_analysis", slots={"period": "上一季度"}))
    result = orchestrator.handle("上个季度我花了多少")
    assert result.states == ["IDLE", "CLASSIFY", "SLOT_FILL", "CLARIFY", "REPLY", "AUDIT"]
    assert result.tool_calls == [] and result.missing_slots == ["period"]
    assert result.ask and "时间范围" in result.ask                  # 缺槽标签来自 templates.SLOT_CN
    assert not re.search(r"\d[\d,]*\.\d{2}", result.reply), "追问里不得出现任何金额（没跑工具就没有事实包）"


def test_missing_period_still_defaults_to_the_anchor_month(seeded: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """`period` **完全没给** → 仍是锚点当月（卡 09 口径未变：这不是回归，是保行为）。"""
    classify_as(monkeypatch, verdict("bill_analysis"))
    result = orchestrator.handle("看看我的消费")
    assert result.tool_calls == ["analyze_spending"] and ANCHOR in result.reply


def test_meta_clearing_the_alias_table_clarifies_instead_of_answering_the_anchor(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**元用例**（模拟旧实现口径）：别名表清空后 `last_month` 无人认领 → 必须追问。

    若有人把"认不出 → 回落当月"改回去，本用例会变成"答了锚点当月"→ 红。
    （打补丁要打在**实现所在模块** `agent.period` 上：打在再导出的名字上不会生效。）
    """
    monkeypatch.setattr(period, "RELATIVE_PERIOD_ALIASES", {})
    classify_as(monkeypatch, verdict("bill_analysis", slots={"period": "last_month"}))
    result = orchestrator.handle("查一下上个月花了多少")
    assert "CLARIFY" in result.states and result.tool_calls == []
    assert not re.search(r"\d[\d,]*\.\d{2}", result.reply), "不许拿当月数字糊过去（旧实现的症状）"
