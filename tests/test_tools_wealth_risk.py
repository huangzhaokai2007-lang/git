"""任务卡 07 单测：T13 `assess_risk` + T14 `recommend_wealth`（纯代码评级 + 风险适配过滤）。

口径来源：`docs/cards/card-07.md` 第 1–2 条 + 规格 §2 T13/T14。风险等级的字面量（"R1".."R5"）
在断言里写成字面量，不复用被测常量（复用等于自证）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools import _wealth_risk, wealth
from tools.schemas import ErrorCode

from tests.conftest import assert_covered, assert_no_money_floats, count, raw

#: 三档答卷（年龄, 经验, 收入, 回撤, 期限）：合计 6 / 14 / 23 分 → R1 / R3 / R5
CONSERVATIVE = {"age": 22, "experience": 0, "income": 1, "drawdown": 0, "horizon": 0}
BALANCED = {"age": 35, "experience": 2, "income": 2, "drawdown": 2, "horizon": 2}
AGGRESSIVE = {"age": 50, "experience": 4, "income": 4, "drawdown": 4, "horizon": 4}
#: seed 的 6 个理财产品（id, 等级, 预期年化）
PRODUCTS = (("prod_r1_mmf", "R1", 2.10), ("prod_r2_bond90", "R2", 3.20), ("prod_r2_bond180", "R2", 3.60),
            ("prod_r3_mixed365", "R3", 4.50), ("prod_r4_quant720", "R4", 6.20), ("prod_r5_growth", "R5", 8.50))


# ---------------- T13 assess_risk ----------------

def test_t13_data_fields_are_frozen_to_the_spec(seeded: Path) -> None:
    result = wealth.assess_risk(BALANCED)
    assert result.ok, result.message
    assert set(result.data) == {"risk_level", "valid_until"}


@pytest.mark.parametrize(("answers", "level", "score"), [
    (CONSERVATIVE, "R1", 6), (BALANCED, "R3", 14), (AGGRESSIVE, "R5", 23),
])
def test_t13_score_table_maps_answers_to_levels(seeded: Path, answers: dict, level: str,
                                                score: int) -> None:
    """规则表（卡 07 第 1 条）：4 题按选项下标计 1..5 分 + 年龄分段，合计 5–25 → R1..R5。"""
    result = wealth.assess_risk(answers)
    assert result.data["risk_level"] == level and result.facts["total_score"] == score


def _answers_for(total: int) -> dict:
    """构造一份恰好得 `total` 分的答卷（5 ≤ total ≤ 25，4 题各 1..5 分 + 年龄分 1..5）。"""
    questions = list(_wealth_risk.QUESTION_SCORE_RULE)
    slots = questions + ["age"]
    scores = {key: 1 for key in slots}
    extra = total - 5
    for key in slots:
        add = min(4, extra)
        scores[key] += add
        extra -= add
    assert extra == 0, "分数超出 5..25 范围"
    answers = {key: scores[key] - 1 for key in questions}          # 选项下标 = 分数 - 1
    answers["age"] = {1: 22, 2: 35, 3: 50, 4: 60, 5: 80}[scores["age"]]
    return answers


@pytest.mark.parametrize(("total", "level"), [
    (5, "R1"), (9, "R1"), (10, "R2"), (13, "R2"), (14, "R3"), (17, "R3"),
    (18, "R4"), (21, "R4"), (22, "R5"), (25, "R5"),
])
def test_t13_level_bands_boundaries_are_pinned(seeded: Path, total: int, level: str) -> None:
    """分段边界逐点钉住（区间左闭右开）：改 `LEVEL_BANDS` 任何一端都必须让本用例变红。"""
    result = wealth.assess_risk(_answers_for(total))
    assert result.ok and result.facts["total_score"] == total, result.message
    assert result.data["risk_level"] == level


@pytest.mark.parametrize(("age", "age_score"), [
    (18, 1), (25, 1), (26, 2), (40, 2), (41, 3), (55, 3), (56, 4), (65, 4), (66, 5),
])
def test_t13_age_bands_contribute_their_score(seeded: Path, age: int, age_score: int) -> None:
    """年龄题按 `AGE_BANDS` 分段计分：其余四题恒为最低分（每题 1 分），总分 = 4 + 年龄分。"""
    base = {"experience": 0, "income": 0, "drawdown": 0, "horizon": 0}
    result = wealth.assess_risk({**base, "age": age})
    assert result.ok and result.facts["total_score"] == 4 + age_score


def test_t13_valid_until_is_one_year_after_the_data_anchor(seeded: Path) -> None:
    """有效期 = 数据集"今天" + 365 天（跨天可复现，不用 datetime.now()）。"""
    result = wealth.assess_risk(BALANCED)
    assert result.data["valid_until"] == "2027-09-12"
    assert result.facts["valid_days"] == 365 and result.facts["max_score"] == 25


def test_t13_minor_is_forbidden_and_logged(seeded: Path) -> None:
    """卡 07「未成年人边界」：<18 → FORBIDDEN，不写测评结果，按 reviewer 口径留一条 rejected 审计。"""
    before = count(seeded, "audit_log")
    result = wealth.assess_risk({**BALANCED, "age": 17})
    assert result.error_code == ErrorCode.FORBIDDEN
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["result"] == "rejected" and row["intent"] == "risk_assess"
    assert _wealth_risk._ASSESSMENTS == {}          # 不落测评结果
    assert wealth.recommend_wealth().error_code == ErrorCode.INVALID_STATE


@pytest.mark.parametrize("bad_answers", [
    {"age": 30, "experience": 1},                                        # 缺题
    {**BALANCED, "extra": 1},                                           # 多题
    {**BALANCED, "experience": 9},                                      # 选项越界
    {**BALANCED, "experience": -1},                                     # 选项负数
    {**BALANCED, "age": 30.0},                                          # 浮点年龄
    {**BALANCED, "age": True},                                          # bool 冒充整数
    {**BALANCED, "age": 0},                                             # 非正年龄
    {**BALANCED, "income": "3"},                                        # 字符串选项
])
def test_t13_malformed_answers_are_rejected(seeded: Path, bad_answers: dict) -> None:
    assert wealth.assess_risk(bad_answers).error_code == ErrorCode.INVALID_ARGUMENT


def test_t13_rejects_non_dict_answers(seeded: Path) -> None:
    for bogus in (None, [1, 2, 3], "answers", 42):
        assert wealth.assess_risk(bogus).error_code == ErrorCode.INVALID_ARGUMENT


def test_t13_message_numbers_come_from_facts(seeded: Path) -> None:
    result = wealth.assess_risk(BALANCED)
    assert_covered(result.message, result.facts)
    assert_no_money_floats(result.facts, "facts")


def test_t13_is_pure_code_never_an_llm() -> None:
    """规格 T13：结果由**代码**按规则算，不由 LLM 判断（静态断言模块不引用模型客户端）。"""
    source = Path(_wealth_risk.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)
    assert "QUESTION_SCORE_RULE" in source and "AGE_BANDS" in source and "LEVEL_BANDS" in source


# ---------------- T14 recommend_wealth ----------------

def test_t14_data_fields_are_frozen_to_the_spec(seeded: Path) -> None:
    result = wealth.recommend_wealth("R3")
    assert result.ok and set(result.data) == {"items"}
    assert set(result.data["items"][0]) == {"product_id", "name", "risk_level", "yield", "term"}


@pytest.mark.parametrize(("level", "expected_ids"), [
    ("R1", ["prod_r1_mmf"]),
    ("R2", ["prod_r2_bond180", "prod_r2_bond90", "prod_r1_mmf"]),
    ("R3", ["prod_r3_mixed365", "prod_r2_bond180", "prod_r2_bond90", "prod_r1_mmf"]),
    ("R5", ["prod_r5_growth", "prod_r4_quant720", "prod_r3_mixed365", "prod_r2_bond180",
            "prod_r2_bond90", "prod_r1_mmf"]),
])
def test_t14_never_recommends_above_the_user_level(seeded: Path, level: str,
                                                   expected_ids: list[str]) -> None:
    """卡 07 第 2 条：**绝不推荐超风险等级产品**（按收益降序，逐档钉住完整清单）。"""
    items = wealth.recommend_wealth(level).data["items"]
    assert [item["product_id"] for item in items] == expected_ids
    ceiling = int(level[1])
    assert all(int(item["risk_level"][1]) <= ceiling for item in items)


def test_t14_sorted_by_yield_descending(seeded: Path) -> None:
    """按预期年化降序；`yield` 是百分数字符串（不引入浮点，见模型 docstring）。"""
    yields = [item["yield"] for item in wealth.recommend_wealth("R5").data["items"]]
    assert yields == ["8.50%", "6.20%", "4.50%", "3.60%", "3.20%", "2.10%"]


def test_t14_horizon_and_amount_filters(seeded: Path) -> None:
    ids = [item["product_id"] for item in wealth.recommend_wealth("R3", 200, 1_200_000).data["items"]]
    assert ids == ["prod_r2_bond180", "prod_r2_bond90", "prod_r1_mmf"]      # 365 天与 R3 被过滤
    only_short = wealth.recommend_wealth("R5", 0).data["items"]
    assert [item["product_id"] for item in only_short] == ["prod_r1_mmf"]   # term=0 只有货币基金


def test_t14_uses_the_stored_assessment_when_level_is_omitted(seeded: Path) -> None:
    assert wealth.recommend_wealth().error_code == ErrorCode.INVALID_STATE   # 未测评
    wealth.assess_risk(BALANCED)
    assert [item["risk_level"] for item in wealth.recommend_wealth().data["items"]] == (
        ["R3", "R2", "R2", "R1"])


def test_t14_expired_assessment_is_treated_as_unassessed(seeded: Path) -> None:
    wealth.assess_risk(BALANCED)
    _wealth_risk._ASSESSMENTS["u_zhangsan_0001"]["valid_until"] = "2026-01-01"
    assert wealth.recommend_wealth().error_code == ErrorCode.INVALID_STATE


def test_t14_empty_result_is_ok_with_a_message(seeded: Path) -> None:
    result = wealth.recommend_wealth("R1", amount=50)          # 起购额最低 100 分
    assert result.ok and result.data["items"] == [] and result.facts["item_count"] == 0
    assert "没有合适的产品" in result.message


@pytest.mark.parametrize(("level", "horizon", "amount"), [
    ("R9", None, None), ("R0", None, None), ("r1", None, None), (None, -1, None),
    ("R3", 10.5, None), ("R3", None, -1), ("R3", None, 1.5), ("", None, None),
])
def test_t14_bad_arguments_are_rejected(seeded: Path, level: object, horizon: object,
                                        amount: object) -> None:
    assert wealth.recommend_wealth(level, horizon, amount).error_code == ErrorCode.INVALID_ARGUMENT


def test_t14_message_numbers_come_from_facts(seeded: Path) -> None:
    result = wealth.recommend_wealth("R3")
    assert_covered(result.message, result.facts)
    assert_no_money_floats(result.facts, "facts")


def test_t14_product_catalog_is_public_so_no_ownership_assertion(seeded: Path) -> None:
    """规格 T14 读的是**公共产品目录**（`wealth_product` 无 `user_id` 列）→ 本工具无归属断言。

    与卡 04「资源查询必须带 user 归属断言」不冲突：那条针对**有归属**的资源（卡/订阅/流水/账户）。
    """
    assert "user_id" not in raw(seeded, "SELECT * FROM wealth_product LIMIT 1")[0]
    assert len(wealth.recommend_wealth("R5").data["items"]) == len(PRODUCTS)
