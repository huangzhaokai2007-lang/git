"""F03 单测：**异常检测回执的措辞**（`agent/render_lists.py` + 编排层端到端）。

为什么单开一个文件：四条规则本身（金额偏离 / 凌晨 / 短时高频 / 陌生商户）与阈值边界在
`tests/test_query_analysis.py` 里已逐条钉住（那里打的是**工具层**）。本文件钉 F03 验收清单
第 2 条与第 5 条 —— 命中项必须让用户看得懂**是哪笔交易、凭什么判它**；没命中时必须说清
"查过了、没问题"，而不是甩一个孤零零的 0。

三条纪律在本文件逐条落实（照 repo 既有口径）：
- **真工具 + 假 LLM**：工具跑在 `seeded` 合成库上，断言的是真实 facts；假 LLM 只给意图分类。
- **渲染层不换算**：回执里的数字都必须能在 facts 里找到（`templates.verify_numbers` 为空集）。
- **data 契约冻结**：多出来的 `ts` / `counterparty` 只进 facts，**不进 `data`**（§2 冻结三个键）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import llm, orchestrator, render_lists, templates
from data import dao
from tools import query

from tests.conftest import ACCOUNT, raw

#: 编排层主线（卡 09：状态不可跳步）
HAPPY_STATES = ["IDLE", "CLASSIFY", "SLOT_FILL", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]

#: 造一行 facts 明细用的样板（不含任何"来自实现"的值：金额/规则名都按工具层的冻结文案抄）
_LINE_ITEM = {"ts": "2026-08-20T12:00:00", "counterparty": "", "amount_yuan": "-1,000.00",
              "reason": "金额显著高于近期均值", "severity": "medium"}


def out(intent: str, *, slots: dict | None = None) -> dict:
    """构造一份假的分类结果（形状同规格 §3 的 IntentOut）。"""
    return {"intent": intent, "confidence": 0.95, "slots": slots or {},
            "missing_slots": [], "unsafe_reason": None}


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 `llm.chat_json`：分类按脚本出招，**润色一律不可用**（远离 LLM 的不确定性）。

    润色不可用是**预期行为**（模板原文就是最终口径，见 `templates.polish` 捕获 `LLMUnavailable`）。
    """
    from agent import classifier

    queue = list(script)

    def chat_json(system: str, user: object, schema: type) -> object:
        if schema is not classifier.IntentOut:
            raise llm.LLMUnavailable("测试：只读回执的断言不许依赖 LLM 润色")
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


# ---------------- ① 命中：显示「是哪笔交易 + 凭什么判它」 ----------------


def test_reply_shows_time_counterparty_amount_and_reason_for_every_hit(seeded: Path) -> None:
    """F03 验收第 2 条：每条命中一行，四样东西都在（时刻 / 对手方 / 金额 / 依据 + 严重程度）。"""
    result = query.detect_anomalies("2026-06")
    assert result.data["items"], "合成库该账期必须有植入的异常（否则这条断言会空转）"

    reply = templates.render("anomaly_check", result.facts)
    lines = reply.splitlines()
    assert len(lines) == 1 + len(result.facts["items"])       # 一行汇总 + 每条命中一行

    assert str(result.facts["scanned_count"]) in lines[0]
    assert str(result.facts["anomaly_count"]) in lines[0]
    for line, item in zip(lines[1:], result.facts["items"]):
        assert item["ts"] in line, "没有说清是哪一刻的交易"
        assert item["amount_yuan"] in line
        assert item["reason"] in line, "没有说清凭什么判它异常"
        assert render_lists.SEVERITY_CN[item["severity"]] in line
        if item["counterparty"]:
            assert item["counterparty"] in line

    assert not templates.verify_numbers(reply, result.facts), "回执出现 facts 之外的数字（幻觉）"


def test_facts_carry_the_details_but_the_data_contract_stays_frozen(seeded: Path) -> None:
    """`ts` / `counterparty` 只进 facts —— `data` 仍是规格 §2 冻结的三个键（不得增删）。"""
    result = query.detect_anomalies("2026-06")
    for row, fact in zip(result.data["items"], result.facts["items"]):
        assert set(row) == {"txn_id", "reason", "severity"}
        assert fact["txn_id"] == row["txn_id"]
        assert fact["ts"] and isinstance(fact["counterparty"], str)


# ---------------- ② 空结果：明确告知"查过了、没问题" ----------------


def test_empty_verdict_says_no_anomaly_and_never_prints_a_bare_zero(seeded: Path) -> None:
    """空结果有自己的句子（不套列表头 → 不会出现「检测到 0 笔：」这种残句），并说明四个规则的口径。"""
    result = query.detect_anomalies("2027-05")
    assert result.facts["anomaly_count"] == 0 and result.facts["scanned_count"] == 0

    reply = templates.render("anomaly_check", result.facts)
    assert len(reply.splitlines()) == 1
    assert "没有发现异常交易" in reply
    assert str(result.facts["baseline_days"]) in reply and str(result.facts["amount_ratio_threshold"]) in reply
    assert not templates.verify_numbers(reply, result.facts)


