"""网页端**追问续接**的回归守卫：列出两个李四后回「1001」必须能解析出收款人。

**为什么单独一个文件**（这是用户实机报的第 3 个回归，前两个是确认卡崩溃、定时转账降级）：

    `interfaces/web/app.py` 把追问续接的上下文传成了**模块常量 `HISTORY`**，而它
    **恒为 `None`**（全项目没有第二处赋值 —— `grep HISTORY` 只有定义与读取两行）。

    于是「给李四转100元 → 界面列出两个李四 → 用户回 1001」这一步，分类器只看到一个
    光秃秃的数字 `1001` → 判成 `smalltalk` → **收款人永远解析不出来**。

    修法：每轮从真实会话记录里取最近几轮（用户话 + 助手回执）作为 history 传给分类器；
    那句「列出两个李四」的候选列表因此在第 2 次分类时可见，`1001` 能被解析成
    `payee='李四（139****1001）'`。

**本用例钉的是根因**（分类器是否收到了追问上下文），而不是只断言最终文案 ——
否则「文案对了但上下文其实没传」也能过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def _markdown(at: AppTest) -> str:
    return "\n".join(str(element.value) for element in at.markdown)


def test_clarify_followup_carries_history_so_a_bare_number_resolves_the_payee(
        monkeypatch: pytest.MonkeyPatch, clock: object) -> None:
    """回归（用户实机报）：追问后回「1001」，分类器必须收到那句候选列表。"""
    seen: list[str] = []
    path = demo.build_temp_db()
    monkeypatch.setenv("DB_PATH", str(path))
    verdicts = [
        {"intent": "transfer_single", "confidence": 0.95,
         "slots": {"payee": "李四", "amount": "100.00"}},
        {"intent": "transfer_single", "confidence": 0.95,
         "slots": {"payee": "李四（139****1001）", "amount": "100.00"}},
    ]

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            seen.append(str(user))                                # 记下分类器实际看到的 user 消息
            return schema.model_validate(verdicts[min(len(seen) - 1, len(verdicts) - 1)])
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    at = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=240)
    at.run()
    at.chat_input[0].set_value("给李四转 100 元").run()
    assert not at.exception, at.exception
    assert "139****1002" in _markdown(at)                          # 第 1 轮确实列出了两个李四
    at.chat_input[0].set_value("1001").run()
    assert not at.exception, at.exception
    # ← 根因判据：第 2 次分类必须能看到第 1 轮的追问内容
    assert len(seen) >= 2
    assert "139****1001" in seen[1], f"追问上下文没传给分类器：{seen[1]!r}"
    # 且第 2 轮走到了确认卡（收款人是 1001 那个李四）
    assert "转账确认卡" in _markdown(at)
    assert "139****1001" in _markdown(at)
