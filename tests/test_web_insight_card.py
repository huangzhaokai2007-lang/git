"""卡 25 网页端到端：**对话气泡里真的有图和按钮**（真实 `AppTest` 跑 `interfaces/web/app.py`）。

验收口径是「在对话框里看到内容」，所以断言必须落在页面上，而不是落在函数返回值上：
1. **图上屏**：问完账单分析，页面上的图表比问之前**多出**至少一张（`st.bar_chart`）；
2. **卡片真的画了**：卡片那行标记说明（`insight_card_ui.CARD_MARK`）出现在页面上 —— 比数图表稳，
   因为页面别处本来也可能有图；
3. **按钮真跑流水线**：点「看流水明细」→ 页面出现 `txn_query` 的回执（共查到 N 笔交易）——
   只画个按钮、点了没反应，等于没做；
4. **降级**：没有事实包的回执**不许**画卡片（宁可朴素，也不许拿半截数据凑图）。

分类器用替身（与 `test_web_payee_card` 同法），工具层 / 权限 / 数字校验 / 渲染全是真链路。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _load_ui_module(name: str):
    """按文件路径加载界面模块（`interfaces/` 没有 `__init__.py`，与 test_web_payee_card 同法）。"""
    path = REPO / "interfaces" / "web" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ui_{name}", path)
    assert spec is not None and spec.loader is not None, f"加载不到界面模块：{path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


insight_ui = _load_ui_module("insight_card_ui")


def _app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """临时合成库 + 关键词替身分类器（只替「理解意图」这一步，其余全真）。"""
    monkeypatch.setenv("DB_PATH", str(demo.build_temp_db()))

    def chat_json(system: str, user: object, schema):
        if schema is not classifier.IntentOut:
            raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")
        #: `user` 是**多轮消息列表**（`classifier.build_messages`），必须只取最后一条 user 的正文：
        #: 拿 `str(user)` 去匹配会把历史回执里的字也匹配进来（问天气答账单的元凶）。
        text = str(user[-1]["content"]) if isinstance(user, list) and user else str(user)
        if "流水" in text:                       # 「上个月的流水」= 回执卡片上的追问按钮
            return schema(intent="txn_query", confidence=0.95, missing_slots=[],
                          slots={"date_from": "2026-08-01", "date_to": "2026-08-31"})
        if "余额" in text:
            return schema(intent="balance_query", confidence=0.95, missing_slots=[], slots={})
        if "异常" in text:
            return schema(intent="anomaly_check", confidence=0.95, missing_slots=[],
                          slots={"period": "上个月"})
        if "账单总结" in text:
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


def _text(at: AppTest) -> str:
    return "\n".join(str(element.value) for element in (*at.markdown, *at.caption))


def test_analysis_reply_comes_with_a_chart_and_follow_up_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    """账单分析 → 回执（分类占比表）+ 图表 + 三个追问按钮，全在对话流里。"""
    at = _app(monkeypatch)
    at.chat_input[0].set_value("上个月花了多少").run()
    assert not at.exception, at.exception

    page = _text(at)
    assert "| 分类 | 金额（元） | 占比 |" not in page              # 分类表替换为动画饼图
    assert insight_ui.CARD_MARK in page                              # ② 卡片真的画出来了
    pies = [item for item in at.get('bidi_component') if item.proto.component_name.endswith('spending_pie')]
    assert len(pies) == 1                                            # ③ 分类只画一份饼图
    labels = [button.label for button in at.button]
    assert "📄 看流水明细" in labels                                  # ④ 追问按钮在页面上
    assert "🧾 出一份账单总结" in labels
    assert "🔍 查异常交易" in labels


def test_follow_up_button_runs_the_same_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """点按钮 = 替用户把话说一遍：走**同一个** orchestrator 入口，不是界面自己拼一句回执。"""
    at = _app(monkeypatch)
    at.chat_input[0].set_value("上个月花了多少").run()
    assert not at.exception, at.exception

    button = next((b for b in at.button if b.label == "📄 看流水明细"), None)
    assert button is not None, "追问按钮没画出来 —— 卡片没生效"
    button.click().run()
    assert not at.exception, at.exception

    page = _text(at)
    assert "共查到" in page and "笔交易" in page                     # 真跑了 list_txn
    #: 2026-10-26 二次验收：气泡只留头句，走势图与逐笔明细交给卡片（同一批数不印两遍）
    assert insight_ui.CARD_MARK in page
    assert "按日支出走势" in page and "逐笔明细" in page
    assert "| 时间 | 交易对手 |" not in page


def test_five_chat_queries_walk_all_five_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """五种查询依次从聊天框输入，每句都真正调用工具。"""
    at = _app(monkeypatch)
    for prompt in ("查一下余额", "上个月的流水", "上个月花了多少", "上个月有没有异常交易", "上个月的账单总结"):
        at.chat_input[0].set_value(prompt).run()
    assert not at.exception, at.exception
    intents = [record["turn"]["intent"] for record in at.session_state["turns"]]
    assert intents == ["balance_query", "txn_query", "bill_analysis", "anomaly_check", "bill_report"]
    assert all(record["turn"]["tool_calls"] for record in at.session_state["turns"])   # 每句都真调了工具


def test_card_offers_copy_and_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """卡片底部的导出/复制：回执原文（Markdown）+ 明细表（CSV），做 PPT 时直接拿数字。

    断言的是**下载按钮真的在页面上**（`AppTest` 的 `get("download_button")`），不是"函数能拼出串"；
    CSV 的每一行都只是 `facts` 原值搬运（见 `insight_card_ui._table`，界面不重算金额）。
    """
    at = _app(monkeypatch)
    at.chat_input[0].set_value("上个月的流水").run()
    assert not at.exception, at.exception

    buttons = [button.label for button in at.get("download_button")]
    assert "下载回执（Markdown）" in buttons
    assert "下载流水明细（CSV）" in buttons


def test_no_facts_means_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """降级契约：没有事实包的回执（未接通 / 没调工具）**不许**画图表或追问按钮。"""
    at = _app(monkeypatch)
    baseline = len(at.get("vega_lite_chart"))
    at.chat_input[0].set_value("今天天气怎么样").run()
    assert not at.exception, at.exception
    page = _text(at)
    assert "意图 `out_of_scope`" in page, [                  # 先确认这一问确实没接通（不是测错了对象）
        (m["role"], (m.get("turn") or {}).get("intent")) for m in at.session_state["messages"]]
    assert insight_ui.CARD_MARK not in page
    assert len(at.get("vega_lite_chart")) == baseline
    assert "📄 看流水明细" not in [button.label for button in at.button]
