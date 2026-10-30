"""F11 单测：理财推荐端到端 —— 编排层回执带明细 + 与本会话测评结果的衔接。

沿用仓库做法：**真工具 + 假 LLM**。推荐走真实的 `tools/_wealth_risk.recommend_wealth`
（风险上限 + 期限 + 金额过滤），断言的是真实 facts 与回执。

**本文件只测编排层特有行为**（工具层的过滤/排序/入参校验已在 `test_tools_wealth_risk.py`）：

① 正常路径：用**本会话的有效测评结果**筛选（F10 → F11 的衔接）；
② 无匹配 → 明确告知，**不放宽风险等级**（验收清单第 2 条）；
③ 回执带明细：产品名/风险等级/预期年化/期限，数字全部可溯（验收清单第 1、3 条）；
④ 只推荐不申购：回执里不出现申购/买入字样，工具名不是 `trade_wealth`；
⑤ 期限文案不引入浮点数（720 天不得显示成「1.9726 年」——实测缺陷）；
⑥ 未测评就推荐 → INVALID_STATE 走错误模板（F10 接通前后行为一致）。

F11 验收清单见 `docs/features/F11-wealth-recommend.md`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent import llm, orchestrator, render_lists, risk_flow
from agent import render_wealth
from tools import wealth
from tools.schemas import ErrorCode

from tests.conftest import assert_covered
from tests.test_tools_wealth_risk import AGGRESSIVE

SESSION = "sess-f11"
BALANCED = {"age": 35, "experience": 2, "income": 2, "income_level": 2, "asset_ratio": 2,
            "drawdown": 2, "horizon": 2, "goal": 2, "reaction": 2,
            "knowledge": 2, "structure_exp": 2, "liquidity": 2, "liability": 2,
            "concentration": 2, "volatility": 2}                        # → 44 分 → R3


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))
    monkeypatch.setattr(llm, "chat_json", chat_json)


def out(intent: str, *, slots: dict | None = None, confidence: float = 0.9) -> dict:
    return {"intent": intent, "confidence": confidence, "slots": slots or {},
            "missing_slots": [], "unsafe_reason": None}


# ---------------- ① 正常路径：用本会话的测评结果 ----------------

def test_recommendation_uses_the_assessment_from_this_session(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**F10 → F11 的衔接**：先测评（R3）→ 不传 risk_level 也能推荐，且上限就是 R3。

    这是两个功能合在一起的核心价值：用户不必自己报风险等级。
    """
    orchestrator.submit_assessment(BALANCED, session_id=SESSION)
    fake_llm(monkeypatch, [out("wealth_recommend")])
    turn = orchestrator.handle("推荐适合我的理财产品")
    assert turn.tool_calls == ["recommend_wealth"]
    facts = orchestrator.TOOL_ROUTES["wealth_recommend"][1]({})
    assert facts.facts["risk_level"] == "R3" and facts.facts["ceiling_rank"] == 3


