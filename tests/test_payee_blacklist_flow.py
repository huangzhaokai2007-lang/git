"""T28「加入黑名单」的回归守卫 —— **L1 确认卡、无 OTP**；黑白互斥；拉黑后转账被拒。

| 验收点 | 用例 |
|---|---|
| 加黑后 `blacklist=1` 且 **`is_whitelist` 被清**（互斥） | `test_blacklist_flips_flag_and_clears_whitelist` |
| 已在黑名单 → 幂等（不重复写审计） | `test_blacklist_is_idempotent` |
| 编排层：L1 确认卡、**不要验证码** | `test_orchestration_blacklists_without_otp` |
| **拉黑后转账被风控直接拒** | `test_transfer_to_blacklisted_is_rejected` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, payee_resolve, payee_risk, session_memory
from data import payee_dao
from tests.conftest import raw
from tools import payee

UID = "u_zhangsan_0001"
SESSION = "sess-blacklist"
ZHANG = "张小美"          # 种子：非白名单
MONTH = "2026-08"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    payee_resolve.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    payee_resolve.reset()


def _flag(path, name: str) -> int:
    return raw(path, "SELECT blacklist FROM payee WHERE name = ?", (name,))[0]["blacklist"]


def _wl(path, name: str) -> int:
    return raw(path, "SELECT is_whitelist FROM payee WHERE name = ?", (name,))[0]["is_whitelist"]


def test_blacklist_flips_flag_and_clears_whitelist(wired) -> None:
    """加黑 = `blacklist=1` + **清 `is_whitelist`**（黑白互斥在工具层强制）。"""
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    payee_dao.update_payee(pid, UID, is_whitelist=1, whitelist_quota=100_000)   # 先给白名单
    result = payee.set_payee_blacklist(pid, "诈骗")
    assert result.ok is True and result.data["blacklist"] is True
    assert _flag(wired, ZHANG) == 1 and _wl(wired, ZHANG) == 0


def test_blacklist_is_idempotent(wired) -> None:
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    assert payee.set_payee_blacklist(pid, "诈骗").ok is True
    before = len(raw(wired, "SELECT 1 FROM audit_log WHERE tool = 'set_payee_blacklist'"))
    again = payee.set_payee_blacklist(pid, "诈骗")
    assert again.ok is True and "已经" in again.message
    assert len(raw(wired, "SELECT 1 FROM audit_log WHERE tool = 'set_payee_blacklist'")) == before


def _fake(monkeypatch) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "payee_blacklist", "confidence": 0.95,
                                          "slots": {"payee": ZHANG}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_orchestration_blacklists_without_otp(wired, monkeypatch) -> None:
    """L1 确认卡（**无验证码**）→ 确认 → 直接执行。"""
    _fake(monkeypatch)
    card = orchestrator.handle(f"把{ZHANG}加入黑名单", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "之后" in card.reply and "拒绝" in card.reply
    assert "验证码" not in card.reply                       # L1：不要验证码
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and done.tool_calls == ["set_payee_blacklist"]
    assert _flag(wired, ZHANG) == 1


def test_transfer_to_blacklisted_is_rejected(wired, monkeypatch) -> None:
    """拉黑之后：风控关卡直接 `reject` —— 转账/AA/送礼共用的那条关卡。"""
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    payee.set_payee_blacklist(pid, "诈骗")
    person = payee_resolve.profile_of(pid)
    assert person is not None and person["blacklist"]
    got = payee_risk.assess(person, 10_000, month=MONTH)
    assert got.decision == payee_risk.DECISION_REJECT
    assert "黑名单" in got.message and "诈骗" in got.message


# ---------------- 解除黑名单（T29 · L2 + OTP） ----------------

def test_unblacklist_requires_a_confirm_ref(wired) -> None:
    """伪造凭证 → 拒，且不改库。"""
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    payee.set_payee_blacklist(pid, "诈骗")
    bad = payee.unset_payee_blacklist(pid, "cf_forged")
    assert bad.ok is False and str(bad.error_code).endswith("FORBIDDEN")
    assert _flag(wired, ZHANG) == 1                      # 仍在黑名单


def test_unblacklist_clears_the_flag_with_a_valid_ref(wired) -> None:
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    payee.set_payee_blacklist(pid, "诈骗")
    ref = payee.issue_confirm_ref(payee.UNBLACKLIST_ACTION, f"{pid}:unblacklist")
    result = payee.unset_payee_blacklist(pid, ref)
    assert result.ok is True and result.data["blacklist"] is False
    assert _flag(wired, ZHANG) == 0


def test_unblacklist_of_a_non_blacklisted_payee_is_invalid(wired) -> None:
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    ref = payee.issue_confirm_ref(payee.UNBLACKLIST_ACTION, f"{pid}:unblacklist")
    bad = payee.unset_payee_blacklist(pid, ref)
    assert bad.ok is False and str(bad.error_code).endswith("INVALID_STATE")


def _fake_unbl(monkeypatch) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "payee_unblacklist", "confidence": 0.95,
                                          "slots": {"payee": ZHANG}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_orchestration_unblacklists_with_otp(wired, monkeypatch) -> None:
    """**L2 + OTP**：确认 → 验证码 → 才执行。解除黑名单必须验证码（人类需求原文）。"""
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]
    payee.set_payee_blacklist(pid, "诈骗")
    _fake_unbl(monkeypatch)
    card = orchestrator.handle(f"把{ZHANG}移出黑名单", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"
    assert _flag(wired, ZHANG) == 1                      # 出卡而已，没写库

    otp_stage = orchestrator.handle("确认", session_id=SESSION)
    assert "CLASSIFY" not in otp_stage.states            # 钉根因：在途确认被消费
    assert "验证码" in otp_stage.reply and otp_stage.executed is False
    assert _flag(wired, ZHANG) == 1                      # 验证码没给，仍没写库

    wrong = orchestrator.handle("000000", session_id=SESSION)
    assert wrong.executed is False and _flag(wired, ZHANG) == 1

    ok = orchestrator.handle("123456", session_id=SESSION)
    assert ok.executed is True and ok.tool_calls == ["unset_payee_blacklist"]
    assert _flag(wired, ZHANG) == 0                      # 解除生效
