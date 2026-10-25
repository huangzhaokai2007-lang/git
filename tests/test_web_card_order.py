"""排版回归：页面渲染顺序必须是「解析卡 → 表单 → 各查询卡 → **确认卡** → 聊天框」。

用户实测过：「查看收款人后改备注，修改备注确认卡出现在聊天框**上方**」——根因是
`_render_confirm()` 排在收款人卡之前。确认卡属于「当前这一步要你确认的东西」，
必须贴着聊天输入框（页面最下方），否则看起来像上一条消息的回执。

用**源码顺序断言**（比 AppTest 稳、快）：读 `interfaces/web/app.py` 的 `_chat_page()` 函数体，
取各渲染调用的行号，钉住相对次序。改坏顺序这条就红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "interfaces" / "web" / "app.py"

#: 从上到下的期望顺序（只列有先后要求的）
EXPECTED = ("payee_picker_ui.render", "payee_ui.render", "schedule_card_ui.render",
            "aa_card_ui.render", "gift_card_ui.render", "_render_confirm()")


def _chat_page_body() -> str:
    """取 `def _chat_page(...)` 到下一个顶层 `def ` 之间的源码。"""
    source = APP.read_text(encoding="utf-8")
    start = source.index("def _chat_page(")
    rest = source[start + 1:]
    end = rest.index("\ndef ")
    return rest[:end]


def _line_of(body: str, needle: str) -> int:
    pattern = re.compile(r"^\s*" + re.escape(needle), re.MULTILINE)
    match = pattern.search(body)
    if match is None:
        raise AssertionError(f"_chat_page() 里找不到渲染调用：{needle}")
    return body[: match.start()].count("\n")


@pytest.mark.parametrize("first,second", list(zip(EXPECTED, EXPECTED[1:])))
def test_render_order(first: str, second: str) -> None:
    """前者必须排在后者之前（`_render_confirm` 是最后一个）。"""
    body = _chat_page_body()
    assert _line_of(body, first) < _line_of(body, second), (
        f"渲染顺序不对：{first} 应排在 {second} 之前")


def test_confirm_card_is_the_last_card_on_the_chat_page() -> None:
    """确认卡之后只剩聊天输入框（不能再有别的卡片）。"""
    body = _chat_page_body()
    tail = body.splitlines()[_line_of(body, "_render_confirm()") + 1:]
    offenders = [line.strip() for line in tail if ".render(" in line]
    assert not offenders, f"确认卡之后还渲染了别的卡片：{offenders}"
    assert any("chat_input" in line for line in tail), "确认卡之后应当紧跟聊天输入框"
