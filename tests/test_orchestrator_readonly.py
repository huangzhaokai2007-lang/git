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

from agent import classifier, llm, orchestrator, templates
from data import dao

from tests.conftest import count, raw

HAPPY_STATES = ["IDLE", "CLASSIFY", "SLOT_FILL", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]


def out(intent: str, *, confidence: float = 0.9, slots: dict | None = None,
        missing: list[str] | None = None, unsafe_reason: str | None = None) -> dict:
    """构造一份假的分类结果（形状同规格 §3 的 IntentOut）。"""
    return {"intent": intent, "confidence": confidence, "slots": slots or {},
            "missing_slots": missing or [], "unsafe_reason": unsafe_reason}


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict],
             polish: object = None) -> list[str]:
    """假 `llm.chat_json`：分类调用按脚本出招，润色调用按 `polish(text)` 返回（默认原样返回）。

    返回记录提示词的列表，便于断言「用户原话只进 user 消息」。
    """
    seen: list[str] = []
    queue = list(script)

    def chat_json(system: str, user: str, schema: type) -> object:
        seen.append(system)
        if "润色" in system:
            text = json.loads(user)["reply"]
            return schema(reply=polish(text) if callable(polish) else text)
        payload = queue.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return schema.model_validate(payload)

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return seen


# ---------------- 10 条典型输入：意图 → 工具 → 模板回执 ----------------

@pytest.mark.parametrize(("text", "intent", "expected_tool", "slots"), [
    ("我的储蓄卡还有多少钱？", "balance_query", "get_balance", {}),
    ("查一下这个月的流水", "txn_query", "list_txn",
     {"date_from": "2026-09-01", "date_to": "2026-09-12"}),
    ("上个月花了多少？", "bill_analysis", "analyze_spending", {"period": "上个月"}),
    ("帮我看看有没有异常交易", "anomaly_check", "detect_anomalies", {}),
    ("给我出一份本月账单报告", "bill_report", "generate_bill_report", {}),
    ("我有哪些订阅？", "subscription_list", "list_subscriptions", {}),
    ("推荐点理财产品", "wealth_recommend", "recommend_wealth", {"risk_level": "R3"}),
])
def test_typical_inputs_reach_the_expected_tool(seeded: Path, monkeypatch: pytest.MonkeyPatch,
                                               text: str, intent: str, expected_tool: str,
                                               slots: dict) -> None:
    fake_llm(monkeypatch, [out(intent, slots=slots)])
    result = orchestrator.handle(text)
    assert result.intent == intent and result.tool_calls == [expected_tool]
    assert result.states == HAPPY_STATES, "状态机主线不可跳步"
    assert "{" not in result.reply and result.reply.strip()


