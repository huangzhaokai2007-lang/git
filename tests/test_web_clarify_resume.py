"""追问续接：**编排层契约**（不依赖 Streamlit 的会话/全局状态）。

用户实机报过：「给李四转 100 元 → 界面列出两个李四 → 回 1001」这一步，分类器只看到
一个光秃秃的 `1001` → 判成 smalltalk → 收款人永远解析不出来。

原来这条用 AppTest 端到端跑，全量跑时**偶发红**（单跑必绿；表现为第 1 轮就出了
某个"别人"的确认卡 —— 进程级全局串味）。现在改成直接打编排层：把上一轮回执作为
`history` 传进去，断言**分类器看到了候选列表**、并且第二轮解析成 1001 那位。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent import classifier, confirm_card, llm, orchestrator, payee_picker, session_memory
from agent import payee_resolve
from agent import pending_slots

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402

SESSION = "sess-clarify-resume"


@pytest.fixture(autouse=True)
def _hermetic(seeded, monkeypatch):
    """进程级全局一律清干净：解析记忆 / 在途确认 / 补槽 / 解析卡 / 会话记忆。"""
    monkeypatch.setenv("DB_PATH", str(demo.build_temp_db()))

    def _clean() -> None:
        confirm_card.reset_state()
        pending_slots.clear()
        payee_picker.clear_all()
        session_memory.reset()
        payee_resolve._PICKED.clear()

    _clean()
    yield
    _clean()


def test_clarify_followup_carries_history_so_a_bare_number_resolves_the_payee(
        monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    verdicts = [
        {"intent": "transfer_single", "confidence": 0.95,
         "slots": {"payee": "李四", "amount": "100.00"}},
        {"intent": "transfer_single", "confidence": 0.95,
         "slots": {"payee": "139****1001", "amount": "100.00"}},
    ]

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            seen.append(str(user))
            return schema.model_validate(verdicts[min(len(seen) - 1, len(verdicts) - 1)])
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)

    first = orchestrator.handle("给李四转 100 元", session_id=SESSION)
    assert first.missing_slots, "同名两位应判为缺槽（出解析卡/追问）"
    assert "139****1001" in first.reply and "139****1002" in first.reply, first.reply

    second = orchestrator.handle("1001", history=[first.reply], session_id=SESSION)
    assert len(seen) >= 2, "第二轮应当再分类一次"
    assert "139****1001" in seen[1], f"追问上下文没传给分类器：{seen[1]!r}"
    assert "139****1002" not in (second.reply or ""), "解析到了另一个李四"
    assert "确认卡" in second.reply or second.missing_slots == [], second.reply
