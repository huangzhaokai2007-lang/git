"""收款人信息维护（2026-10-24 人类原文）：备注 / 修改白名单信息 / 移出白名单。

| 验收点 | 用例 |
|---|---|
| T45 备注：设置 + 清除，画像里带 note | `test_note_set_and_clear` |
| 加白时一并设**专属额度 + 有效期**（L2 要码） | `test_add_whitelist_with_quota_and_expire` |
| 修改白名单信息（额度）→ **L2 要码**，卡面「原值→新值」 | `test_edit_whitelist_quota_needs_otp` |
| 移出白名单（L1 免码）→ 恢复普通收款人 | `test_remove_whitelist` |
| 改备注意图（L1） | `test_note_intent` |
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw

SESSION = "sess-payee-info"
ZHANG = "张小美"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, intent: str, slots: dict | None = None) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots or {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def _pid(wired, name: str) -> str:
    return raw(wired, "SELECT id FROM payee WHERE name = ?", (name,))[0]["id"]


def test_note_set_and_clear(wired) -> None:
    """T45：备注可设、可清；`facts.profiles` 带 note（查询卡要用）。"""
    from tools import payee
    pid = _pid(wired, WANGWU)
    assert payee.set_payee_note(pid, "房东，月底交租").ok is True
    assert raw(wired, "SELECT note FROM payee WHERE id = ?", (pid,))[0]["note"] == "房东，月底交租"
    assert payee.list_payees().facts["profiles"][pid]["note"] == "房东，月底交租"
    assert payee.set_payee_note(pid, "").ok is True
    assert raw(wired, "SELECT note FROM payee WHERE id = ?", (pid,))[0]["note"] is None


def test_note_masks_full_phone(wired) -> None:
    """备注里写完整手机号 → 入库前**脱敏**（铁律 8：完整号码绝不入库）。"""
    from tools import payee
    pid = _pid(wired, WANGWU)
    assert payee.set_payee_note(pid, "他电话 13812345678，别转错").ok is True
    stored = raw(wired, "SELECT note FROM payee WHERE id = ?", (pid,))[0]["note"]
    assert "138****5678" in stored and "13812345678" not in stored


def test_add_whitelist_with_quota_and_expire(wired, monkeypatch) -> None:
    """「把张小美加入白名单，额度 5000 元，有效期到 2026-12-31」→ L2 卡 → 123456 → 两项都落库。"""
    _fake(monkeypatch, "payee_whitelist",
          {"payee": ZHANG, "quota": "5000", "expire": "2026-12-31"})
    card = orchestrator.handle("把张小美加入白名单，额度 5000 元，有效期到 2026-12-31",
                               session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"
    assert "专属额度" in card.reply and "5000.00 元" in card.reply and "2026-12-31" in card.reply
    orchestrator.handle("确认", session_id=SESSION)
    done = orchestrator.handle("123456", session_id=SESSION)
    assert done.executed is True
    row = raw(wired, "SELECT is_whitelist, whitelist_quota, whitelist_expire FROM payee"
                     " WHERE id = ?", (_pid(wired, ZHANG),))[0]
    assert row["is_whitelist"] == 1 and row["whitelist_quota"] == 500_000
    assert row["whitelist_expire"] == "2026-12-31"


def test_edit_whitelist_quota_needs_otp(wired, monkeypatch) -> None:
    """修改白名单信息 → **L2 + 验证码**（人类原文）；卡面给出「当前额度 → 新额度」。"""
    pid = _pid(wired, WANGWU)
    before = raw(wired, "SELECT whitelist_quota FROM payee WHERE id = ?", (pid,))[0]["whitelist_quota"]
    _fake(monkeypatch, "payee_whitelist_edit", {"payee": WANGWU, "quota": "800"})
    card = orchestrator.handle("把王五的专属额度改成 800 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"
    assert "验证码" in card.reply                                  # L2 卡面会写明要码
    assert "当前专属额度" in card.reply and "新的专属额度" in card.reply
    stage = orchestrator.handle("确认", session_id=SESSION)
    assert "验证码" in stage.reply
    done = orchestrator.handle("123456", session_id=SESSION)
    assert done.executed is True
    after = raw(wired, "SELECT whitelist_quota FROM payee WHERE id = ?", (pid,))[0]["whitelist_quota"]
    assert after == 80_000 and after != before


def test_remove_whitelist(wired, monkeypatch) -> None:
    """移出白名单 → L1 免码，恢复普通收款人。"""
    pid = _pid(wired, WANGWU)
    assert raw(wired, "SELECT is_whitelist FROM payee WHERE id = ?", (pid,))[0]["is_whitelist"] == 1
    _fake(monkeypatch, "payee_whitelist_remove", {"payee": WANGWU})
    card = orchestrator.handle("把王五移出白名单", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "移出白名单" in card.reply and "需要验证码" in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True
    assert raw(wired, "SELECT is_whitelist FROM payee WHERE id = ?", (pid,))[0]["is_whitelist"] == 0


def test_note_intent(wired, monkeypatch) -> None:
    """改备注意图（L1）：卡面「原备注 → 新备注」→ 确认即生效。"""
    pid = _pid(wired, ZHANG)
    _fake(monkeypatch, "payee_note", {"payee": ZHANG, "note": "妈妈，别转错"})
    card = orchestrator.handle("给张小美加个备注：妈妈，别转错", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "原备注" in card.reply and "新备注" in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True
    assert raw(wired, "SELECT note FROM payee WHERE id = ?", (pid,))[0]["note"] == "妈妈，别转错"


# ---------------- 2026-10-24：银行卡号（只存脱敏形态） ----------------

def test_card_no_is_stored_masked(wired) -> None:
    """T50：完整卡号入库前脱敏成 `**** **** **** 8888`；位数不对拒绝；空串清除。"""
    from tools import payee
    pid = _pid(wired, WANGWU)
    got = payee.set_payee_card_no(pid, "6222 0212 3456 8888")
    assert got.ok is True and got.data["card_no"] == "**** **** **** 8888"
    assert raw(wired, "SELECT card_no FROM payee WHERE id = ?", (pid,))[0]["card_no"] == "**** **** **** 8888"
    assert payee.list_payees().facts["profiles"][pid]["card_no"] == "**** **** **** 8888"
    assert payee.set_payee_card_no(pid, "123").ok is False        # 位数不对
    assert payee.set_payee_card_no(pid, "").ok is True
    assert raw(wired, "SELECT card_no FROM payee WHERE id = ?", (pid,))[0]["card_no"] is None


def test_card_no_never_stores_full_number(wired) -> None:
    """铁律 8：库里绝不能出现完整卡号。"""
    from tools import payee
    pid = _pid(wired, ZHANG)
    full = "6222021234567890123"
    assert payee.set_payee_card_no(pid, full).ok is True
    row = raw(wired, "SELECT * FROM payee WHERE id = ?", (pid,))[0]
    assert full not in str(dict(row)) and "7890123" not in str(dict(row))
