"""T25「加入白名单」的**工具层**回归守卫 —— 凭证校验与**方向绑定**。

**为什么把档位与绑定方式钉死在这里**：白名单 = 转账免验证码的特权。若「加收款人」（L1、不要 OTP）
能顺带授予它，账户一旦被盗，攻击者可把自己加进白名单**永久绕过 OTP**。所以：

| 验收点 | 用例 |
|---|---|
| 未签发的凭证 → 拒 | `test_whitelist_requires_a_confirm_ref` |
| **方向绑定**：拿「加入」的凭证执行「移出」→ 拒 | `test_confirm_ref_is_bound_to_the_direction` |
| 同一凭证重复调用 → 幂等、不重复写 | `test_whitelist_is_idempotent_per_ref` |
| 写入后 `is_whitelist` 真的变了 + 审计留痕 | `test_whitelist_flips_the_flag_and_audits` |
"""

from __future__ import annotations

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import payee, payee_status

SESSION = "sess-whitelist"

ZHANG = "张小美"                       # data/seed.py：is_whitelist=0


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    payee_status._CONFIRM_REFS.clear()          # 凭证进程内状态，逐用例复位
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    payee_status._CONFIRM_REFS.clear()


def _payee_id(path, name: str) -> str:
    return raw(path, "SELECT id FROM payee WHERE name = ?", (name,))[0]["id"]


def _flag(path, name: str) -> int:
    return raw(path, "SELECT is_whitelist FROM payee WHERE name = ?", (name,))[0]["is_whitelist"]


def _ref(name: str, enable: bool) -> tuple[str, str]:
    pid = payee.list_payees().facts["items"]
    target = next(i["payee_id"] for i in pid if i["name"] == name)
    return target, payee.issue_confirm_ref(payee.WHITELIST_ACTION,
                                          payee.whitelist_target(target, enable))


def test_whitelist_requires_a_confirm_ref(wired) -> None:
    """伪造凭证一律拒（`FORBIDDEN`），且**不改库**。"""
    pid = _payee_id(wired, ZHANG)
    result = payee.set_payee_whitelist(pid, True, "cf_forged")
    assert result.ok is False and str(result.error_code).endswith("FORBIDDEN")
    assert _flag(wired, ZHANG) == 0


def test_confirm_ref_is_bound_to_the_direction(wired) -> None:
    """**安全要点**：凭证把方向绑进 target —— 拿「加入」的凭证去执行「移出」必须被拒。"""
    pid, ref = _ref(ZHANG, enable=True)
    result = payee.set_payee_whitelist(pid, False, ref)
    assert result.ok is False and str(result.error_code).endswith("FORBIDDEN")
    assert _flag(wired, ZHANG) == 0


def test_whitelist_flips_the_flag_and_audits(wired) -> None:
    pid, ref = _ref(ZHANG, enable=True)
    before = len(raw(wired, "SELECT 1 FROM audit_log"))
    result = payee.set_payee_whitelist(pid, True, ref)
    assert result.ok is True and result.data["is_whitelist"] is True
    assert _flag(wired, ZHANG) == 1
    audits = raw(wired, "SELECT * FROM audit_log WHERE tool = 'set_payee_whitelist'")
    assert len(audits) == 1 and audits[0]["permission_tier"] == "L2"
    assert len(raw(wired, "SELECT 1 FROM audit_log")) == before + 1


def test_whitelist_is_idempotent_per_ref(wired) -> None:
    """同一凭证重复调用只写一次（幂等），第二次返回既有结果。"""
    pid, ref = _ref(ZHANG, enable=True)
    assert payee.set_payee_whitelist(pid, True, ref).ok is True
    again = payee.set_payee_whitelist(pid, True, ref)
    assert again.ok is True and "重复提交" in again.message
    assert len(raw(wired, "SELECT * FROM audit_log WHERE tool = 'set_payee_whitelist'")) == 1


# ---------------- 编排层：L2 + OTP 才写库 ----------------

def _fake(monkeypatch, seen: list[str]) -> None:
    """假 LLM：分类出 `payee_whitelist`；润色把原文原样返回（数字一个不动）。"""
    import json

    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            seen.append(str(user))
            return schema.model_validate({"intent": "payee_whitelist", "confidence": 0.95,
                                          "slots": {"payee": ZHANG}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_orchestration_needs_otp_before_writing(wired, monkeypatch) -> None:
    """出 **L2 卡** → 「确认」**必须进验证码阶段** → 错码不写库 → 对码才落库。

    **钉的是根因**：第二轮的 `states` 里**不能出现 `CLASSIFY`** ——
    出现就说明「在途确认没被消费」，用户点确认只会反复重出同一张卡（我第一版就踩了这个坑）。
    """
    seen: list[str] = []
    _fake(monkeypatch, seen)
    card = orchestrator.handle(f"把{ZHANG}加入白名单", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and card.tier == "L2"
    # 影响必须事先说清（v2 SPEC-CHANGE 口径）：专属额度内免验证码（含深夜）、超额度要码。
    #: 2026-10-24：额度口径改成「每月」，卡面文案随之更新（人类要求明确写出每月）。
    assert "白名单专属额度（每月）内转账免验证码" in card.reply
    assert "超额度仍需验证码" in card.reply
    assert _flag(wired, ZHANG) == 0                  # 出卡而已，没写库

    otp_stage = orchestrator.handle("确认", session_id=SESSION)
    assert "CLASSIFY" not in otp_stage.states, "在途确认没被消费（会反复重出卡）"
    assert "验证码" in otp_stage.reply and otp_stage.executed is False

    wrong = orchestrator.handle("000000", session_id=SESSION)
    assert wrong.executed is False and _flag(wired, ZHANG) == 0

    ok = orchestrator.handle("123456", session_id=SESSION)
    assert ok.executed is True and ok.tool_calls == ["set_payee_whitelist"]
    assert _flag(wired, ZHANG) == 1


