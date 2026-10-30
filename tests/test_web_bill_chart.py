"""卡 25 第 2 刀：**账单图表页改读 `Turn.facts`**，不再从回执文案里正则反解数字。

为什么要钉这个（此前踩过、也最容易再踩）：
- 老实现 `components.parse_bill` 是**拿正则去扫给人看的回执文案**（`「2026-08 账单：支出 9,152.00 元」`
  这种整句）。回执措辞一变（「共支出」→「支出合计」、多一句客套、换个单位）图表就**静默变空**，
  而且没有任何报错 —— 演示时看着像"这功能坏了"，实际只是文案对不上；
- 编排层已经把同一批数字放进 `Turn.facts`，且这些数字**过过数字校验器**。界面读它是零解析、
  零漂移，也符合铁律 2（界面不派生数字）。

本文件的断言分两层：
1. 单元层：`bill_from_turn` 认两种账单意图、其它意图/空 facts 一律返回 `{}`；
2. 端到端：真实 `AppTest` 跑起页面 → 问一句账单总结 → 切到「📊 账单图表」页 → 图表数量增加。

`parse_bill` 保留为兜底（老会话没有 `turn` 字段），所以第 3 条用例同时守住这条向后兼容通路。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm
from interfaces.web import components as ui

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402

REPORT_FACTS = {
    "period": "2026-08",
    "out_sum_yuan": "9,152.00",
    "groups": [{"key": "餐饮", "amount_yuan": "3,000.00", "pct": 33},
               {"key": "交通", "amount_yuan": "1,152.00", "pct": 13}],
}

ANALYSIS_FACTS = {
    "period": "2026-08",
    "total_yuan": "9,152.00",
    "groups": [{"key": "餐饮", "amount_yuan": "3,000.00", "pct": 100}],
}


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """临时合成库 + 关键词替身分类器（只替「理解意图」这一步，工具层与渲染全真）。"""
    monkeypatch.setenv("DB_PATH", str(demo.build_temp_db()))

    def chat_json(system: str, user: object, schema):
        if schema is not classifier.IntentOut:
            raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")
        text = str(user[-1]["content"]) if isinstance(user, list) and user else str(user)
        if "账单总结" in text or "账单报告" in text:
            return schema(intent="bill_report", confidence=0.95, missing_slots=[],
                          slots={"period": "上个月", "kind": "monthly"})
        if "花了多少" in text:
            return schema(intent="bill_analysis", confidence=0.95, missing_slots=[],
                          slots={"period": "上个月"})
        return schema(intent="out_of_scope", confidence=0.0, slots={}, missing_slots=[])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    at = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=240)
    at.run()
    return at


def test_report_facts_feed_the_chart() -> None:
    """F24 账单总结：账期取 `period`、柱高取 `out_sum_yuan`、分类取 `groups[]`。"""
    parsed = ui.bill_from_turn({"intent": "bill_report", "facts": REPORT_FACTS})
    assert parsed["period"] == "2026-08"
    assert parsed["total"] == 9152.0                       # 去千分位，数值不变
    assert [item["key"] for item in parsed["categories"]] == ["餐饮", "交通"]
    assert parsed["categories"][0]["pct"] == 33


def test_analysis_facts_feed_the_chart() -> None:
    """F14 账单分析：合计取 `total_yuan`（与账单总结的 `out_sum_yuan` 不同键，别串）。"""
    parsed = ui.bill_from_turn({"intent": "bill_analysis", "facts": ANALYSIS_FACTS})
    assert parsed["total"] == 9152.0
    assert parsed["categories"][0]["amount"] == 3000.0


def test_unknown_intent_or_empty_facts_yield_nothing() -> None:
    """降级契约：没有事实包 → 一个点都不出（不画空图、不补零、不猜）。"""
    assert ui.bill_from_turn(None) == {}
    assert ui.bill_from_turn({}) == {}
    assert ui.bill_from_turn({"intent": "balance_query", "facts": REPORT_FACTS}) == {}
    assert ui.bill_from_turn({"intent": "bill_report", "facts": {}}) == {}


def test_series_prefers_facts_over_reply_text() -> None:
    """关键回归：**回执文案一个数字都没有**，图表照样出点 —— 证明不再依赖正则。"""
    messages = [{"role": "assistant", "text": "好的，已经帮你整理好了。",
                 "turn": {"intent": "bill_report", "facts": REPORT_FACTS}}]
    assert ui.bill_series(messages) == {"2026-08": 9152.0}
    assert ui.parse_bill(messages[0]["text"]) == {}        # 兜底通路确实是空的，点只能来自 facts


def test_series_still_falls_back_to_reply_text() -> None:
    """向后兼容：老会话没有 `turn`（例如历史存档）→ 仍按回执文案解析。"""
    messages = [{"role": "assistant", "text": "2026-08 账单：支出 9,152.00 元，收入 11,046.00 元。"}]
    assert ui.bill_series(messages) == {"2026-08": 9152.0}
    assert ui.latest_bill(messages)["period"] == "2026-08"


def test_chat_page_charts_the_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：问一句账单总结 → **先选时间段** → 聊天页内嵌图表出图；**抹掉回执文案里的数字，图还在**。

    前提变了（用户 2026-10-29 Comment 8）：账单报告不再「问一句就铺开」，而是先给时间段选择。
    所以第一步只断言**门关着**（有日期框、没图、正文被挡），第二步补上门槛（预置
    `bill-range`）才断言图表真上屏 —— 后面「抹掉回执文案」那一步的价值不变：老实现靠正则扫
    回执文案出坐标，文案一改图就静默变空；现在数字来自 `Turn.facts`，把 `text` 换成一句没有
    数字的话，图**一张不少**。
    """
    at = _app(monkeypatch)
    at.chat_input[0].set_value("上个月的账单总结").run()
    assert not at.exception, at.exception

    # —— 新流程的前置步骤：还没选时间段 → 只有选择区，报告与图表都不出 ——
    assert {box.label for box in at.date_input} >= {"开始日期", "结束日期"}
    assert not at.get("vega_lite_chart"), "没选时间段就先画图 = 旧的一步到底行为回来了"
    placeholders = "\n".join(str(element.value) for element in at.caption)
    assert "选好时间段后" in placeholders                     # 正文位置只剩一句占位提示
    assert not any(element.label == "📊 这份账单的图表" for element in at.expander)

    at.session_state["bill-range"] = ("2026-08-01", "2026-08-31")   # 选好时间段
    at.run()
    assert not at.exception, at.exception

    charts = len(at.get("vega_lite_chart"))
    assert charts == 0  # 独立的月度趋势图已删除，报告中的分类饼图继续保留。
    captions = "\n".join(str(element.value) for element in at.caption)
    assert not any('这份账单的图表' in element.label for element in at.expander)

    for message in at.session_state["messages"]:             # 抹掉所有回执正文（含全部数字）
        if message["role"] == "assistant":
            message["text"] = "好的，已经整理好了。"
    at.run()
    assert not at.exception, at.exception
    assert len(at.get("vega_lite_chart")) == charts          # 图还在 → 坐标不来自回执文案
