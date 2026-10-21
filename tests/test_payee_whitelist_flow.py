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

from agent import confirm_card, session_memory
from tests.conftest import raw
from tools import payee

ZHANG = "张小美"                       # data/seed.py：is_whitelist=0


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    payee._CONFIRM_REFS.clear()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()
    payee._CONFIRM_REFS.clear()


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


# ⚠️ **本文件刻意不覆盖聊天接线**：`payee_whitelist` 意图尚未登记进 §3 ——
# 实测发现把 `transfer`/`subscription` 那套 `confirm_ref` 描述符直接套上来时，
# 点「确认」会**重新出卡**而不是进入验证码阶段（在途确认没被消费）。
# 这是一个用户可见的死循环，故**没有接线**，只交付工具层（T25）+ 上面 4 条安全用例。
# 接线属下一张卡；在它完成前，用户**无法**通过聊天加入白名单（功能不可达，但不是半坏状态）。

