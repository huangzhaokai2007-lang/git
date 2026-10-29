"""F03 异常详情卡（`interfaces/web/anomaly_card_ui`）：分档、结论句、沿期追问。

卡片在 2026-10-27 从 `insight_card_ui` 迁出并升级 —— 这里守三件事，
都是**上屏才看得出来**的（返回值对了不代表用户看得懂）：
1. **分档**按工具层冻结的规则名归类（措辞一改就悄悄失效的那一类缺陷）；
2. **结论句随构成变化**：一类独大说「集中」，多类并存说「分散、以谁为主」；
3. **追问按钮跟着时间段走**：问四月 → 按钮查四月，而不是永远跳回「上个月」。

单元部分直接调纯函数（分档与结论都不需要 Streamlit 运行时）；
最后一条端到端用 `AppTest` 真跑一遍页面 —— 卡片是画在气泡里的，
"页面上有图和按钮"才是验收口径（照 `test_web_insight_card` 的先例）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _load_ui_module(name: str):
    """按文件路径加载界面模块（`interfaces/` 没有 `__init__.py`，与 test_web_insight_card 同法）。"""
    path = REPO / "interfaces" / "web" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ui_{name}", path)
    assert spec is not None and spec.loader is not None, f"加载不到界面模块：{path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


card = _load_ui_module("anomaly_card_ui")


def _items(*reasons: str) -> list[dict]:
    """造几条命中项：只有**依据**影响分档，其余字段随手给（不进断言）。"""
    return [{"reason": reason, "severity": "medium", "ts": "2026-06-15T20:40:00",
             "counterparty": "某商户", "amount_yuan": "-100.00"} for reason in reasons]


def test_kinds_group_every_rule_under_its_own_label() -> None:
    """四类依据各归各档 —— 顺序照卡片展示顺序（金额 / 时间 / 密集 / 陌生）。"""
    assert card.kinds(_items("金额显著高于近期均值", "凌晨时段交易", "陌生商户交易")) == [
        ("金额偏高", 1), ("时间异常", 1), ("陌生商户", 1)]


def test_one_transaction_hitting_two_rules_counts_once_in_each_kind() -> None:
    """一笔同时命中两档 → **两档各记一条**（这就是"各档之和可多于命中笔数"的来历）。"""
    assert card.kinds(_items("金额显著高于近期均值、凌晨时段交易")) == [
        ("金额偏高", 1), ("时间异常", 1)]


def test_kinds_stay_silent_about_rules_that_never_hit() -> None:
    """没命中的档**不补零** —— 卡片上不该出现「短时密集 0 条」这种占位。"""
    assert card.kinds(_items("金额显著高于近期均值")) == [("金额偏高", 1)]


def test_an_unknown_reason_is_not_counted_as_anything() -> None:
    """认不出的依据**不猜**：宁可少一档，也不许算进任何一类（unknown 也不许漏）..."""
    assert card.kinds(_items("某种没见过的规则")) == []


def test_summary_says_concentrated_when_one_kind_stands_alone() -> None:
    """只有一类依据 → 说「集中在这一处」（多类时那句"分散"就不该出现）。"""
    text = card.summary([("金额偏高", 1)], 1)

    assert "集中" in text
    assert "分散" not in text


def test_summary_names_the_main_kind_when_risks_spread() -> None:
    """多类并存 → 说清**几类**、点名**主因**是哪一类。"""
    text = card.summary([("金额偏高", 5), ("短时密集", 3), ("陌生商户", 1)], 5)

    assert "3 类" in text and "以「金额偏高」为主" in text
    assert "集中" not in text


def test_summary_says_nothing_when_nothing_was_flagged() -> None:
    """没有命中就不说结论 —— 卡片不许印「本期 0 笔可疑交易」这种废话。"""
    assert card.summary([], 0) == ""


def test_follow_ups_keep_the_period_the_user_just_asked_about() -> None:
    """问四月 → 按钮送出的是四月的话（不是永远的「上个月」）。"""
    assert card.follow_ups({"period": "2026-04"})[1:] == (
        ("📄 看这段时间的流水", "2026-04的流水"),
        ("🧾 这段时间的账单总结", "2026-04的账单总结"))


def test_follow_ups_fall_back_when_the_period_is_missing() -> None:
    """facts 里没有 `period` 时**不猜用户在问哪段** —— 退回固定那三问。"""
    ups = card.follow_ups({})

    assert ups == card._FALLBACK_FOLLOW_UPS
    assert "上个月的流水" in [prompt for _, prompt in ups]


def test_card_draws_the_kind_chart_and_conclusion_on_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：问四月的异常 → 页面真的多出一张依据分布图，并印出结论句与沿期按钮。"""
    monkeypatch.setenv("DB_PATH", str(demo.build_temp_db()))
    at = AppTest.from_file(str(REPO / "interfaces" / "web" / "app_offline.py"), default_timeout=90)
    at.run()
    before = len(at.get("vega_lite_chart"))
    at.chat_input[0].set_value("四月份的异常分析").run()

    assert len(at.get("vega_lite_chart")) > before, "依据分布图没上屏"
    body = "\n".join(item.value for item in at.markdown)
    assert ("依据全部指向" in body or "风险分散在" in body), "卡片没给出结论句"
    assert "📄 看这段时间的流水" in [button.label for button in at.button], "追问按钮没跟着时间段走"
