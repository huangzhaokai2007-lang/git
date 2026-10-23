"""白名单**有效期**（T36）：仅展示、**不参与风控**（人类明确口径）。

| 验收点 | 用例 |
|---|---|
| T36：白名单的人可设有效期（归一成 YYYY-MM-DD） | `test_set_expire_on_whitelisted_payee` |
| T36：非白名单 / 非法日期 → 拒绝 | `test_set_expire_rejects_non_whitelist_and_bad_date` |
| 加白时带有效期 → 卡面有「有效期」行 → 落库 | `test_whitelist_with_expire_lands_on_row` |
| **不参与风控**：有效期已过也不影响额度内免码判定 | `test_expire_does_not_affect_risk_gate` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, payee_risk, session_memory
from data import payee_dao
from tests.conftest import raw
from tools import payee

SESSION = "sess-expire"
WANGWU, ZHANG = "王五", "张小美"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, slots: dict) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "payee_whitelist", "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_set_expire_on_whitelisted_payee(wired) -> None:
    """T36：有效期归一成 YYYY-MM-DD；空串 = 清除。"""
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (WANGWU,))[0]["id"]
    assert payee.set_whitelist_expire(pid, "2027-01-31").ok is True
    assert raw(wired, "SELECT whitelist_expire FROM payee WHERE id = ?", (pid,))[0][
        "whitelist_expire"] == "2027-01-31"
    assert payee.set_whitelist_expire(pid, "").ok is True                  # 清除 = 长期
    assert raw(wired, "SELECT whitelist_expire FROM payee WHERE id = ?", (pid,))[0][
        "whitelist_expire"] is None


def test_set_expire_rejects_non_whitelist_and_bad_date(wired) -> None:
    zhang = raw(wired, "SELECT id FROM payee WHERE name = ?", (ZHANG,))[0]["id"]  # 非白名单
    bad = payee.set_whitelist_expire(zhang, "2027-01-31")
    assert bad.ok is False and "不在白名单" in bad.message
    wang = raw(wired, "SELECT id FROM payee WHERE name = ?", (WANGWU,))[0]["id"]
    assert payee.set_whitelist_expire(wang, "下个月三十二号").ok is False


def test_whitelist_with_expire_lands_on_row(wired, monkeypatch) -> None:
    """「把张小美加入白名单，有效期到 2027-01-31」→ 卡面有「有效期」→ 确认后 T25 + T36 都落。"""
    _fake(monkeypatch, {"payee": ZHANG, "expire": "2027-01-31"})
    card = orchestrator.handle(f"把{ZHANG}加入白名单，有效期到 2027-01-31", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"
    assert "有效期" in card.reply and "仅展示" in card.reply
    otp_stage = orchestrator.handle("确认", session_id=SESSION)
    assert "验证码" in otp_stage.reply
    done = orchestrator.handle("123456", session_id=SESSION)
    assert done.executed is True and "白名单有效期到" in done.reply
    row = raw(wired, "SELECT is_whitelist, whitelist_expire FROM payee WHERE name = ?", (ZHANG,))[0]
    assert row["is_whitelist"] == 1 and row["whitelist_expire"] == "2027-01-31"


def test_expire_does_not_affect_risk_gate(wired) -> None:
    """**仅展示**：把有效期设在过去，额度内仍然是 L1 免码（风控不读有效期）。"""
    from agent import payee_resolve
    pid = raw(wired, "SELECT id FROM payee WHERE name = ?", (WANGWU,))[0]["id"]
    payee_dao.update_payee(pid, "u_zhangsan_0001", whitelist_expire="2020-01-01")   # 早就过期
    person = payee_resolve.profile_of(pid)
    assert person["whitelist_expire"] == "2020-01-01"
    month = "2026-08"
    assert payee_risk.assess(person, 10_000, month=month).decision == payee_risk.DECISION_ALLOW
