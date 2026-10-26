"""界面层新增能力（高级感三件套）的回归守卫。

**拆成两层来测**：
1. **纯函数层**（`pipeline_text` / `evidence_rows` / `trace_bits`）—— 内容对不对，直接断言，
   不需要 Streamlit 运行时；
2. **渲染层** —— 用真实 `AppTest` 跑一次首页与一次转账，只断言「不抛异常」。
   之所以不逐元素断言：`st.pills` / `st.expander` 在 1.63 的 AppTest 里没有稳定的取用接口，
   硬取会让用例变脆（前面已经因为「界面没人渲染」吃过 NameError 的亏，重点是**别炸**）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402

sys.path.insert(0, str(REPO / "interfaces" / "web"))
import premium_ui                                                 # noqa: E402


# ---------------- ① 纯函数层 ----------------

def test_pipeline_text_skips_idle_and_shows_tier() -> None:
    turn = SimpleNamespace(intent="transfer_single", tier="L1",
                           states=["IDLE", "CLASSIFY", "PRECHECK", "CONFIRM_CARD", "REPLY"])
    text = premium_ui.pipeline_text(turn)
    assert "transfer_single" in text and "档位 L1" in text
    assert "待命" not in text                      # IDLE 不该出现
    assert "理解意图" in text and "生成确认卡" in text


def test_pipeline_text_is_empty_without_states() -> None:
    assert premium_ui.pipeline_text(None) == ""
    assert premium_ui.pipeline_text(SimpleNamespace(states=["IDLE"])) == ""


def test_evidence_rows_reads_facts_snapshot() -> None:
    confirmation = SimpleNamespace(evidence={
        "limits_single_max_yuan": "500.00", "limits_daily_remaining_yuan": "2,000.00",
        "tier": "L2", "factors": ["night"], "irrelevant": "x"})
    rows = dict(premium_ui.evidence_rows(confirmation))
    assert rows["单笔转账上限"] == "500.00"
    assert rows["今日剩余额度"] == "2,000.00"
    assert rows["命中因子"] == "night"
    assert "irrelevant" not in " ".join(rows)      # 只渲染白名单里的键


def test_evidence_rows_empty_when_no_evidence() -> None:
    assert premium_ui.evidence_rows(SimpleNamespace(evidence={})) == []
    assert premium_ui.evidence_rows(SimpleNamespace()) == []


def test_trace_bits_reports_execution_and_error() -> None:
    done = premium_ui.trace_bits(SimpleNamespace(intent="transfer_single", tier="L1",
                                                 executed=True, error_code=None))
    assert "已执行" in done and "错误码" not in done
    failed = premium_ui.trace_bits(SimpleNamespace(intent="transfer_single", tier=None,
                                                   executed=False, error_code="FORBIDDEN"))
    assert "未执行" in failed and "FORBIDDEN" in failed


def test_examples_are_non_empty_and_distinct() -> None:
    assert len(premium_ui.EXAMPLES) >= 3
    assert len(set(premium_ui.EXAMPLES)) == len(premium_ui.EXAMPLES)


# ---------------- ② 渲染层：不抛异常 ----------------

def _app(monkeypatch: pytest.MonkeyPatch, verdict: dict) -> AppTest:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate(verdict)
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=240)
    app.run()
    return app


def test_home_page_renders_without_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """首页（含示例胶囊、CSS 注入）必须能渲染。"""
    app = _app(monkeypatch, {"intent": "balance_query", "confidence": 0.9,
                             "slots": {}, "missing_slots": []})
    assert not app.exception, app.exception


def test_transfer_turn_renders_without_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """一轮转账（含依据面板、管线条、可追溯页脚）必须能渲染。"""
    app = _app(monkeypatch, {"intent": "transfer_single", "confidence": 0.95,
                             "slots": {"payee": "王五", "amount": "100"}, "missing_slots": []})
    app.chat_input[0].set_value("给王五转 100 元").run()
    assert not app.exception, app.exception


def test_payee_card_transfer_button_actually_dispatches(monkeypatch) -> None:
    """用户实测 bug：点专属卡的「转账」**毫无反应**。

    根因：`payee_detail_ui._dispatch` 写的是 `last-turn` / `pending-*` 这些**不存在的会话键**，
    而界面读的是 `messages` / `turns`。这条用真实 AppTest 点一次按钮，
    断言会话里**真的多了一条回执**。
    """
    app = _app(monkeypatch, {"intent": "payee_list", "confidence": 0.95,
                             "slots": {}, "missing_slots": []})
    app.chat_input[0].set_value("我的收款人有哪些").run()
    assert not app.exception, app.exception
    before = len(app.session_state["messages"])
    next(button for button in app.button if button.label == "转账").click().run()
    assert not app.exception, app.exception
    assert len(app.session_state["messages"]) > before, "点「转账」后会话里没有新增回执"
