"""F10 单测：风险测评端到端 —— 编排层接通（问卷引导 / 一句话解析 / 表单提交）。

沿用仓库做法：**真工具 + 假 LLM**（monkeypatch `llm.chat_json`）——测评走真实的
`tools/_wealth_risk.assess_risk`（代码计分），断言的是真实 facts 与回执；
假 LLM 只负责给出分类结果与「一个数字都不动」的润色。

**本文件只测编排层特有行为**（工具层自己的计分表 / 边界 / 归属已在 `test_tools_wealth_risk.py`）：

① 聊天里只说"做测评" → 回引导语、**不调工具**、界面据此弹问卷；
② 一句话把 5 题说全 → 直接一次算完（不弹问卷）；
③ 一句话只说了一部分 → CLARIFY 追问（不漏算、不猜）；
④ 表单提交入口 `submit_assessment` → 计分 + 回执（含总分/满分/等级/有效期）；
⑤ 年龄限制（<18）→ 工具层 FORBIDDEN，编排层如实报错、不算成功；
⑥ 用户隔离：A 的测评不会出现在 B 的推荐里；
⑦ 有效期：过期后按未测评处理；
⑧ 回执里的数字全部可溯（未降级）。

规格依据：`docs/01-接口规格.md` §2 T13（第 108 行：权限档 **L0**）+ 卡 07 第 1 条。
F10 验收清单见 `docs/features/F10-risk-assessment.md`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import llm, orchestrator, risk_flow
from tools import _wealth_risk, query, wealth
from tools.schemas import ErrorCode

from tests.conftest import assert_covered

SESSION = "sess-f10"
#: 三档答卷：合计 6 / 14 / 23 分 → R1 / R3 / R5（与工具层单测同一批事实，便于交叉核对）
CONSERVATIVE = {"age": 22, "experience": 0, "income": 1, "drawdown": 0, "horizon": 0}
BALANCED = {"age": 35, "experience": 2, "income": 2, "drawdown": 2, "horizon": 2}
AGGRESSIVE = {"age": 50, "experience": 4, "income": 4, "drawdown": 4, "horizon": 4}


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))
    monkeypatch.setattr(llm, "chat_json", chat_json)


def out(intent: str, *, slots: dict | None = None, confidence: float = 0.9) -> dict:
    return {"intent": intent, "confidence": confidence, "slots": slots or {},
            "missing_slots": [], "unsafe_reason": None}


def one_sentence(answers: dict) -> str:
    """把一份答卷渲染成一句话（供解析器用例用）。"""
    age = answers["age"]
    exp = risk_flow.QUESTIONS["experience"][1][answers["experience"]]
    inc = risk_flow.QUESTIONS["income"][1][answers["income"]]
    cur = risk_flow.QUESTIONS["drawdown"][1][answers["drawdown"]]
    hor = risk_flow.QUESTIONS["horizon"][1][answers["horizon"]]
    return f"年龄{age}，经验{exp}，收入{inc}，回撤{cur}，期限{hor}"


# ---------------- ① 聊天触发：回引导语，不调工具 ----------------

def test_asking_for_assessment_prompts_without_calling_the_tool(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只说"做测评" → 回引导语、**不调工具**（问卷还没收呢，不能凭空算分）。"""
    fake_llm(monkeypatch, [out("risk_assess")])
    turn = orchestrator.handle("帮我做风险测评")
    assert turn.intent == "risk_assess" and turn.tool_calls == []
    assert turn.reply == risk_flow.PROMPT and turn.tier == "L0"


def test_the_prompt_is_the_template_constant_not_a_second_copy() -> None:
    """引导语只有一份（`templates.T_RISK_PROMPT`）—— 防止两处文案漂移。"""
    from agent import templates
    assert risk_flow.PROMPT == templates.T_RISK_PROMPT


# ---------------- ② 一句话说全 5 题 → 直接算 ----------------

