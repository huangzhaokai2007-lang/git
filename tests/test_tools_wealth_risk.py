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

#: 三档答卷（15 题）：14 道选择（每档 1/3/5 分）+ 年龄分段 1/2/3 分 → 15 / 44 / 73 分 → R1 / R3 / R5
_LOW = {"experience": 0, "income": 0, "income_level": 0, "asset_ratio": 0, "drawdown": 0,
        "horizon": 0, "goal": 0, "reaction": 0, "knowledge": 0, "structure_exp": 0,
        "liquidity": 0, "liability": 0, "concentration": 0, "volatility": 0}
_MID = {key: 2 for key in _LOW}
_HIGH = {key: 4 for key in _LOW}
CONSERVATIVE = {"age": 22, **_LOW}
BALANCED = {"age": 35, **_MID}
AGGRESSIVE = {"age": 50, **_HIGH}
#: seed 的 25 个理财产品（id, 等级, 预期年化）
PRODUCTS = (("prod_r1_mmf", "R1", 2.10), ("prod_r1_cash", "R1", 2.30),
            ("prod_r1_cd", "R1", 2.65),
            ("prod_r2_bond30", "R2", 2.80), ("prod_r2_bond90", "R2", 3.20),
            ("prod_r2_bond180", "R2", 3.60), ("prod_r2_cd_index", "R2", 2.55),
            ("prod_r2_sharkfin", "R2", 4.20),
            ("prod_r3_balance", "R3", 3.90), ("prod_r3_mixed365", "R3", 4.50),
            ("prod_r3_div", "R3", 4.80), ("prod_r3_secondary", "R3", 4.30),
            ("prod_r3_reits", "R3", 5.20), ("prod_r3_gold", "R3", 3.80),
            ("prod_r4_asia", "R4", 5.60), ("prod_r4_quant720", "R4", 6.20),
            ("prod_r4_growth540", "R4", 7.10), ("prod_r4_snowball", "R4", 12.80),
            ("prod_r4_convertible", "R4", 6.80), ("prod_r4_qdii", "R4", 6.50),
            ("prod_r5_theme", "R5", 7.90), ("prod_r5_growth", "R5", 8.50),
            ("prod_r5_pe", "R5", 9.80), ("prod_r5_cta", "R5", 8.60),
            ("prod_r5_airbag", "R5", 16.50))


# ---------------- T13 assess_risk ----------------

def test_t13_data_fields_are_frozen_to_the_spec(seeded: Path) -> None:
    result = wealth.assess_risk(BALANCED)
    assert result.ok, result.message
    assert set(result.data) == {"risk_level", "valid_until"}


@pytest.mark.parametrize(("answers", "level", "score"), [
    (CONSERVATIVE, "R1", 15), (BALANCED, "R3", 44), (AGGRESSIVE, "R5", 73),
])
def test_t13_score_table_maps_answers_to_levels(seeded: Path, answers: dict, level: str,
                                                score: int) -> None:
    """规则表（卡 07 第 1 条）：14 题按选项下标计 1..5 分 + 年龄分段，合计 15–75 → R1..R5。"""
    result = wealth.assess_risk(answers)
    assert result.data["risk_level"] == level and result.facts["total_score"] == score


def _answers_for(total: int) -> dict:
    """构造一份恰好得 `total` 分的答卷（15 ≤ total ≤ 75，14 题各 1..5 分 + 年龄分 1..5）。"""
    questions = list(_wealth_risk.QUESTION_SCORE_RULE)
    slots = questions + ["age"]
    scores = {key: 1 for key in slots}
    extra = total - 15
    for key in slots:
        add = min(4, extra)
        scores[key] += add
        extra -= add
    assert extra == 0, "分数超出 15..75 范围"
    answers = {key: scores[key] - 1 for key in questions}          # 选项下标 = 分数 - 1
    answers["age"] = {1: 22, 2: 35, 3: 50, 4: 60, 5: 80}[scores["age"]]
    return answers


