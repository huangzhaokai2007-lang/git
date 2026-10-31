"""F11 推荐区的**视图筛选**单测（2026-10-31 加期限筛选时补）。

筛选是纯视图行为：只筛工具已返回的 items，不重新查工具、不动风险上限。
把它抽成纯函数就是为了能这样直接测 —— 不用起 Streamlit。
"""

from __future__ import annotations

from interfaces.web import wealth_ui

#: (名称, 期限天数) —— 覆盖活期、边界 365、边界 1095、超长
ITEMS = [
    {"name": "活期", "term": 0},
    {"name": "一年整", "term": 365},
    {"name": "一年零一天", "term": 366},
    {"name": "三年整", "term": 1095},
    {"name": "三年零一天", "term": 1096},
]


def _names(items) -> list[str]:
    return [item["name"] for item in items]


def test_all_keeps_everything() -> None:
    assert _names(wealth_ui.filter_by_term(ITEMS, "全部")) == [i["name"] for i in ITEMS]


def test_within_one_year_includes_on_demand_and_the_boundary() -> None:
    """活期（0 天）归入「1 年内」——它确实随时能取；365 天含在区间内。"""
    assert _names(wealth_ui.filter_by_term(ITEMS, "1 年内")) == ["活期", "一年整"]


def test_one_to_three_years_is_left_open() -> None:
    """左开右闭：366 进、365 不进；1095 进、1096 不进。"""
    assert _names(wealth_ui.filter_by_term(ITEMS, "1–3 年")) == ["一年零一天", "三年整"]


def test_over_three_years() -> None:
    assert _names(wealth_ui.filter_by_term(ITEMS, "3 年以上")) == ["三年零一天"]


def test_empty_result_is_empty_not_an_error() -> None:
    assert wealth_ui.filter_by_term([{"name": "活期", "term": 0}], "3 年以上") == []


def test_term_sort_is_ascending() -> None:
    ordered = wealth_ui.order_items(ITEMS, "期限 ↑")
    assert [item["term"] for item in ordered] == [0, 365, 366, 1095, 1096]


def test_default_sort_keeps_the_tool_order() -> None:
    """「收益 ↓」是工具层给的顺序，界面不许自己重排。"""
    assert _names(wealth_ui.order_items(ITEMS, "收益 ↓")) == [i["name"] for i in ITEMS]


def test_missing_term_is_treated_as_on_demand() -> None:
    """老 facts 没有 `term` 时按活期处理，不能抛异常。"""
    assert _names(wealth_ui.filter_by_term([{"name": "缺字段"}], "1 年内")) == ["缺字段"]