@pytest.mark.parametrize(("answers", "level", "score"), [
    (CONSERVATIVE, "R1", 6), (BALANCED, "R3", 14), (AGGRESSIVE, "R5", 23),
])
def test_a_complete_sentence_is_scored_in_one_shot(seeded: Path, monkeypatch: pytest.MonkeyPatch,
                                                   answers: dict, level: str, score: int) -> None:
    """5 题一次说全 → 直接调工具算完，**不弹问卷**（实测缺陷：曾在 `answers` 已齐全时仍弹问卷）。"""
    fake_llm(monkeypatch, [out("risk_assess", slots={"answers": one_sentence(answers)})])
    turn = orchestrator.handle("帮我做风险测评，" + one_sentence(answers))
    assert turn.tool_calls == ["assess_risk"], "5 题齐全却没直接算"
    assert level in turn.reply and f"{score}/" in turn.reply


@pytest.mark.parametrize("sentence", [
    "年龄35，经验1-3年，收入稳定，回撤10%以内，期限1年",
    "年龄 35，经验 1-3 年，收入稳定，回撤 10% 以内，期限 1 年",
    "35岁，投资经验1-3年，收入稳定，能承受10%以内回撤，投资期限1年",
])
def test_parser_accepts_both_word_orders_and_spacing(sentence: str) -> None:
    """「年龄35」与「35岁」两种语序、有无空格，都要解析成同一份答卷。"""
    assert risk_flow.parse_answers(sentence) == BALANCED


def test_parser_never_mistakes_the_experience_range_for_an_age() -> None:
    """**回归**：「经验1-3年」里的 `1` 不得被当成 1 岁（实测缺陷：曾触发未成年拒绝）。

    年龄必须有锚点词（年龄/岁/周岁）才算数。
    """
    got = risk_flow.parse_answers("年龄35，经验1-3年，收入稳定，回撤10%以内，期限1年")
    assert got["age"] == 35, f"年龄被解析成了 {got['age']}（把经验区间的数字当岁数了）"


def test_age_is_the_years_not_an_option_index() -> None:
    """**回归**：`answers["age"]` 存的是**岁数**，不是选项下标（口径与另外 4 题不同）。

    工具层 `_score()` 拿它去查 `AGE_BANDS`；曾错误地折算成下标 `1` → 判成 1 岁 → 未成年拒绝。
    这里「年龄50」若被送成下标 4，`AGE_BANDS` 会去查"4 岁"那一段而**找不到**（4 < 18）。
    """
    got = risk_flow.parse_answers("年龄50，投资经验超过5年，收入极其稳定")
    assert got["age"] == 50, f"年龄被送成了 {got['age']}（下标而不是岁数）"


# ---------------- ③ 说了一部分 → 追问，不猜 ----------------