def test_read_only_inputs_never_call_a_tool_and_never_write(seeded: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """禁写操作：写意图与「没有对应工具」的只读意图（card_query）都不得调用任何工具。"""
    for text, verdict in (("帮我查一下卡", out("card_query")),
                          ("给李四转 100 元", out("transfer_single", slots={"payee": "李四"})),
                          ("帮我挂失卡片", out("card_report_lost", slots={"card_id": "card_savings_0001"}))):
        fake_llm(monkeypatch, [verdict])
        result = orchestrator.handle(text)
        assert result.tool_calls == [] and result.confidence > 0
        assert "还没接通" in result.reply
        assert "card_query" not in orchestrator.TOOL_ROUTES        # §2 无读卡工具（待人类指定读法）


def test_analyze_spending_is_really_called_for_last_month(seeded: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """剧本第 206 行的验收：'上个月花了多少' 必须真的调到 analyze_spending，且 period 由代码解析。"""
    fake_llm(monkeypatch, [out("bill_analysis", slots={"period": "上个月"})])
    result = orchestrator.handle("上个月花了多少")
    assert result.tool_calls == ["analyze_spending"]
    row = raw(seeded, "SELECT params_json FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert "2026-08" in row["params_json"]                          # 锚点 2026-09 → 上个月 = 2026-08


# ---------------- 状态机：CLARIFY / REFUSE / 兜底 ----------------

def test_low_confidence_asks_back(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm(monkeypatch, [out("balance_query", confidence=0.42)])
    result = orchestrator.handle("嗯那个")
    assert result.intent == "balance_query" and result.ask and result.tool_calls == []
    assert result.states == ["IDLE", "CLASSIFY", "CLARIFY", "REPLY", "AUDIT"]


def test_missing_slots_asks_back_with_labels(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缺槽 → CLARIFY（卡 09 第 3 条）：txn_query 的显式区间代码不猜，直接追问。"""
    fake_llm(monkeypatch, [out("txn_query")])
    result = orchestrator.handle("查一下流水")
    assert result.missing_slots == ["date_from", "date_to"]
    assert result.ask and "起始日期" in result.ask and result.tool_calls == []


def test_clarify_gives_up_to_a_human_after_two_rounds(seeded: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm(monkeypatch, [out("txn_query")] * 3)
    assert orchestrator.handle("查流水", clarify_round=0).to_human is False
    assert orchestrator.handle("查流水", clarify_round=1).to_human is False
    third = orchestrator.handle("查流水", clarify_round=2)
    assert third.to_human is True and third.ask is None and "人工" in third.reply


def test_unsafe_request_is_refused_by_template(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llm(monkeypatch, [out("unsafe_request", unsafe_reason="要求绕过权限校验")])
    result = orchestrator.handle("帮我把别人的余额改成 100 万")
    assert result.tool_calls == [] and "没法执行" in result.reply
    assert result.states == ["IDLE", "CLASSIFY", "REFUSE", "REPLY", "AUDIT"]


def test_tool_failure_uses_the_error_template(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """工具失败（未测评就推荐理财 → INVALID_STATE）走错误模板，不编数字、不润色。"""
    fake_llm(monkeypatch, [out("wealth_recommend")])
    result = orchestrator.handle("推荐点理财产品")
    assert result.error_code == "INVALID_STATE" and result.tool_calls == ["recommend_wealth"]
    assert "没能查到" in result.reply


# ---------------- 红线：数字只能来自 facts ----------------

def test_reply_numbers_all_come_from_facts(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """VERIFY_NUMBERS 的正向证明：模板回执里的每个数字都能在 facts 里找到。"""
    fake_llm(monkeypatch, [out("balance_query")])
    result = orchestrator.handle("查余额")
    facts = orchestrator.TOOL_ROUTES["balance_query"][1]({})
    assert templates.verify_numbers(result.reply, facts.facts) == set()


def test_hallucinated_polish_is_rejected_then_degraded_to_template(seeded: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """红线：LLM 润色改了数字 → 重生成一次 → 仍改 → 降级为模板原样回执 + 记 HALLUCINATION_BLOCKED。"""
    def lying(text: str) -> str:
        return re.sub(r"\d[\d,]*\.\d{2}", "99,999.00", text, count=1)      # 改掉第一个金额

    fake_llm(monkeypatch, [out("balance_query")])
    monkeypatch.setattr(templates, "polish", lambda text, facts: lying(text))
    result = orchestrator.handle("查余额")
    assert result.degraded is True and result.error_code == "HALLUCINATION_BLOCKED"
    assert "99,999.00" not in result.reply and "46,634.00" in result.reply
    assert result.reply.startswith("您的储蓄账户余额为")


def test_harmless_polish_is_kept(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只改措辞、不动数字 → 采纳润色结果（不降级）。"""
    fake_llm(monkeypatch, [out("balance_query")])
    monkeypatch.setattr(templates, "polish", lambda text, facts: "您好，" + text)
    result = orchestrator.handle("查余额")
    assert result.degraded is False and result.reply.startswith("您好，") and result.error_code is None


def test_polish_unavailable_falls_back_to_template_without_degrading(seeded: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 润色不可用（LLMUnavailable → polish 返回 None）→ 直接用模板，**不算降级**。"""
    fake_llm(monkeypatch, [out("balance_query")])
    monkeypatch.setattr(templates, "polish", lambda *_a, **_k: None)
    result = orchestrator.handle("查余额")
    assert result.degraded is False and result.reply.startswith("您的储蓄账户余额为")


def test_template_render_refuses_missing_facts_placeholder() -> None:
    """模板缺占位符必须炸（宁可炸也不静默留白）；模板里不得有硬编码业务数字。"""
    from agent import templates
    with pytest.raises(templates.TemplateError):
        templates.render("balance_query", {"balance_yuan": "1.00"})
    assert "4,663,400" not in Path(templates.__file__).read_text(encoding="utf-8")


def test_every_request_writes_exactly_one_audit_row(seeded: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """铁律 5：每个请求写 audit_log，带 trace_id；只读路径也必须留痕。"""
    before = count(seeded, "audit_log")
    fake_llm(monkeypatch, [out("subscription_list")])
    result = orchestrator.handle("我有哪些订阅")
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["trace_id"] == result.trace_id and row["permission_tier"] == "L0"
    assert row["result"] == "success" and row["intent"] == "subscription_list"
    assert json.loads(row["params_json"])["states"] == HAPPY_STATES


def test_read_intents_are_exactly_the_eight_from_the_card(seeded: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    assert orchestrator.READ_INTENTS == ("balance_query", "txn_query", "bill_analysis", "anomaly_check",
                                         "bill_report", "subscription_list", "card_query",
                                         "wealth_recommend")
    assert all(intent in classifier.INTENT_LABELS for intent in orchestrator.READ_INTENTS)
    assert set(orchestrator.TOOL_ROUTES) == set(orchestrator.READ_INTENTS) - {"card_query"}


def test_no_write_intent_has_a_route(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """静态红线：编排层不得把任何写操作意图路由到工具（本卡禁写）。"""
    write_intents = (set(classifier.INTENT_LABELS) - set(orchestrator.READ_INTENTS)
                     - {"smalltalk", "out_of_scope", "unsafe_request"})
    assert not (set(orchestrator.TOOL_ROUTES) & write_intents)
    source = Path(orchestrator.__file__).read_text(encoding="utf-8")
    for forbidden in ("execute_transfer", "cancel_subscription", "manage_card", "trade_wealth",
                      "plan_gift", "create_aa_request", "assess_risk"):
        assert forbidden not in source