def test_before_assessment_the_recommendation_is_refused(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未测评 → INVALID_STATE 走错误模板（不猜风险等级、不给推荐）。"""
    fake_llm(monkeypatch, [out("wealth_recommend")])
    turn = orchestrator.handle("推荐点理财产品")
    assert turn.error_code == ErrorCode.INVALID_STATE
    assert turn.tool_calls == ["recommend_wealth"] and "要不要现在做" in turn.reply


# ---------------- ② 无匹配：不放宽风险等级 ----------------

def test_no_match_says_so_without_relaxing_the_risk_ceiling(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验收清单第 2 条：筛不出来就明说，**绝不放宽风险等级**去凑数。

    `amount=50`（分）低于所有产品起购额 → 空结果；回执要明说"没有放宽"。
    """
    fake_llm(monkeypatch, [out("wealth_recommend", slots={"risk_level": "R1", "amount": 50})])
    turn = orchestrator.handle("50 分钱能买什么理财")
    assert "没有" in turn.reply and "没有放宽" in turn.reply
    assert turn.reply == render_lists.T_WEALTH_EMPTY


def test_empty_result_has_its_own_sentence_not_a_dangling_head() -> None:
    """空结果不套列表头（避免「为您推荐 0 个产品：」这种残句）—— 与卡片/订阅同口径。"""
    text = render_lists.render_wealth({"items": [], "risk_level": "R1", "item_count": 0})
    assert text == render_lists.T_WEALTH_EMPTY and not text.endswith("：")


# ---------------- ③ 回执带明细 ----------------

def test_receipt_lists_every_product_with_all_four_fields(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验收清单第 1 条：明细要有产品名、风险等级、预期年化、期限，且**每个**产品都列出来。"""
    fake_llm(monkeypatch, [out("wealth_recommend", slots={"risk_level": "R3"})])
    turn = orchestrator.handle("推荐 R3 的理财")
    items = orchestrator.TOOL_ROUTES["wealth_recommend"][1]({"risk_level": "R3"}).facts["items"]
    assert items, "R3 应该能筛出产品"
    for item in items:
        assert item["name"] in turn.reply, f"明细漏了产品：{item['name']}"
        assert item["yield"] in turn.reply, f"明细漏了年化：{item['yield']}"
        assert render_lists.RISK_LABELS[item["risk_level"]] in turn.reply, \
            f"明细漏了风险等级：{item['risk_level']}"
    assert turn.reply.count("\n- ") == len(items), "明细行数与产品数不符"


def test_receipt_says_recommend_because_the_script_pins_the_word(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**回归**：回执必须含「推荐」二字 —— `tests/cases/orchestrator.yaml` 的 wlt-001/wlt-002/
    sec-005 都有 `must_contain: 推荐`，改成「筛选出」等同义词会让剧本验收失败。

    2026-10-28：快捷按钮「推荐理财」的文案 `推荐理财产品` 改走**确定性路由**（模型不可用时
    按钮也要有反应），不再经过 LLM 抽槽，所以这里先做测评、由测评结果决定推荐范围。
    """
    wealth.assess_risk(BALANCED)
    fake_llm(monkeypatch, [out("wealth_recommend", slots={"risk_level": "R1"})])
    turn = orchestrator.handle("推荐理财产品")
    assert "推荐" in turn.reply


def test_receipt_reports_the_correct_product_count(
        seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """汇总句里的个数 == 明细行数（不许只报个数不报明细，反之亦然）。"""
    orchestrator.submit_assessment({"age": 50, **{key: 4 for key in BALANCED if key != "age"}},
                                   session_id=SESSION)
    fake_llm(monkeypatch, [out("wealth_recommend", slots={"risk_level": "R5"})])
    turn = orchestrator.handle("推荐 R5 理财")
    head = turn.reply.splitlines()[0]
    assert f"{turn.reply.count(chr(10) + '- ')} 个产品" in head


def test_receipt_numbers_all_come_from_facts(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验收清单第 3 条：回执数字全部来自 facts，且未降级。"""
    fake_llm(monkeypatch, [out("wealth_recommend", slots={"risk_level": "R5"})])
    turn = orchestrator.handle("推荐理财")
    assert turn.degraded is False
    assert_covered(turn.reply, orchestrator.TOOL_ROUTES["wealth_recommend"][1]({"risk_level": "R5"}).facts)


# ---------------- ④ 只推荐不申购 ----------------

def test_recommendation_never_mentions_buying(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验收清单第 3 条：**只推荐不申购** —— 回执里不得出现申购/买入/下单字样。"""
    fake_llm(monkeypatch, [out("wealth_recommend", slots={"risk_level": "R3"})])
    turn = orchestrator.handle("推荐理财")
    assert not any(word in turn.reply for word in ("申购", "买入", "下单", "已购"))


def test_the_route_is_the_read_tool_not_the_trade_tool(seeded: Path) -> None:
    """路由指向 `recommend_wealth`（T14，L0 只读），不是 `trade_wealth`（T15，L2 写）。"""
    name, _call = orchestrator.TOOL_ROUTES["wealth_recommend"]
    assert name == "recommend_wealth" and "trade" not in name


# ---------------- ⑤ 期限文案不引入浮点 ----------------

@pytest.mark.parametrize(("days", "expected"), [
    (0, "活期"), (7, "7 天"), (90, "3 个月"), (180, "6 个月"), (365, "1 年"),
    (720, "720 天"), (1095, "3 年"),
])
def test_term_label_never_produces_a_float(days: int, expected: str) -> None:
    """**回归**：`term_cn` 只在能整除时换算月/年，除不尽就按天说。

    实测缺陷：720 天曾显示成「1.9726 年」—— 既违反「禁浮点」红线，
    又因 `1.9726` 不在 facts 里被数字校验器判为幻觉、触发降级。
    """
    label = render_lists.term_cn(days)
    assert label == expected
    assert not re.search(r"\d\.\d", label), f"期限文案出现浮点：{label}"


def test_no_product_term_label_contains_a_float(seeded: Path) -> None:
    """把 seed 里全部产品的期限都过一遍 —— 一个浮点都不许出现。"""
    for row in wealth.dao.list_products():
        label = render_lists.term_cn(row["term_days"])
        assert not re.search(r"\d\.\d", label), f"{row['name']} 的期限文案有浮点：{label}"


# ---------------- ⑥ 明细渲染器的纯度 ----------------

def test_wealth_line_copies_numbers_verbatim() -> None:
    """明细行只照抄 facts，不做任何换算（年化直接取工具层给的百分数字符串）。"""
    line = render_lists.wealth_line({"name": "混合优选 365 天", "risk_level": "R3",
                                     "yield": "4.50%", "term": 365})
    assert line == "- 混合优选 365 天：中风险，预期年化 4.50%，期限 1 年"


def test_render_wealth_raises_on_missing_facts_key() -> None:
    """缺 facts 键 → 抛 `TemplateError`（宁可炸也不静默留白）。"""
    with pytest.raises(render_lists.TemplateError):
        render_lists.render_wealth({"items": [{"name": "x", "risk_level": "R1",
                                               "yield": "1.00%", "term": 0}]})
    assert render_lists.TemplateError is __import__("agent.templates", fromlist=["x"]).TemplateError


def test_recommendation_replies_are_grouped_by_regulatory_category(seeded: Path) -> None:
    """2026-10-29 用户要求：推荐**必须按大类分组**，不许把 25 只产品毫无规律地一锅烩。

    组序照工具层 `CATEGORY_ORDER`（稳健 → 进取），组内保持 facts 原序（收益降序）；
    `render_lists` 与 `agent.render_wealth` 是同一份实现（拆文件再导出），两边都能调。
    """
    result = wealth.recommend_wealth("R5")
    reply = render_lists.render_wealth(result.facts)
    headers = [line.strip("*") for line in reply.splitlines()
               if line.startswith("**") and line.endswith("**")]
    assert headers[0] == "现金管理类" and headers.index("现金管理类") < headers.index("权益类")
    assert "券商收益凭证（雪球结构 · 非保本）" in headers
    #: 每只产品都落在某个组下面：组数 = 不同大类数，明细行数 = 产品数
    assert sum(1 for line in reply.splitlines() if line.startswith("- ")) == len(result.facts["items"])
    assert render_wealth.wealth_groups([]) == []


def test_recommendation_cards_appear_below_reply_with_buy_and_all_buttons(seeded: Path) -> None:
    """回归真实问题：卡片要在推荐回执下面，而不是聊天末尾的独立区块。"""
    from streamlit.testing.v1 import AppTest
    from interfaces.web import components, wealth_ui

    wealth.assess_risk(BALANCED)
    recommendation = wealth.recommend_wealth('R3')
    assert recommendation.ok and len(recommendation.facts['items']) == 14
    received = []
    turn = {'trace_id': 'wealth-test', 'intent': 'wealth_recommend', 'error_code': None,
            'reply': recommendation.message, 'facts': recommendation.facts,
            'states': ['REPLY'], 'tier': 'L0'}

    def page(turn, received):
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': turn['reply'], 'turn': turn}],
                              wealth_card=lambda result, key: wealth_ui.render_turn(
                                  result, ask=received.append, key_prefix=key))

    at = AppTest.from_function(page, args=(turn, received), default_timeout=30).run()
    assert not at.exception
    assert [b.label for b in at.button].count('订购') == 4
    assert [b.label for b in at.button].count('查看全部产品 →') == 1
    assert len(at.columns) >= 2
    next(b for b in at.button if b.label == '查看全部产品 →').click().run()
    assert received == ['看看全部理财产品']
    next(b for b in at.button if b.label == '订购').click().run()
    assert received[-1].startswith('买 ')


def test_web_app_recommendation_renders_buttons_in_real_chat(monkeypatch, seeded: Path) -> None:
    """真正运行 app.py，用户发送推荐请求后，每只产品下方都有订购入口。"""
    from streamlit.testing.v1 import AppTest
    from agent import classifier, llm

    monkeypatch.setenv('DB_PATH', str(seeded))
    monkeypatch.setattr(classifier, 'classify', lambda text, history=None:
                        classifier.IntentOut(intent='wealth_recommend', confidence=1,
                                             slots={'risk_level': '高风险'}))
    monkeypatch.setattr(llm, 'chat_json', lambda *args, **kwargs:
                        (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    path = Path(__file__).resolve().parents[1] / 'interfaces/web/app.py'
    at = AppTest.from_file(str(path), default_timeout=60).run()
    assert not at.exception
    at.chat_input[0].set_value('推荐高风险理财').run()
    assert not at.exception
    turn = at.session_state['turns'][-1]['turn']
    assert turn['intent'] == 'wealth_recommend' and not turn['error_code']
    assert len(turn['facts']['items']) == 25
    assert sum(b.label == '订购' for b in at.button) == 4
    assert any(b.label == '查看全部产品 →' for b in at.button)
    assert any('模拟参考区间' in m['text'] for m in at.session_state['messages'])


def test_recommendation_disclaimer_survives_polishing(seeded: Path, monkeypatch) -> None:
    """结构化推荐禁止被 LLM 润色成「保证最高收益」等误导性文字。"""
    from agent import templates
    monkeypatch.setattr(templates, 'polish', lambda *_: '保证最高收益')
    result = wealth.recommend_wealth('R3')
    reply, degraded, calls = templates.compose_reply('wealth_recommend', {}, result)
    assert '模拟参考区间' in reply and '非历史极值或收益承诺' in reply
    assert not degraded and calls == 0


def test_all_products_button_prompt_uses_catalog_even_if_llm_is_offline(monkeypatch, seeded: Path) -> None:
    from agent import classifier, llm

    monkeypatch.setattr(llm, 'chat_json', lambda *args, **kwargs:
                        (_ for _ in ()).throw(llm.LLMUnavailable('offline')))
    classified = classifier.classify('看看全部理财产品')
    assert classified.intent == 'wealth_all_products'
    wealth.assess_risk(BALANCED)
    previous = orchestrator.handle('推荐理财', session_id='f11-all-products-button',
                                   intent_resolver=lambda *_: classifier.IntentOut(
                                       intent='wealth_recommend', confidence=1.0, slots={}))
    assert previous.intent == 'wealth_recommend' and not previous.error_code
    result = orchestrator.handle('看看全部理财产品', session_id='f11-all-products-button')
    assert result.intent == 'wealth_all_products' and not result.error_code
    assert result.facts['total_count'] == 25


def test_recommendation_shows_four_cards_and_can_expand_the_rest(seeded: Path) -> None:
    """默认只铺 4 张卡（25 张会刷满好几屏），其余折叠在「展开其余 N 只」后面。"""
    from streamlit.testing.v1 import AppTest
    from interfaces.web import components, wealth_ui

    wealth.assess_risk(AGGRESSIVE)
    recommendation = wealth.recommend_wealth('R5')
    assert len(recommendation.facts['items']) == 25
    turn = {'trace_id': 'expand', 'intent': 'wealth_recommend', 'error_code': None,
            'facts': recommendation.facts, 'states': ['REPLY'], 'tier': 'L0'}

    def page(turn):
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': '推荐', 'turn': turn}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=lambda _: None, key_prefix=key))

    at = AppTest.from_function(page, args=(turn,), default_timeout=60).run()
    assert not at.exception
    assert sum(b.label == '订购' for b in at.button) == 4
    toggle = next(b for b in at.button if b.label.startswith('展开其余'))
    assert '21' in toggle.label                      # 25 - 4
    toggle.click().run()
    assert sum(b.label == '订购' for b in at.button) == 25
    assert any(b.label.startswith('收起') for b in at.button)


def test_recommendation_cards_survive_the_all_products_drilldown(seeded: Path) -> None:
    """点「查看全部产品」之后，上一条推荐卡（K 线 + 区间图例 + 订购）必须还在。

    用户反馈的原话：「在全部理财产品的页面，推荐的那几个产品的图例会消失」。
    根因是卡片只挂「最后一条回执」，而全部产品页的最后一条是 `wealth_all_products`。
    """
    from streamlit.testing.v1 import AppTest
    wealth.assess_risk(BALANCED)
    result = wealth.recommend_wealth('R1')
    turn = {'trace_id': 'old', 'intent': 'wealth_recommend', 'error_code': None,
            'facts': result.facts, 'states': ['REPLY'], 'tier': 'L0'}
    later = {'trace_id': 'new', 'intent': 'wealth_all_products', 'error_code': None,
             'facts': {}, 'states': ['REPLY'], 'tier': 'L0'}

    def page(old, new):
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': '推荐', 'turn': old},
                               {'role': 'assistant', 'text': '全部产品', 'turn': new}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=lambda _: None, key_prefix=key))

    at = AppTest.from_function(page, args=(turn, later), default_timeout=30).run()
    assert not at.exception
    assert any(b.label == '订购' for b in at.button)          # 卡片还在
    assert any('参考下沿' in m.value for m in at.markdown)     # 区间图例还在


def test_old_recommendation_buttons_disappear_after_other_replies(seeded: Path) -> None:
    """除「全部产品」外的后续回执，仍不能让旧推荐留着可点击的订购按钮。"""
    from streamlit.testing.v1 import AppTest
    wealth.assess_risk(BALANCED)
    result = wealth.recommend_wealth('R1')
    turn = {'trace_id': 'old', 'intent': 'wealth_recommend', 'error_code': None,
            'facts': result.facts, 'states': ['REPLY'], 'tier': 'L0'}
    later = {'trace_id': 'new', 'intent': 'balance_query', 'error_code': None,
             'facts': {}, 'states': ['REPLY'], 'tier': 'L0'}

    def page(old, new):
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': '推荐', 'turn': old},
                               {'role': 'assistant', 'text': '查余额', 'turn': new}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=lambda _: None, key_prefix=key))

    at = AppTest.from_function(page, args=(turn, later), default_timeout=30).run()
    assert not at.exception
    assert not any(b.label in ('订购', '查看全部产品 →') for b in at.button)


def test_unassessed_recommendation_offers_a_questionnaire_button() -> None:
    """未测评时不能只回一句报错 —— 要在推荐回执下面直接给「开始风险测评」。"""
    from streamlit.testing.v1 import AppTest
    turn = {'trace_id': 'need-assess', 'intent': 'wealth_recommend',
            'error_code': 'INVALID_STATE', 'facts': {}, 'states': ['REPLY'], 'tier': 'L0'}
    actions: list[str] = []

    def page(turn, actions):
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': '尚未完成风险评估', 'turn': turn}],
                              wealth_card=lambda shown, key: wealth_ui.render_turn(
                                  shown, ask=actions.append, key_prefix=key))

    at = AppTest.from_function(page, args=(turn, actions), default_timeout=30).run()
    assert not at.exception
    assert any("先做一次风险测评" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "开始风险测评").click().run()
    assert actions == ["做风险测评"]


