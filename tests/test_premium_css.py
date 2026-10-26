"""动效层（`interfaces/web/premium_css.py`）的守卫。

动效本身不可断言"好看"，但**可以**断言：
1. 关键动画都在（卡片入场 / 表格行 / 数字脉冲 / 管线条 / 进度条 / 按钮）；
2. 一定有 `prefers-reduced-motion` 兜底（无障碍，也是"专业"的一部分）；
3. `num_html()` 只做包装、**不产生新数字**（铁律 2 的展示侧延伸）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "interfaces" / "web"))

import premium_css                                                 # noqa: E402


def test_key_animations_are_present() -> None:
    css = premium_css.CSS
    for keyframe in ("dsh-rise", "dsh-row", "dsh-flash", "dsh-bar", "dsh-alert" if False else "dsh-slide",
                     "dsh-step"):
        assert f"@keyframes {keyframe}" in css, f"缺少动画：{keyframe}"


def test_targets_cards_tables_numbers_widgets() -> None:
    css = premium_css.CSS
    assert 'data-testid="stVerticalBlockBorderWrapper"' in css     # 卡片
    assert 'data-testid="stMarkdownContainer"' in css              # 表格
    assert 'data-testid="stMetricValue"' in css                    # 数字
    assert 'data-testid="stProgress"' in css                       # 进度条
    assert ".stButton > button" in css                             # 按钮
    assert 'data-testid="stExpander"' in css                       # 折叠面板


def test_reduced_motion_disables_everything() -> None:
    css = premium_css.CSS
    assert "prefers-reduced-motion" in css
    block = css[css.index("prefers-reduced-motion"):]
    assert "animation: none" in block and "transition: none" in block


def test_num_html_wraps_without_inventing_digits() -> None:
    """`num_html` 只能把**已有**文本包起来，不能引入新数字。"""
    wrapped = premium_css.num_html("1,000.00")
    assert "1,000.00" in wrapped and wrapped.count("1") == 1
    assert "dsh-num" in wrapped
    assert "dsh-flash" in premium_css.num_html("1,000.00", flash=True)
    assert re.sub(r"<[^>]+>", "", premium_css.num_html("300 元")) == "300 元"