def test_empty_verdict_for_a_real_period_keeps_the_scanned_count(seeded: Path) -> None:
    """扫描过但没问题 ≠ 没查到：回执必须带上真实扫描笔数（用户据此判断"查过了"）。

    自己种一个账期（合成库没覆盖的 2024-01）：三笔都是同一个商户、中午发生、间隔 15 天 →
    四条规则一条都不该命中，于是"空结果"句能在这套确定的数据上被单独验证。
    """
    dao.insert_txn(ACCOUNT, "2023-12-01T12:00:00", -100, "out", 1,
                   counterparty="常来商户", id="txn-clean-base")
    dao.insert_txn(ACCOUNT, "2024-01-05T12:00:00", -100, "out", 1,
                   counterparty="常来商户", id="txn-clean-1")
    dao.insert_txn(ACCOUNT, "2024-01-20T12:00:00", -100, "out", 1,
                   counterparty="常来商户", id="txn-clean-2")

    result = query.detect_anomalies("2024-01")
    assert result.facts["scanned_count"] == 2 and result.facts["anomaly_count"] == 0

    reply = templates.render("anomaly_check", result.facts)
    assert "2" in reply and "没有发现异常交易" in reply
    assert not templates.verify_numbers(reply, result.facts)


# ---------------- ③ 明细行的边界：缺对手方、严重程度、缺键 ----------------


def test_line_without_counterparty_prints_nothing_instead_of_a_guess() -> None:
    """没有对手方（工资 / 内部划转）：整截不印，不补「未知商户」这类编造值。"""
    line = render_lists.anomaly_line(dict(_LINE_ITEM))
    assert line.count("｜") == 1, "只有金额那一截分隔符"
    assert "未知" not in line and "None" not in line
    assert _LINE_ITEM["amount_yuan"] in line and _LINE_ITEM["reason"] in line


@pytest.mark.parametrize(("severity", "expected"), [
    ("high", "高危"), ("medium", "关注"), ("low", "留意"), ("CRITICAL", "CRITICAL"),
])
def test_severity_is_plain_chinese_and_out_of_table_values_pass_through(severity: str,
                                                                       expected: str) -> None:
    """严重程度说人话（不说英文枚举）；表外值原样透传 —— 不猜、不编。"""
    assert render_lists.anomaly_line(dict(_LINE_ITEM, severity=severity)).endswith(f"（{expected}）")


@pytest.mark.parametrize("facts", [{"items": [], "scanned_count": 3},
                                   {"items": [dict(_LINE_ITEM)], "scanned_count": 3}])
def test_missing_facts_key_raises_instead_of_rendering_blanks(facts: dict) -> None:
    """缺占位符**直接报错**：宁可炸，也绝不静默留白（静默留白就是幻觉的温床）。"""
    with pytest.raises(templates.TemplateError):
        templates.render("anomaly_check", facts)


# ---------------- ④ 编排层端到端：异常话术 → 工具 → 回执不被降级 ----------------


def test_anomaly_check_end_to_end_is_not_degraded(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F03 的用户原话走完整状态机：工具真的执行，回执不被数字校验降级（无 HALLUCINATION_BLOCKED）。

    种一笔**确定会命中**的异常（第一次出现的商户 → 陌生商户规则），这样断言不依赖合成库里
    植入异常的月份分布：`handle` 的回执必须真的带上这笔交易的时刻、对手方与依据。
    """
    dao.insert_txn(ACCOUNT, "2026-08-20T12:00:00", -100, "out", 1,
                   counterparty="从未见过的商户甲", id="txn-f03-e2e")
    fake_llm(monkeypatch, [out("anomaly_check", slots={"period": "上个月"})])
    turn = orchestrator.handle("上个月有什么异常支出")

    assert turn.intent == "anomaly_check" and turn.tool_calls == ["detect_anomalies"]
    assert turn.states == HAPPY_STATES
    assert turn.degraded is False and turn.error_code is None

    assert "2026-08-20T12:00:00" in turn.reply      # 「是哪笔交易」：时刻
    assert "从未见过的商户甲" in turn.reply           # 对手方
    assert "陌生商户交易" in turn.reply               # 「凭什么判它」：规则名（工具层冻结文案）

    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["trace_id"] == turn.trace_id and row["result"] == "success"
    assert row["error_code"] is None, "出现数字幻觉会被降级并记 HALLUCINATION_BLOCKED"
    assert json.loads(row["params_json"])["tool_calls"] == ["detect_anomalies"]
