"""T30「删除收款人」的回归守卫 —— L1 确认卡、无 OTP；只删联系人、不动流水。

| 验收点 | 用例 |
|---|---|
| 删除后行消失、历史流水**保留** | `test_delete_removes_row_keeps_txns` |
| 删不存在的 → NOT_FOUND | `test_delete_missing_payee` |
| 归属 fail-closed（他人收款人不可删） | `test_delete_foreign_payee_rejected` |
| 编排层：L1 确认卡（无验证码）→ 确认 → 执行 | `test_orchestration_deletes_without_otp` |
| 删白名单的人：卡面讲清现状 | `test_delete_whitelisted_shows_kind` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import payee

UID = "u_zhangsan_0001"
SESSION = "sess-delete"
ZHANG = "张小美"
FOREIGN_PHONE = "139****9999"        # conftest.foreign_payee：马洛里名下的「李四」


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _pid(path, name: str) -> str:
    return raw(path, "SELECT id FROM payee WHERE name = ? AND user_id = ?", (name, UID))[0]["id"]


def test_delete_removes_row_keeps_txns(wired) -> None:
    """删除只删联系人；`txn` 流水一条不动（审计可回溯）。"""
    pid = _pid(wired, ZHANG)
    txns_before = len(raw(wired, "SELECT 1 FROM txn"))
    result = payee.delete_payee(pid)
    assert result.ok is True and result.data["deleted"] is True
    assert raw(wired, "SELECT 1 FROM payee WHERE id = ?", (pid,)) == []
    assert len(raw(wired, "SELECT 1 FROM txn")) == txns_before


def test_delete_missing_payee(wired) -> None:
    got = payee.delete_payee("payee_ghost")
    assert got.ok is False and str(got.error_code).endswith("NOT_FOUND")


def test_delete_foreign_payee_rejected(wired, foreign_payee) -> None:
    got = payee.delete_payee("payee_x_0001")                 # conftest.FOREIGN_PAYEE：马洛里的
    assert got.ok is False and str(got.error_code).endswith("NOT_FOUND")
    assert raw(wired, "SELECT 1 FROM payee WHERE id = ?", ("payee_x_0001",)) != []


def _fake(monkeypatch) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "payee_delete", "confidence": 0.95,
                                          "slots": {"payee": ZHANG}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_orchestration_deletes_without_otp(wired, monkeypatch) -> None:
    """L1 确认卡（**无验证码**）→ 确认 → 直接执行。"""
    _fake(monkeypatch)
    card = orchestrator.handle(f"删除收款人{ZHANG}", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "删除收款人确认卡" in card.reply and "验证码" not in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and done.tool_calls == ["delete_payee"]
    assert raw(wired, "SELECT 1 FROM payee WHERE name = ? AND user_id = ?", (ZHANG, UID)) == []


def test_delete_whitelisted_shows_kind(wired, monkeypatch) -> None:
    """删白名单的人：卡面「现状」行讲清是白名单（删除后该状态一并消失）。"""
    _fake(monkeypatch)
    card = orchestrator.handle(f"删除收款人{ZHANG}", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states
    assert "现状" in card.reply
