"""气泡只留**头句**：明细归卡片，气泡不再铺一遍（`components._head_only`）。

为什么单独守这一处：截断条件已经因为实机效果改过两轮 ——
第一轮只挡表格行，异常清单（「头句 + 每条一行 `- …`」）整份留在气泡里；
第二轮发现卡片底部的「**接着问：**」小标题也被留成了残句。
两轮都是「代码看着对、上屏才发现」的问题，所以把上屏时看到的真实形态固化成断言：
气泡里**只剩一句完整的话**，不出现任何明细、列表项或悬空冒号。

`_head_only` 是纯函数（输入回执文本、输出气泡文本），不需要起 Streamlit 运行时。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_ui_module(name: str):
    """按文件路径加载界面模块（`interfaces/` 没有 `__init__.py`，与 test_web_insight_card 同法）。"""
    path = REPO / "interfaces" / "web" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ui_{name}", path)
    assert spec is not None and spec.loader is not None, f"加载不到界面模块：{path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


components = _load_ui_module("components")


def test_head_only_stops_at_anomaly_item_lines() -> None:
    """异常回执「头句 + 逐笔 `- …`」：气泡只留头句，逐笔清单交给卡片表格。"""
    reply = ("已扫描 52 笔交易，检测到 2 笔异常交易。\n"
             "- 2026-06-15T20:40:00｜信用卡还款｜金额 -1,993.00 元 —— 金额显著高于近期均值（关注）\n"
             "- 2026-06-03T02:10:00｜便利店｜金额 -18.00 元 —— 凌晨时段交易（留意）")

    assert components._head_only(reply) == "已扫描 52 笔交易，检测到 2 笔异常交易。"


def test_head_only_stops_at_a_bold_heading() -> None:
    """卡片里的「**接着问：**」小标题归卡片，不许留在气泡末尾当残句。"""
    reply = "已扫描 49 笔交易，检测到 1 笔异常交易。\n**接着问：**"

    assert components._head_only(reply) == "已扫描 49 笔交易，检测到 1 笔异常交易。"


def test_head_only_stops_at_a_table() -> None:
    """流水回执的 Markdown 表格行：气泡不再铺一遍（表格由卡片呈现）。"""
    reply = ("共查到 2 笔交易：\n"
             "| 时间 | 交易对手 | 金额（元） |\n"
             "| --- | --- | --- |\n"
             "| 2026-08-01 12:00 | 便利店 | -18.00 |")

    assert components._head_only(reply) == "共查到 2 笔交易："


def test_head_only_keeps_a_single_paragraph_intact() -> None:
    """本来就只有一句话的回执**原样返回** —— 截断不能把正常回执也削掉。"""
    reply = "您名下目前没有卡片。"

    assert components._head_only(reply) == reply


def test_anomaly_head_does_not_end_with_a_dangling_colon() -> None:
    """头句以句号收尾：气泡里只有这一句时，冒号会让它看起来像没加载完。"""
    from agent import render_lists

    head = render_lists.T_ANOMALY_HEAD.format(scanned_count=49, anomaly_count=1)

    assert head.endswith("。"), f"头句不该以冒号结尾：{head!r}"