def test_a_partial_sentence_asks_back_instead_of_guessing(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只说了 2 题 → CLARIFY 追问（**不用默认值补**，那是替用户编答案）。"""
    fake_llm(monkeypatch, [out("risk_assess", slots={"answers": "年龄35，收入稳定"})])
    turn = orchestrator.handle("我想做风险测评，今年 35，收入还算稳定")
    assert turn.tool_calls == [] and turn.ask
    assert not risk_flow.count_covers_every_question(risk_flow.parse_answers("年龄35，收入稳定"))


def test_unrecognizable_sentence_falls_back_to_the_prompt(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一个有效答案都没解析出来 → 回引导语（等同"什么都没说"），不硬算、不猜。"""
    fake_llm(monkeypatch, [out("risk_assess", slots={"answers": "随便吧"})])
    turn = orchestrator.handle("随便做一下风险测评")
    assert turn.tool_calls == [] and turn.reply == risk_flow.PROMPT


# ---------------- ④ 表单提交入口 ----------------

@pytest.mark.parametrize(("answers", "level"), [
    (CONSERVATIVE, "R1"), (BALANCED, "R3"), (AGGRESSIVE, "R5"),
])
def test_submit_assessment_scores_and_reports(seeded: Path, answers: dict, level: str) -> None:
    """表单入口：一次提交算完，回执报出总分/满分/等级/有效期（验收清单第 1 条）。"""
    turn = orchestrator.submit_assessment(answers, session_id=SESSION)
    assert turn.intent == "risk_assess" and turn.tool_calls == ["assess_risk"]
    assert turn.executed is True and turn.tier == "L0" and turn.error_code is None
    assert level in turn.reply and "有效" in turn.reply
    assert turn.states == ["IDLE", "SLOT_FILL", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]


def test_submit_assessment_receipt_numbers_come_from_facts(seeded: Path) -> None:
    """红线：回执里的每个数字都能在 facts 里找到，且未降级。"""
    turn = orchestrator.submit_assessment(BALANCED, session_id=SESSION)
    assert turn.degraded is False
    result = wealth.assess_risk(BALANCED)
    assert_covered(turn.reply, result.facts)


def test_submit_assessment_says_the_result_is_session_only(seeded: Path) -> None:
    """验收清单第 3 条：结果**仅存进程内**，回执要如实说清（不能让用户以为长期有效）。"""
    turn = orchestrator.submit_assessment(BALANCED, session_id=SESSION)
    assert "本次会话" in turn.reply


# ---------------- ⑤ 年龄限制 ----------------

def test_underage_is_refused_and_never_marked_executed(seeded: Path) -> None:
    """<18 岁 → 工具层 FORBIDDEN；编排层如实报错、`executed=False`（没算就是没算）。"""
    turn = orchestrator.submit_assessment({**BALANCED, "age": 16}, session_id=SESSION)
    assert turn.error_code == ErrorCode.FORBIDDEN and turn.executed is False
    assert "未成年" in turn.reply


# ---------------- ⑥ 用户隔离 ----------------

def test_assessment_is_scoped_to_the_current_user(seeded: Path) -> None:
    """验收清单第 2 条：A 的测评不得被 B 用上（`_ASSESSMENTS` 按 user_id 分键）。"""
    orchestrator.submit_assessment(AGGRESSIVE, session_id=SESSION)      # 张三：R5
    query.set_current_user("u_other_9999")                              # 换成另一个人
    assert wealth.recommend_wealth().error_code == ErrorCode.INVALID_STATE
    query.set_current_user(None)                                        # 复位（conftest 的清理口径）


# ---------------- ⑦ 有效期 ----------------

def test_expired_assessment_counts_as_not_assessed(seeded: Path) -> None:
    """验收清单第 2 条：过了有效期按未测评处理（不得拿着过期等级去推荐）。"""
    orchestrator.submit_assessment(BALANCED, session_id=SESSION)
    _wealth_risk._ASSESSMENTS["u_zhangsan_0001"]["valid_until"] = "2026-01-01"
    assert wealth.recommend_wealth().error_code == ErrorCode.INVALID_STATE


# ---------------- ⑧ 解析器的纯度（不依赖 LLM、不依赖数据库） ----------------

@pytest.mark.parametrize("garbage", ["", "   ", "做测评", "我想理财", "年龄", "岁"])
def test_parser_returns_empty_on_garbage_instead_of_guessing(garbage: str) -> None:
    """认不出就是空 dict（**绝不猜**）—— 由调用方判缺失走 CLARIFY。"""
    assert risk_flow.parse_answers(garbage) == {}


def test_missing_questions_helper_matches_the_tool_contract() -> None:
    """`missing_questions` 与工具层 `_score()` 的五题契约对齐（多一题少一题都拒）。"""
    assert risk_flow.missing_questions(BALANCED) == []
    assert len(risk_flow.missing_questions({"age": 35})) == 4
    assert risk_flow.count_covers_every_question({**BALANCED, "extra": 1}) is False