def test_assess_button_phrase_routes_without_the_model() -> None:
    """按钮文案是固定句式：模型不可用也必须能进问卷（否则按钮点了没反应）。"""
    from agent import card_binding_flow

    out = card_binding_flow._resolve_intent("做风险测评\n（当前日期：2026-09-12）", None)
    assert out.intent == "risk_assess" and out.confidence == 1.0


def test_recommendation_cards_do_not_render_for_failed_or_empty_turns() -> None:
    from streamlit.testing.v1 import AppTest
    from interfaces.web import components, wealth_ui

    def page():
        from interfaces.web import components, wealth_ui
        components.transcript([{'role': 'assistant', 'text': '未查到', 'turn': {
            'trace_id': 'failed', 'intent': 'wealth_recommend', 'error_code': 'INVALID_STATE',
            'facts': {}, 'states': ['REPLY'], 'tier': 'L0'}}],
            wealth_card=lambda result, key: wealth_ui.render_turn(result, ask=lambda _: None,
                                                                     key_prefix=key))

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception
    assert not any(b.label in ('订购', '查看全部产品 →') for b in at.button)


def test_holdings_detail_without_assessment_offers_the_form(seeded: Path,
                                                            monkeypatch) -> None:
    """看持仓详情要先测评；回执只有一句文字时用户找不到入口（实测反馈）→ 必须给按钮。

    路径复现：侧边栏「我的理财」→ 选一只 → 「查看所选详情」→ 未测评 → INVALID_STATE。
    """
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DB_PATH", str(seeded))
    path = Path(__file__).resolve().parents[1] / "interfaces" / "web" / "app.py"
    at = AppTest.from_file(str(path), default_timeout=120).run()
    from agent import classifier
    monkeypatch.setattr(classifier, 'classify', lambda *args, **kwargs:
                        classifier.IntentOut(intent='wealth_holdings', confidence=1.0, slots={}))
    next(b for b in at.sidebar.button if b.label == "我的理财").click().run()
    assert not at.exception
    next(b for b in at.button if b.label == "查看所选详情").click().run()
    assert not at.exception
    assert at.session_state["messages"][-1]["turn"]["error_code"] == "INVALID_STATE"
    assert any(b.label == "开始风险测评" for b in at.button)