@pytest.mark.parametrize(("total", "level"), [
    (15, "R1"), (26, "R1"), (27, "R2"), (38, "R2"), (39, "R3"),
    (50, "R3"), (51, "R4"), (62, "R4"), (63, "R5"), (75, "R5"),
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
    """年龄题按 `AGE_BANDS` 分段计分：其余十四题恒为最低分（每题 1 分），总分 = 14 + 年龄分。"""
    result = wealth.assess_risk({**_LOW, "age": age})
    assert result.ok and result.facts["total_score"] == 14 + age_score


def test_t13_valid_until_is_one_year_after_the_data_anchor(seeded: Path) -> None:
    """有效期 = 数据集"今天" + 365 天（跨天可复现，不用 datetime.now()）。"""
    result = wealth.assess_risk(BALANCED)
    assert result.data["valid_until"] == "2027-09-12"
    assert result.facts["valid_days"] == 365 and result.facts["max_score"] == 75


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


def test_t13_reports_a_score_for_every_question(seeded: Path) -> None:
    """结果卡要逐题报分：15 题各 1–5 分，逐题之和 == 总分（少报一题用户就会怀疑漏算）。"""
    result = wealth.assess_risk(BALANCED)
    detail = result.facts["score_detail"]
    assert len(detail) == 15 and result.facts["question_count"] == 15
    assert {row["key"] for row in detail} == set(_LOW) | {"age"}
    assert all(1 <= row["score"] <= 5 for row in detail)
    assert sum(row["score"] for row in detail) == result.facts["total_score"]


@pytest.mark.parametrize(("answers", "buyable"), [
    (CONSERVATIVE, ["R1"]), (BALANCED, ["R1", "R2", "R3"]),
    (AGGRESSIVE, ["R1", "R2", "R3", "R4", "R5"]),
])
def test_t13_buyable_range_is_everything_up_to_the_level(seeded: Path, answers: dict,
                                                         buyable: list[str]) -> None:
    """风险等级是"不超过"：R3 能买 R1–R3，结果卡要说清这个区间而不是只甩一个等级。"""
    result = wealth.assess_risk(answers)
    assert result.facts["buyable_levels"] == buyable
    text = buyable[0] if len(buyable) == 1 else f"{buyable[0]}-{buyable[-1]}"
    assert result.facts["buyable_text"] == text         # 只有一个等级时不再写成「R1-R1」


def test_t13_option_scores_are_the_single_source_for_the_ui() -> None:
    """界面在选项后标的分值来自 `option_scores`（工具层），不是界面自己再写一套。"""
    for key in _wealth_risk.QUESTION_SCORE_RULE:
        assert _wealth_risk.option_scores(key) == (1, 2, 3, 4, 5)
    assert _wealth_risk.option_scores("age") == ()          # 年龄按岁数分段，没有选项分值


def test_t13_structured_products_are_in_the_shelf_as_clauses_not_numbers(seeded: Path) -> None:
    """专业性要求：货架上有结构化产品，且条款是**文字**（不是浮点），facts 里不许有 float。"""
    items = {item["product_id"]: item for item in wealth.recommend_wealth("R5").facts["items"]}
    for pid in ("prod_r4_snowball", "prod_r2_sharkfin", "prod_r5_airbag"):
        assert items[pid]["structure"], f"{pid} 该标明产品结构"
        assert items[pid]["knock_out"] and items[pid]["knock_in"]
    assert all(item["kind_label"] for item in items.values())
    assert_no_money_floats(wealth.recommend_wealth("R5").facts, "facts")


def test_products_carry_the_regulatory_category_taxonomy(seeded: Path) -> None:
    """专业口径（2026-10-29）：每只产品都挂**监管标准大类**（资管新规投资性质分类 + 货架惯例），
    推荐结果据此分组；雪球/鲨鱼鳍不得伪装成"基金"。"""
    from tools._wealth_products import CATEGORY_LABELS, CATEGORY_ORDER
    items = wealth.recommend_wealth("R5").facts["items"]
    for item in items:
        assert item["category"] in CATEGORY_ORDER, f"{item['product_id']} 大类不在规范口径里"
        assert item["category_label"] == CATEGORY_LABELS[item["category"]]
    shark = next(i for i in items if i["product_id"] == "prod_r2_sharkfin")
    assert shark["category"] == "structured_deposit" and "结构性存款" in shark["kind_label"]
    snow = next(i for i in items if i["product_id"] == "prod_r4_snowball")
    assert snow["category"] == "note" and "收益凭证" in snow["kind_label"]
    assert "非保本" in snow["protect"]          # 雪球绝不许标"本金保障"
    airbag = next(i for i in items if i["product_id"] == "prod_r5_airbag")
    assert airbag["category"] == "note" and "非保本" in airbag["protect"]


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
    ("R1", ["prod_r1_cd", "prod_r1_cash", "prod_r1_mmf"]),
    ("R2", ["prod_r2_sharkfin", "prod_r2_bond180", "prod_r2_bond90", "prod_r2_bond30",
            "prod_r1_cd", "prod_r2_cd_index", "prod_r1_cash", "prod_r1_mmf"]),
    ("R3", ["prod_r3_reits", "prod_r3_div", "prod_r3_mixed365", "prod_r3_secondary",
            "prod_r2_sharkfin", "prod_r3_balance", "prod_r3_gold", "prod_r2_bond180",
            "prod_r2_bond90", "prod_r2_bond30", "prod_r1_cd", "prod_r2_cd_index",
            "prod_r1_cash", "prod_r1_mmf"]),
    ("R5", ["prod_r5_airbag", "prod_r4_snowball", "prod_r5_pe", "prod_r5_cta",
            "prod_r5_growth", "prod_r5_theme", "prod_r4_growth540", "prod_r4_convertible",
            "prod_r4_qdii", "prod_r4_quant720", "prod_r4_asia", "prod_r3_reits",
            "prod_r3_div", "prod_r3_mixed365", "prod_r3_secondary", "prod_r2_sharkfin",
            "prod_r3_balance", "prod_r3_gold", "prod_r2_bond180", "prod_r2_bond90",
            "prod_r2_bond30", "prod_r1_cd", "prod_r2_cd_index", "prod_r1_cash",
            "prod_r1_mmf"]),
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
    assert yields == ["16.50%", "12.80%", "9.80%", "8.60%", "8.50%", "7.90%", "7.10%",
                      "6.80%", "6.50%", "6.20%", "5.60%", "5.20%", "4.80%", "4.50%",
                      "4.30%", "4.20%", "3.90%", "3.80%", "3.60%", "3.20%", "2.80%",
                      "2.65%", "2.55%", "2.30%", "2.10%"]


def test_t14_horizon_and_amount_filters(seeded: Path) -> None:
    ids = [item["product_id"] for item in wealth.recommend_wealth("R3", 200, 1_200_000).data["items"]]
    assert ids == ["prod_r3_balance", "prod_r3_gold", "prod_r2_bond180",  # 365 天的被过滤
                   "prod_r2_bond90", "prod_r2_bond30", "prod_r2_cd_index",
                   "prod_r1_cash", "prod_r1_mmf"]
    only_short = wealth.recommend_wealth("R5", 0).data["items"]
    assert [item["product_id"] for item in only_short] == ["prod_r3_gold",   # term=0 只有活期类
                                                          "prod_r1_cash",
                                                          "prod_r1_mmf"]


def test_t14_uses_the_stored_assessment_when_level_is_omitted(seeded: Path) -> None:
    assert wealth.recommend_wealth().error_code == ErrorCode.INVALID_STATE   # 未测评
    wealth.assess_risk(BALANCED)
    assert [item["risk_level"] for item in wealth.recommend_wealth().data["items"]] == (
        ["R3", "R3", "R3", "R3", "R2", "R3", "R3", "R2", "R2", "R2",
         "R1", "R2", "R1", "R1"])


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


def test_t14_assessed_ceiling_cannot_be_raised_by_a_requested_level(seeded: Path) -> None:
    """模型把用户口语误抽成高风险，也不能展示超出已完成测评的产品。"""
    wealth.assess_risk(CONSERVATIVE)
    for slot in ('R5', '高风险', 'high'):
        result = wealth.recommend_wealth(slot)
        assert result.ok and result.facts['risk_level'] == 'R1'
        assert [item['product_id'] for item in result.facts['items']] == ['prod_r1_cd',
                                                                         'prod_r1_cash',
                                                                         'prod_r1_mmf']


def test_t14_unrecognized_model_level_falls_back_to_assessed_level(seeded: Path) -> None:
    wealth.assess_risk(BALANCED)
    result = wealth.recommend_wealth('high')
    assert result.ok and result.facts['risk_level'] == 'R3'
    assert len(result.facts['items']) == 14
    assert all(item['risk_label'] for item in result.facts['items'])


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
