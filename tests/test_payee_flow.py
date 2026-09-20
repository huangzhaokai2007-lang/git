"""卡 20 单测：收款人自助添加的**编排层**行为（聊天触发 → 表单提交 → 可转账）。

盖四条：
① 聊天触发只给信号、**不写库**（界面靠 `turn.intent == "payee_add"` 弹表单）；
② 表单提交经 agent 层落库，回执只出现**脱敏**手机号，完整号不落库/不进审计；
③ 提交入口 `orchestrator.submit_payee` 是 agent 层的导出（界面不直连 tools/）；
④ 加完就能转账：新收款人 → **L2 确认卡**（姓名与脱敏号两种说法都命中）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import classifier, llm, orchestrator

from tests.conftest import count, raw
from tests.test_tools_payee import FULL, MASKED


def _stub(monkeypatch: pytest.MonkeyPatch, verdict: dict) -> None:
    """假 LLM：只回意图分类，其余调用一律不可用（回执退回模板原文）。"""
    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate(verdict)
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)


def _payee_add(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {"intent": "payee_add", "confidence": 0.95, "slots": {}})


# ---------------- ① 聊天触发：只给信号，不写库 ----------------

def test_chat_trigger_signals_the_form_without_writing(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _payee_add(monkeypatch)
    before = count(seeded, "payee")
    turn = orchestrator.handle("加个收款人", session_id="payee-chat")
    assert turn.intent == "payee_add"                       # 界面据此弹表单
    assert turn.tier == "L1" and turn.executed is False     # §5 定档；没执行任何写
    assert turn.tool_calls == [] and count(seeded, "payee") == before
    assert "表单" in turn.reply and "还没接通" not in turn.reply
    assert "PRECHECK" in turn.states and "EXECUTE" not in turn.states


# ---------------- ② 表单提交：落库 + 只出现脱敏号 ----------------

def test_submit_writes_the_row_and_masks_the_phone(seeded: Path) -> None:
    turn = orchestrator.submit_payee("王小明", FULL, session_id="payee-submit")
    assert (turn.intent, turn.tier, turn.executed) == ("payee_add", "L1", True)
    assert turn.tool_calls == ["add_payee"] and turn.error_code is None
    assert "王小明" in turn.reply and MASKED in turn.reply and "现在可以给他转账了" in turn.reply
    assert FULL not in turn.reply                                          # 完整号绝不回显
    row = raw(seeded, "SELECT * FROM payee WHERE name = ?", ("王小明",))[0]
    assert row["phone"] == MASKED and row["is_whitelist"] == 0 and row["last_used_ts"] is None
    everywhere = str(raw(seeded, "SELECT * FROM payee")) + str(raw(seeded, "SELECT * FROM audit_log"))
    assert FULL not in everywhere                                          # 落库与审计里都没有完整号


def test_submit_audits_the_masked_form_only(seeded: Path) -> None:
    before = count(seeded, "audit_log")
    orchestrator.submit_payee("王小明", FULL, session_id="payee-audit")
    rows = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")
    assert count(seeded, "audit_log") == before + 1
    assert rows[0]["intent"] == "payee_add" and rows[0]["tool"] == "add_payee"
    assert rows[0]["result"] == "success" and rows[0]["permission_tier"] == "L1"
    assert MASKED in rows[0]["params_json"] and FULL not in rows[0]["params_json"]


def test_submit_twice_does_not_duplicate(seeded: Path) -> None:
    orchestrator.submit_payee("王小明", FULL, session_id="payee-dup")
    before = count(seeded, "payee")
    again = orchestrator.submit_payee("王小明", FULL, session_id="payee-dup")
    assert count(seeded, "payee") == before and "已经" in again.reply
    assert again.executed is True and again.tier == "L1"                   # 既有收款人也是"已就绪"


def test_submit_with_bad_phone_reports_invalid_and_audits_error(seeded: Path) -> None:
    before = count(seeded, "payee")
    turn = orchestrator.submit_payee("王小明", "1381234567", session_id="payee-bad")
    assert turn.executed is False and turn.error_code == "INVALID_ARGUMENT"
    assert "1381234567" not in turn.reply and count(seeded, "payee") == before
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["result"] == "error" and row["error_code"] == "INVALID_ARGUMENT"
    assert "1381234567" not in str(row)                                    # 铁律 8：审计也不留完整号


# ---------------- ④ 加完能转账：姓名 / 脱敏号 都应命中，且走 L2 ----------------

@pytest.mark.parametrize("payee_slot", ["王小明", MASKED])
def test_new_payee_is_transferable_and_lands_on_l2(seeded: Path, monkeypatch: pytest.MonkeyPatch,
                                                   payee_slot: str) -> None:
    from agent import confirm_card

    confirm_card.reset_state()                                             # 两个参数各用独立会话，别串在途确认态
    session = f"payee-transfer-{payee_slot}"
    orchestrator.submit_payee("王小明", FULL, session_id=session)
    _stub(monkeypatch, {"intent": "transfer_single", "confidence": 0.95,
                        "slots": {"payee": payee_slot, "amount": 100}})
    turn = orchestrator.handle(f"给{payee_slot}转 100 元", session_id=session)
    assert turn.intent == "transfer_single" and turn.tier == "L2" and turn.executed is False
    assert "确认卡" in turn.reply and "验证码" in turn.reply                # L2：确认卡 + OTP
    confirm_card.reset_state()                                             # 不留残状态给后续用例


# ---------------- 契约：不给 Turn 加字段 ----------------

def test_payee_turns_keep_the_existing_turn_fields(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """卡 20 的硬要求：界面靠 `intent` 判断，**不新增** Turn / API 响应的任何字段。"""
    _payee_add(monkeypatch)
    chat = orchestrator.handle("加个收款人", session_id="payee-fields")
    submit = orchestrator.submit_payee("王小明", FULL, session_id="payee-fields")
    assert set(chat.model_dump()) == set(submit.model_dump())
    assert chat.model_dump().keys() == orchestrator.Turn.model_fields.keys()
