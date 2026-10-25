"""F02 流水查询升级包：事实核算 **与** 回执渲染 **与** 网页卡（2026-10-26）。

背景（用户验收口径）：只回一句「共 N 笔、支出 X 元」是一份**删减过的账单** ——
分类去向、按日走势、逐笔明细全不在视野里，界面也没有可以点开一层的地方。
这组用例把三件事钉死，后面谁改回去都会当场变红：

1. **事实包**（`tools/_txn_insight`）：分类 / 日序 / 大额 / 净额全部**工具层算好**掺进 facts，
   这里用**独立复算**核对分组总和、笔数与占比 —— 界面与回执读同一份数（铁律 2）；
2. **回执**（`agent/render_query`）：新增的分类汇总、大额榜里的每个数字必须能在 facts 里
   找到出处（`guard.facts_check` 复跑一遍），多打印一个数字就算幻觉；
3. **网页卡**（`interfaces/web/txn_card_ui`）：四个指标 + 每个分类一块占比条 + 「逐笔展开」
   折叠 + 按日走势 + 全量明细，全部用真实 `AppTest` 断言“**真的画出来了**”。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import tools.query as query
from agent import render_query, templates


PERIOD = ("2026-08-01", "2026-08-31")


@pytest.fixture()
def txns(seeded: Path):                       # noqa: ARG001 —— `seeded` 负责建库接 DAO
    """2026-08 的流水结果（合成数据每个月都有数，不是凑出来的空集）。"""
    query.set_current_user(None)
    return query.list_txn(*PERIOD, limit=500)


# ---------------- 事实包：工具层算好的那一摞数 ----------------


def test_facts_carry_the_whole_insight(txns) -> None:
    """分类 / 日序 / 大额 / 净额 / 明细 全在 facts 里，且明细笔数与本页一致。"""
    facts = txns.facts
    assert facts["categories"] and facts["daily"] and facts["largest"]
    assert [item["id"] for item in facts["items"]] == [row["id"] for row in txns.data["items"]]
    assert facts["out_count"] + facts["in_count"] == facts["total_count"]


def test_category_groups_add_up_to_the_page_expense(txns) -> None:
    """**独立复算**：分类金额合计 = 本页支出合计，笔数合计 = 支出笔数，占比不超 100。

    分组漏笔、或把手取收入也算进分组（分母变糊涂账），总和就会对不上 —— 这是“只看合计数”最容易踩的坑。
    """
    facts = txns.facts
    groups = facts["categories"]
    assert sum(group["amount"] for group in groups) == sum(
        abs(item["amount"]) for item in facts["items"] if item["amount"] < 0)
    assert sum(group["count"] for group in groups) == facts["out_count"]
    assert 0 < sum(group["pct"] for group in groups) <= 100
    assert [group["amount"] for group in groups] == sorted(
        (group["amount"] for group in groups), reverse=True)          # 降序：大额头在前面


def test_groups_never_count_income(txns) -> None:
    """分类只统计支出（口径同 T3 账单分析）：收入单笔就九千五，混进来占比就没意义了。"""
    facts = txns.facts
    income_ids = {item["id"] for item in facts["items"] if item["amount"] > 0}
    assert income_ids and all(group["amount"] > 0 for group in facts["categories"])
    assert sum(group["count"] for group in facts["categories"]) < facts["total_count"]


def test_net_equals_inflow_minus_outflow(txns) -> None:
    """净额 = 收入 - 支出（正数=净流入，负数=净支出），且两者本身不为零。"""
    facts = txns.facts
    assert facts["net"] == facts["in_sum"] - facts["out_sum"]
    assert facts["in_sum"] > 0 < facts["out_sum"]


def test_daily_series_is_ascending_and_starts_from_ts(txns) -> None:
    """日序升序、日期是 `ts` 的前十个字符（不是新编的日期号码）。"""
    daily = txns.facts["daily"]
    days = [item["date"] for item in daily]
    assert days == sorted(days)
    assert all(day.startswith(PERIOD[0][:4]) for day in days)
    assert sum(item["out"] for item in daily) == txns.facts["out_sum"]


def test_empty_range_invents_nothing(seeded: Path) -> None:  # noqa: ARG001
    """空区间：分类/大额/明细全是空列表 —— 不许编个 0 出来假装画得出来。"""
    query.set_current_user(None)
    empty = query.list_txn("2026-09-13", "2026-09-14")       # 数据窗口之外的两天
    assert empty.ok and empty.facts["total_count"] == 0
    assert empty.facts["categories"] == [] and empty.facts["largest"] == []
    assert empty.facts["items"] == []


# ---------------- 回执：多出来的句子必须在 facts 里有出处 ----------------


def test_reply_carries_groups_largest_and_stays_verifiable(txns) -> None:
    """回执 = 区间 + 分类汇总 + 大额榜 + 明细表；`facts_check` 复跑一遍不许有 extra 数字。"""
    from guard import facts_check

    reply = templates.render("txn_query", txns.facts)
    assert PERIOD[0] in reply and PERIOD[1] in reply              # 点明查的是哪一段
    assert "| 分类 | 笔数 | 金额（元） | 占比 |" in reply
    assert "金额最大的几笔" in reply
    assert "| 时间 | 交易对手 | 金额（元） | 分类 |" in reply
    ok, offenders = facts_check.verify_numbers(reply, txns.facts)
    assert ok, f"回执出现了事实包里查不到的数字：{offenders}"


def test_reply_degrades_without_the_new_facts() -> None:
    """旧形状的 facts（没有分类/大额）照样能渲染：缺数据宁可不说，也不许硬凑。"""
    reply = render_query.render_txns({
        "date_from": PERIOD[0], "date_to": PERIOD[1], "total_count": 1,
        "page_out_sum_yuan": "15.00", "page_in_sum_yuan": "0.00",
        "items": [{"ts": "2026-08-28T16:27:00", "amount_yuan": "-15.00",
                   "counterparty": "街角烧烤", "category": "餐饮"}]})
    assert "街角烧烤" in reply
    assert "| 分类 | 笔数 | 金额（元） | 占比 |" not in reply      # 分类汇总那张表的表头
    assert "金额最大的几笔" not in reply
    assert "净" not in reply                                      # 没给 net_yuan 就不提净额


# ---------------- 网页卡：真的画出来了没有 ----------------


def _run_card(facts: dict) -> AppTest:
    """真跑一遍 `txn_card_ui.render`（`AppTest`），断言落在**页面上**而不是函数返回值上。"""
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from interfaces.web import txn_card_ui\n"
        f"facts = json.loads({json_dumps(facts)!r})\n"
        "txn_card_ui.render(facts, key='txn')\n"
    )
    at = AppTest.from_string(script, default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def json_dumps(obj: object) -> str:
    """`json.dumps`（只是为了让 `_run_card` 读起来顺，不额外 add ensure_ascii 之外参数）。"""
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


def test_card_draws_metrics_groups_and_expanders(txns) -> None:
    """指标 + 占比条 + 每个分类一个「展开 N 笔明细」折叠 + 大额榜 + 全量明细，都要真出来。"""
    at = _run_card(dict(txns.facts))

    metrics = {metric.label: metric.value for metric in at.metric}
    assert metrics["支出合计（元）"] == txns.facts["out_sum_yuan"]
    assert metrics["收入合计（元）"] == txns.facts["in_sum_yuan"]
    assert metrics["交易笔数"] == str(txns.facts["total_count"])

    first = txns.facts["categories"][0]
    labels = [str(expander.label) for expander in at.expander]
    assert any(f"展开「{first['key']}」" in label for label in labels)
    assert any("金额最大的" in label for label in labels)
    assert any("查看本区间全部" in label for label in labels)
    bars = at.get("progress")                                     # AppTest 用 `get` 取非列表属性的元素
    assert len(bars) == len(txns.facts["categories"])             # 每个分类一根占比条
    assert bars[0].value == pytest.approx(first["pct"])            # AppTest 给的是百分数（不是 0~1）


def test_card_rows_echo_the_facts_and_nothing_else(txns) -> None:
    """卡片上的明细表：金额/对手方照抄 facts，界面**不求和、不改数**。"""
    at = _run_card(dict(txns.facts))
    rendered = "\n".join(str(markdown.value) for markdown in at.markdown)
    first_item = txns.facts["items"][0]
    for text in (first_item["amount_yuan"], first_item["counterparty"],
                 str(first_item["ts"])[:16].replace("T", " ")):
        assert text in rendered, f"明细表里缺这笔：{text}"


def test_card_draws_the_daily_trend(txns) -> None:
    """按日走势图：日序来自 facts 的 `daily`，图上点数 ≥ 2 才会画（一天画不出走势）。"""
    at = _run_card(dict(txns.facts))
    assert len(at.get("vega_lite_chart")) == 1                    # 走势图真的上屏了
    texts = "\n".join([str(markdown.value) for markdown in at.markdown]
                      + [str(caption.value) for caption in at.get("caption")])
    assert "按日支出走势" in texts


def test_card_stays_silent_without_items() -> None:
    """没有 items → 卡片返回 False（上层退回纯文本），不画空图、不补零。"""
    from interfaces.web import txn_card_ui
    assert txn_card_ui.render({"total_count": 0, "categories": []}, key="none") is False
