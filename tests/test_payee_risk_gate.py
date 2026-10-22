"""风控关卡（`agent/payee_risk.py`）的回归守卫 —— 白名单专属额度是 v2 的新口径。

**钉的是一条产品规则**（人类需求原文）：
> 白名单：在专属额度内转账，不需要验证码。
> 超出限额、超出白名单专属额度、非白名单 → 需要验证码。
> 黑名单：禁止转账与送礼，直接拒绝并提示原因。

所以「白名单」不是一个布尔值，而是「额度还没用完」这个**动态状态**。

| 验收点 | 用例 |
|---|---|
| 黑名单 → 拒（连确认卡都不出） | `test_blacklist_rejects_before_any_card` |
| 白名单 **额度内** → 免码（L1） | `test_whitelist_within_quota_needs_no_otp` |
| 白名单 **超额度** → 要码 | `test_whitelist_over_quota_needs_otp` |
| 白名单但**没设额度** → 要码（不是"默认免码"） | `test_whitelist_without_quota_needs_otp` |
| 普通收款人 → 要码 | `test_non_whitelist_needs_otp` |
| 额度**已用掉一部分**后，剩的那点是上限 | `test_remaining_quota_shrinks_with_usage` |
"""

from __future__ import annotations

import pytest

from agent import payee_resolve, payee_risk
from data import payee_dao
from tools import payee

UID = "u_zhangsan_0001"
MONTH = "2026-08"                     # 种子账期


@pytest.fixture()
def wired(seeded):
    payee_resolve.reset()
    yield seeded
    payee_resolve.reset()


def _person(name: str) -> dict:
    """取解析关卡认出来的那个人（带 tags/blacklist/quota 画像）。

    **允许黑名单**：黑名单也是一种"认出来了"（结论是 `blacklisted` 而不是 `unique`），
    风控关卡要拿到这个人的画像才能判「拒」。
    """
    got = payee_resolve.resolve(name)
    assert got.payees, f"{name} 没解析出任何人：{got.kind} {got.clarify}"
    return dict(got.payees[0])


def test_blacklist_rejects_before_any_card(wired) -> None:
    pid = payee.add_payee("骗子甲", "13800004444").data["payee_id"]
    payee_dao.update_payee(pid, UID, blacklist=1, blacklist_reason="诈骗嫌疑")
    person = _person("骗子甲")
    got = payee_risk.assess(person, 10_000, month=MONTH)
    assert got.decision == payee_risk.DECISION_REJECT
    assert got.reason == payee_risk.REASON_BLACKLISTED
    assert "黑名单" in got.message and "诈骗嫌疑" in got.message


def test_whitelist_within_quota_needs_no_otp(wired) -> None:
    """王五（种子：白名单 + 1000 元额度）。转 100 元 → **免码**。"""
    got = payee_risk.assess(_person("房东"), 10_000, month=MONTH)
    assert got.decision == payee_risk.DECISION_ALLOW
    assert got.tier == payee_risk.TIER_L1
    assert got.reason == payee_risk.REASON_WITHIN_QUOTA


def test_whitelist_over_quota_needs_otp(wired) -> None:
    """**同一个人**：额度 50 元时转 100 元 → 要码。

    注意额度必须设成「低于单笔上限（500 元）」才有意义 —— 否则 3000 元会先被**单笔上限**
    拒掉（`preview_failed`），测到的就不是额度这条规则了。
    """
    person = _person("房东")
    payee_dao.update_payee(person["payee_id"], UID, whitelist_quota=5_000)     # 50 元
    tightened = _person("房东")
    got = payee_risk.assess(tightened, 10_000, month=MONTH)                    # 转 100 元
    assert got.decision == payee_risk.DECISION_NEED_OTP
    assert got.reason == payee_risk.REASON_QUOTA_EXCEEDED
    assert got.facts["whitelist_quota"] == 5_000


def test_whitelist_without_quota_needs_otp(wired) -> None:
    """白名单但没设额度 → 要码。**不能**因为"他是白名单"就默认免码。"""
    pid = payee.add_payee("同事甲", "13800005555").data["payee_id"]
    payee_dao.update_payee(pid, UID, is_whitelist=1)          # 只设白名单，不设额度
    got = payee_risk.assess(_person("同事甲"), 10_000, month=MONTH)
    assert got.decision == payee_risk.DECISION_NEED_OTP
    assert got.reason == payee_risk.REASON_NO_QUOTA


def test_non_whitelist_needs_otp(wired) -> None:
    got = payee_risk.assess(_person("妈妈"), 10_000, month=MONTH)
    assert got.decision == payee_risk.DECISION_NEED_OTP
    assert got.reason == payee_risk.REASON_NON_WHITELIST


def test_remaining_quota_shrinks_with_usage(wired) -> None:
    """额度是**剩余**口径，不是总额 —— 否则会一直免下去。

    用**没有任何历史转账**的新收款人，才能干净地观察「额度上限」这条规则；
    种子里的王五本月已有转出，剩余额度本来就不等于总额度。
    """
    pid = payee.add_payee("新同事", "13800006666").data["payee_id"]
    payee_dao.update_payee(pid, UID, is_whitelist=1, whitelist_quota=20_000)   # 200 元
    person = _person("新同事")

    ok = payee_risk.assess(person, 10_000, month=MONTH)                       # 100 元 ≤ 200 元
    assert ok.decision == payee_risk.DECISION_ALLOW
    assert ok.facts["quota_remaining"] >= 10_000

    over = payee_risk.assess(person, 30_000, month=MONTH)                     # 300 元 > 200 元
    assert over.decision == payee_risk.DECISION_NEED_OTP
    assert over.reason == payee_risk.REASON_QUOTA_EXCEEDED
    assert over.facts["quota_remaining"] == 20_000


# ---------------- apply_quota（接入 _prepare_transfer 的那一层） ----------------

def test_apply_quota_leaves_base_tier_when_within_quota(wired) -> None:
    """额度内 → 不覆盖既有档位（白名单 → L1 保持 L1）。"""
    person = _person("房东")                       # 种子：白名单 1000 元额度
    tier, note = payee_risk.apply_quota(person, 10_000, "L1", month=MONTH)
    assert tier == "L1" and note == ""


def test_apply_quota_raises_to_l2_when_over_quota(wired) -> None:
    """超额度 → 至少 L2 要码，并给出提示（这是接进确认卡后用户会看到的那句）。"""
    person = _person("房东")
    payee_dao.update_payee(person["payee_id"], UID, whitelist_quota=5_000)     # 50 元
    person = _person("房东")
    tier, note = payee_risk.apply_quota(person, 10_000, "L1", month=MONTH)
    assert tier == "L2" and "超出" in note


def test_apply_quota_never_downgrades(wired) -> None:
    """**绝不放松**：即使额度内，night 已经给的 L2 不能被降回 L1。"""
    person = _person("房东")
    tier, _ = payee_risk.apply_quota(person, 10_000, "L2", month=MONTH)
    assert tier == "L2"


def test_apply_quota_ignores_non_whitelist(wired) -> None:
    """普通收款人不受额度影响（他没有额度这个维度）。"""
    person = _person("妈妈")                       # 张小美，非白名单
    tier, note = payee_risk.apply_quota(person, 10_000, "L2", month=MONTH)
    assert tier == "L2" and note == ""
