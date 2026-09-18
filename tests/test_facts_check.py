"""卡 13 单测：数字校验器（规格 §7，幻觉防护核心）。

三块：
① 归一化：千分位 / 万元 / 百分比 / 块·元 / 整数分↔元 / 日期时间**不算业务数字**（卡 09 定下的语义）；
② 拦截：回执里出现 facts 里没有的数字 → `(False, [越界数字])`；
③ 端到端：让假 LLM 在润色时**改数字** → 重生成一次 → 仍改 → 降级为模板回执 + 审计记 `HALLUCINATION_BLOCKED`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import llm, orchestrator, templates
from guard import facts_check

from tests.conftest import count, raw


# ---------------- ① 归一化 ----------------

@pytest.mark.parametrize(("reply", "facts", "why"), [
    ("您的储蓄账户余额为 46,634.00 元。", {"balance_yuan": "46,634.00"}, "千分位"),
    ("您的储蓄账户余额为 46634.00 元。", {"balance_yuan": "46,634.00"}, "千分位有无都应通过"),
    ("余额 46,634.00 元。", {"balance_cents": 4_663_400}, "整数分 ↔ 元（分→元）"),
    ("支出 100 元。", {"amount": 10_000}, "分 → 元（100 元 = 10000 分）"),
    ("支出 12000 分。", {"amount_yuan": "120.00"}, "元 → 分（12000 分 = 120 元）"),
    ("支出 100块。", {"amount": 10_000}, "块 = 元"),
    ("收益 1.2万元。", {"amount": 1_200_000}, "万元"),
    ("环比 -32%。", {"vs_prev_pct": -32}, "百分比"),
    ("环比 32%。", {"vs_prev_pct": -32}, "§7 容差：绝对值相等"),
    ("占比 0.32。", {"ratio_pct": 32}, "百分数表达的另一种写法"),
    ("2026-09-12 的余额为 100.00 元。", {"balance_yuan": "100.00"}, "日期不算业务数字"),
    ("2026年9月账单：支出 300.00 元。", {"total_yuan": "300.00"}, "年月日不算业务数字"),
    ("截至 2026-09-12T12:00:00，余额 100.00 元。", {"balance_yuan": "100.00"}, "ISO 时刻不算业务数字"),
    ("共 3 笔，合计 300.00 元。", {"total_count": 3, "total_yuan": "300.00"}, "笔数在 facts 里"),
    ("没有任何数字的一句话。", {}, "回执无数字 → 通过"),
])
def test_normalization_passes(reply: str, facts: dict, why: str) -> None:
    passed, offenders = facts_check.verify_numbers(reply, facts)
    assert passed is True and offenders == [], f"{why} 未通过：{offenders}"


@pytest.mark.parametrize(("reply", "facts", "expected_token"), [
    ("您的储蓄账户余额为 99,999.00 元。", {"balance_yuan": "46,634.00"}, "99,999.00元"),
    ("共 5 笔，合计 1,234.00 元。", {"total_count": 3, "total_yuan": "300.00"}, "5"),
    ("支出 1.3万元。", {"amount": 1_200_000}, "1.3万元"),
    ("环比 -42%。", {"vs_prev_pct": -32}, "-42%"),
    ("余额 100 元，可用 200 元。", {"balance_yuan": "100.00"}, "200元"),      # 一个对一个错 → 仍拦
])
def test_fabricated_numbers_are_blocked(reply: str, facts: dict, expected_token: str) -> None:
    passed, offenders = facts_check.verify_numbers(reply, facts)
    assert passed is False and expected_token in offenders, f"越界数字应为 {expected_token!r}，实际 {offenders}"


def test_nested_facts_are_searched() -> None:
    """facts 里的数字可能嵌在 list/dict（如 groups[]、items[]）里，必须递归找到。"""
    facts = {"groups": [{"category": "餐饮", "total_yuan": "1,234.56"}], "total_count": 12}
    assert facts_check.verify_numbers("餐饮合计 1,234.56 元，共 12 笔。", facts)[0] is True
    assert facts_check.verify_numbers("餐饮合计 9,999.00 元。", facts)[1] == ["9,999.00元"]


def test_signature_and_error_code_contract() -> None:
    """规格 §7 的冻结签名是 `(bool, list[str])`；模板层的适配版本返回集合，两者必须一致。"""
    facts = {"balance_yuan": "100.00"}
    assert facts_check.verify_numbers("余额 999.00 元。", facts) == (False, ["999.00元"])
    assert templates.verify_numbers("余额 100.00 元。", facts) == set()
    assert templates.verify_numbers("余额 999.00 元。", facts) == {"999.00元"}
    assert facts_check.ERROR_CODE == "HALLUCINATION_BLOCKED"
    source = Path(orchestrator.__file__).read_text(encoding="utf-8")
    assert facts_check.ERROR_CODE in source          # 编排层实际写进 audit 的就是这个码


def test_no_dead_helpers_left_in_templates() -> None:
    """最小版判据（`_digits`/`facts_digits`）必须已被 §7 实现取代，不留两套口径。"""
    source = Path(templates.__file__).read_text(encoding="utf-8")
    assert "_digits(" not in source and "facts_digits" not in source


# ---------------- ③ 端到端：改数字 → 重生成一次 → 降级 + 审计 ----------------

def _lying_llm(monkeypatch: pytest.MonkeyPatch, *, lie: str = "9,999,999.00") -> dict[str, int]:
    """假 LLM：分类照常；润色时**把金额改成 facts 里没有的数字**。统计调用次数。"""
    calls = {"classify": 0, "polish": 0}

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            calls["polish"] += 1
            text = json.loads(user)["reply"]
            return schema(reply=text.replace("46,634.00", lie))
        calls["classify"] += 1
        return schema.model_validate({"intent": "balance_query", "confidence": 0.95, "slots": {}})

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return calls


def _honest_llm(monkeypatch: pytest.MonkeyPatch, prefix: str = "您好，") -> dict[str, int]:
    calls = {"classify": 0, "polish": 0}

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            calls["polish"] += 1
            return schema(reply=prefix + json.loads(user)["reply"])
        calls["classify"] += 1
        return schema.model_validate({"intent": "balance_query", "confidence": 0.95, "slots": {}})

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return calls


def test_hallucinated_reply_is_regenerated_then_degraded_and_audited(seeded: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """红线：改数字 → 重生成一次（共 2 次润色调用）→ 仍不过 → 回退模板回执 + 审计记 HALLUCINATION_BLOCKED。"""
    before_audit = count(seeded, "audit_log")
    calls = _lying_llm(monkeypatch)
    turn = orchestrator.handle("我的储蓄卡还有多少钱", session_id="facts-13")
    assert calls["classify"] == 1 and calls["polish"] == 2           # 只重生成一次
    assert turn.degraded is True and turn.error_code == facts_check.ERROR_CODE
    assert "9,999,999" not in turn.reply and "46,634.00" in turn.reply
    assert turn.reply.startswith("您的储蓄账户余额为")                  # 模板回执（不是模型的措辞）
    row = raw(seeded, "SELECT error_code, result, tool FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert count(seeded, "audit_log") == before_audit + 1 and row["error_code"] == facts_check.ERROR_CODE


def test_honest_reply_is_kept(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不改数字 → 采纳润色结果（不降级、不重生成）。"""
    calls = _honest_llm(monkeypatch)
    turn = orchestrator.handle("我的储蓄卡还有多少钱", session_id="facts-13-ok")
    assert calls["polish"] == 1 and turn.degraded is False and turn.error_code is None
    assert turn.reply.startswith("您好，您的储蓄账户余额为")


def test_write_path_uses_the_same_verifier(seeded: Path, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    """写路径回执走同一套判据：转账执行成功后润色改数字 → 同样降级 + 记码。"""
    from agent import confirm_card
    from tools import transfer

    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply="已转账 88,888.00 元。")                   # 改数字 → 重生成后仍改 → 降级
        return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                      "slots": {"payee": "张小美", "amount": "100.00"}})

    monkeypatch.setattr(llm, "chat_json", chat_json)
    session = "facts-13-write"
    assert orchestrator.handle("给张小美转 100 元", session_id=session).executed is False   # 确认卡
    assert orchestrator.handle("确认", session_id=session).executed is False               # L2 → 等 OTP
    done = orchestrator.handle(transfer.OTP_CODE, session_id=session)                      # 执行
    assert done.executed is True and done.degraded is True
    assert done.error_code == facts_check.ERROR_CODE and "88,888" not in done.reply
    confirm_card.reset_state()
