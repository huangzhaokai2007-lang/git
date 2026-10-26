"""卡 25 单测：F02 流水明细 / F14 分类占比的**渲染层**（`agent/render_query.py`）。

三条边界（照仓内每个渲染函数的用例口径：正常 / 空 / 非法）：
- 正常：明细行、占比条**一行不少**地进回执（老口径把这两块整块丢在回执外）；
- 空：走**自己的句子**，且不丢账期/条数（只说「没有」用户分不清"真没有"还是"没查到"）；
- 非法：facts 缺键 → 抛 `TemplateError`（宁可炸，也不许静默留白）。
"""

from __future__ import annotations

import pytest

from agent import render_query


# ---------------- F02 流水查询 ----------------

TXN_FACTS = {
    "total_count": 2, "returned_count": 2,
    #: 查询区间（T2 facts 原样回传）：头句点明"查的是哪一段"，用户才核对得了月份对不对
    "date_from": "2026-08-01", "date_to": "2026-08-31",
    "page_out_sum_yuan": "465.00", "page_in_sum_yuan": "11,046.00",
    "items": [{"id": "txn-1", "ts": "2026-08-28T16:27:00", "counterparty": "街角烧烤",
               "category": "餐饮", "amount": -1500, "amount_yuan": "-15.00",
               "ref_no": "2026082816270001", "status": "done"},
              {"id": "txn-2", "ts": "2026-08-28T13:38:00", "counterparty": "好买商城",
               "category": "电商", "amount": -47000, "amount_yuan": "-470.00",
               "ref_no": "2026082813380002", "status": "refunded"}],
}


def test_txn_reply_lists_every_transaction_not_just_a_total() -> None:
    """缺口本身：老口径只印一句合计，用户**一笔流水都看不到** → 明细表必须上回执。"""
    reply = render_query.render_txns(TXN_FACTS)
    assert "共查到 2 笔交易" in reply
    assert "| 时间 | 流水号 | 交易对手 | 金额（元） | 状态 | 分类 |" in reply
    for item, status in zip(TXN_FACTS["items"], ("已完成", "退款")):
        assert (f"| {item['ts'][:16].replace('T', ' ')} | {item['ref_no']} "
                f"| {item['counterparty']} | {item['amount_yuan']} | {status} "
                f"| {item['category']} |") in reply


def test_txn_reply_never_truncates_the_items() -> None:
    """2026-10-26 验收口径：`items` 有多少笔就列多少笔，**不许再只列一部分**。"""
    many = [{**TXN_FACTS["items"][0], "ts": f"2026-08-{day:02d}T10:00:00",
             "ref_no": f"202608{day:02d}10000001", "amount_yuan": "-15.00"}
            for day in range(1, 29)]
    reply = render_query.render_txns({**TXN_FACTS, "items": many})
    assert reply.count("\n| 2026-08-") == len(many)          # 28 笔一行不少
    assert "一部分" not in reply and "其余" not in reply


def test_txn_reply_drops_the_summary_blocks() -> None:
    """净额行 / 分类汇总表 / 大额榜已从回执移除（用户嫌"前面这些"喧宾夺主）。"""
    reply = render_query.render_txns({**TXN_FACTS, "net_yuan": "1.00",
                                      "categories": [{"key": "餐饮", "count": 1,
                                                      "amount_yuan": "15.00", "pct": 100}],
                                      "largest": TXN_FACTS["items"]})
    assert "净" not in reply
    assert "| 分类 | 笔数 |" not in reply
    assert "金额最大的几笔" not in reply


def test_txn_empty_keeps_the_count_so_it_does_not_read_like_a_failure() -> None:
    """空结果：条数照印（0 也是交代），且不再套列表头。"""
    reply = render_query.render_txns({**TXN_FACTS, "total_count": 0, "items": []})
    assert "共查到 0 笔交易" in reply
    assert "| 时间 |" not in reply


def test_txn_missing_facts_raises_instead_of_blanking() -> None:
    """facts 缺键 → 抛错（与 `render_lists` 同口径：宁可炸也不许留白）。"""
    with pytest.raises(render_query.TemplateError):
        render_query.render_txns({"items": [{"ts": "2026-08-01T00:00:00"}]})


def test_txn_cell_escapes_pipes_and_never_invents_a_value() -> None:
    """竖线会破坏 Markdown 表格 → 换斜杠；空值用占位符（不编造）。"""
    facts = {"total_count": 1, "page_out_sum_yuan": "0.00", "page_in_sum_yuan": "0.00",
             "date_from": "2026-08-01", "date_to": "2026-08-31",
             "items": [{"ts": "2026-08-01T00:00:00", "counterparty": "A|B", "category": "",
                        "amount_yuan": "-1.00"}]}
    reply = render_query.render_txns(facts)
    assert "A/B" in reply and "A|B" not in reply
    assert "| — |" in reply                       # 空分类占位，不是编一个分类出来


# ---------------- F14 账单分析 ----------------

SPEND_FACTS = {"period": "2026-08", "group_by": "category", "group_count": 2,
               "total_yuan": "4,940.00", "prev_total_yuan": "13,456.00", "vs_prev_pct": -32,
               "groups": [{"key": "电商", "amount": 437000, "pct": 88, "amount_yuan": "4,370.00"},
                          {"key": "餐饮", "amount": 57000, "pct": 12, "amount_yuan": "570.00"}]}


def test_spending_reply_shows_where_the_money_went() -> None:
    """缺口本身：老口径只有一句合计 + 环比，**分类占比整块被丢回执外**。"""
    reply = render_query.render_spending(SPEND_FACTS)
    assert "2026-08 共支出 4,940.00 元，环比 -32%。" in reply
    assert "| 分类 | 金额（元） | 占比 |" in reply
    assert "| 电商 | 4,370.00 | 88% " in reply
    assert "| 餐饮 | 570.00 | 12% " in reply


def test_spending_bar_has_ten_cells_and_is_rounded_down_only() -> None:
    """占比条：格子数 = `pct * 10 // 100`（只看格子，真正的数字仍是旁边那列）。"""
    assert render_query.pct_bar(88) == "■" * 8 + "□" * 2
    assert render_query.pct_bar(12) == "■" * 1 + "□" * 9
    assert render_query.pct_bar(0) == "□" * 10
    assert len(render_query.pct_bar(100)) == render_query.BAR_CELLS


def test_spending_without_a_comparable_previous_period_says_so() -> None:
    """上期无支出：`vs_prev_pct` 为 None → 自己的句子，**绝不印「环比 None%」**（老口径的 bug）。"""
    reply = render_query.render_spending({**SPEND_FACTS, "vs_prev_pct": None})
    assert "None" not in reply
    assert "上一账期没有可比的支出" in reply


def test_spending_empty_names_the_period() -> None:
    """空账期：要点明**哪个月**没支出（否则用户不知查的是哪期，也就无从判断对不对）。"""
    reply = render_query.render_spending({**SPEND_FACTS, "groups": []})
    assert "2026-08" in reply and "没有支出记录" in reply
    assert "%" not in reply                       # 没有可比上期 → 一个百分比都不印
