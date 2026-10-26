"""收款人三态（白名单 ⇄ 普通 ⇄ 黑名单）可自由切换。

人类要求（2026-10-26）：「我希望现在可以将白名单拉黑，人物可以在这三个身份切换」。
此前 `_shape_blacklist` 会拒绝白名单（「请先移出白名单」），而 `_shape_whitelist`
又拒绝解析黑名单里的人 —— 一拉黑就再也切不回来。本文件把三条边都钉住。
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from data import dao

SESSION = "sess-3states"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _say(monkeypatch, intent: str, slots: dict, text: str):
    def chat_json(system, user, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": intent, "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    return orchestrator.handle(text, session_id=SESSION)


def _state() -> tuple[bool, bool]:
    row = dao.connection().execute(
        "SELECT is_whitelist, blacklist FROM payee WHERE name = ?", (WANGWU,)).fetchone()
    return bool(row["is_whitelist"]), bool(row["blacklist"])


def test_whitelist_can_be_blacklisted_directly(wired, monkeypatch) -> None:
    """白名单 → 黑名单：一步到位（卡面要说明「并移出白名单」）。"""
    assert _state() == (True, False)
    card = _say(monkeypatch, "payee_blacklist", {"payee": WANGWU}, "把王五拉黑")
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"
    assert "并移出白名单" in card.reply and "白名单专属额度同时失效" in card.reply
    done = _say(monkeypatch, "payee_blacklist", {"payee": WANGWU}, "确认")
    assert done.executed is True
    assert _state() == (False, True)


def test_blacklist_can_be_promoted_back_to_whitelist(wired, monkeypatch) -> None:
    """黑名单 → 白名单：也要走得通（此前被「已在黑名单」永久挡住）。"""
    _say(monkeypatch, "payee_blacklist", {"payee": WANGWU}, "把王五拉黑")
    _say(monkeypatch, "payee_blacklist", {"payee": WANGWU}, "确认")
    assert _state() == (False, True)

    card = _say(monkeypatch, "payee_whitelist", {"payee": WANGWU}, "把王五加入白名单")
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"      # 加白要码
    _say(monkeypatch, "payee_whitelist", {"payee": WANGWU}, "确认")
    done = _say(monkeypatch, "payee_whitelist", {"payee": WANGWU}, "123456")
    assert done.executed is True
    assert _state() == (True, False)                                # 黑名单被一并解除


def test_off_the_whitelist_returns_to_normal(wired, monkeypatch) -> None:
    """白名单 → 普通（移出白名单），再可拉黑 —— 三态闭环。"""
    _say(monkeypatch, "payee_whitelist_remove", {"payee": WANGWU}, "把王五移出白名单")
    done = _say(monkeypatch, "payee_whitelist_remove", {"payee": WANGWU}, "确认")
    assert done.executed is True
    assert _state() == (False, False)
