"""查看白名单（T20）网页端**卡片**渲染测试 —— 真实 `AppTest` 跑 `interfaces/web/app.py`。

两条守卫：
1. **卡片真的画出来了**：回执被解析成卡片结构（`class="payee-card"`、姓名 / 脱敏手机号 / 徽章都在），
   且**裸 Markdown 表格不再上屏**（否则等于没画）；
2. **美化不许吞回执**：回执不是那张表（例如零收款人的独立句子）时，`render_payee_card` 必须返回
   `False`，调用方据此回退纯文本 —— 免得以后改列名/改格式时页面直接空白。

为什么卡片在**网页层**解析回执而不是拿结构化数据：`interfaces/` 只能调 `agent/`，`Turn` 不携带
facts；仓内先例是 `components.parse_bill`（回执文本 → 绘图坐标）。卡片里的每个字都照抄回执，
不重算、不补数（铁律 2）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm
from agent.render_lists import render_payees

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _load_ui_module(name: str):
    """按文件路径加载界面模块（`interfaces/` 没有 `__init__.py`，与 test_web_layering 同法）。

    先登记进 `sys.modules`：不登记的话模块内的 `dataclass` 之类会找不到自己的模块名。
    """
    path = REPO / "interfaces" / "web" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ui_{name}", path)
    assert spec is not None and spec.loader is not None, f"加载不到界面模块：{path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


payee_ui = _load_ui_module("payee_card_ui")

#: 直接喂渲染层的事实包（形状照抄 `tools/payee.py:list_payees` 的 facts）
FACTS = {"total_count": 2, "whitelist_count": 1, "non_whitelist_count": 1,
         "items": [{"payee_id": "payee_0003", "name": "王五", "masked_phone": "137****2003",
                    "bank": "建设银行", "is_whitelist": True},
                   {"payee_id": "payee_0004", "name": "张小美", "masked_phone": "136****3004",
                    "bank": "中国银行", "is_whitelist": False}]}


# ---------------- ① 解析：只认那张表 ----------------

def test_parse_picks_up_every_row_of_the_real_renderer() -> None:
    """真渲染函数的输出 → 卡片；**一行不少、一格不错位**（含 ✅ 与非白名单的区分）。"""
    card = payee_ui.parse_payee_reply(render_payees(FACTS))
    assert card is not None
    assert card.rows == (("王五", "137****2003", "建设银行", "✅"),
                         ("张小美", "136****3004", "中国银行", "—"))
    assert card.head.startswith("您共有 2 位收款人")
    assert card.foot                                    # 表下那行说明要带进卡片页脚


def test_parse_returns_none_for_anything_that_is_not_the_table() -> None:
    """零收款人的独立句子 / 空串 / 列名不符 / 缺分隔行 → `None`（调用方回退纯文本）。"""
    assert payee_ui.parse_payee_reply("您还没有收款人。可以说「我要加个收款人」来添加。") is None
    assert payee_ui.parse_payee_reply("") is None
    assert payee_ui.parse_payee_reply("| 谁 | 号码 |\n| --- | --- |\n| 李四 | 1 |") is None
    assert payee_ui.parse_payee_reply("| 收款人 | 手机号 | 银行 | 白名单 |\n| 李四 | 139****1001 |") is None


def test_render_reports_false_when_it_cannot_draw() -> None:
    """降级契约：画不出来必须如实返回 `False`（这一句会被调用方当纯文本渲染）。"""
    assert payee_ui.render_payee_card("您还没有收款人。") is False


# ---------------- ② 端到端：AppTest 真跑 app.py ----------------

def _app(monkeypatch: pytest.MonkeyPatch, verdict: dict) -> AppTest:
    """临时合成库 + 假分类器（`payee_list` 无槽位，不需要时钟）。"""
    path = demo.build_temp_db()
    monkeypatch.setenv("DB_PATH", str(path))

    #: **在 `classify` 这一层打桩**，而不是只桩 `llm.chat_json`。
    #: 只桩 LLM 的话，UI 用例的意图还依赖「分类管道能不能走到那个桩」——
    #: 2026-10-30 实测：全量跑时约 1/3 概率分类失败 → 兜底 `out_of_scope` → 用例红，
    #: 单独跑必过。UI 用例本来就不该考分类管道，桩在入口最稳。
    def classify(text: str, history: list[str] | None = None):
        return classifier.IntentOut.model_validate(verdict)

    monkeypatch.setattr(classifier, "classify", classify)

    #: 回执润色（`agent/templates` 的 `_Polished`）仍走 LLM 桩并抛异常 → 回退静态文案，
    #: 免得 UI 用例真的去调模型（慢且不确定）。
    def chat_json(system: str, user: object, schema):
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    at = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=240)
    at.run()
    return at


def _markdown(at: AppTest) -> str:
    return "\n".join(str(element.value) for element in at.markdown)


def test_whitelist_reply_is_drawn_as_an_interactive_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-10-28 起：问收款人**不再画那张静态卡**，改由下方可交互列表（▸ 展开）接管。

    历史：这条原本断言「回执要画成 `payee-card`」。用户看到同一份数据出现两遍
    （聊天里一张静态卡 + 下方一张可点列表），要求只保留可交互的那张。
    """
    at = _app(monkeypatch, {"intent": "payee_list", "confidence": 0.95,
                            "slots": {}, "missing_slots": []})
    at.chat_input[0].set_value("我的白名单有哪些").run()
    assert not at.exception, at.exception
    page = _markdown(at)
    assert at.session_state['workspace-result']['scene'] == 'payee_list'
    assert 'class="payee-card"' not in page
    assert "| 收款人 | 手机号 | 银行 | 白名单 |" not in page
    row_buttons = [button for button in at.button if "****" in button.label]
    assert len(row_buttons) == 5
    assert "139****1001" in "\n".join(button.label for button in row_buttons)
    assert "trace_id" not in "\n".join(str(e.value) for e in at.caption)
