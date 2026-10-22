"""风控关卡（转账域 v2 的**唯一实现**）—— 白名单专属额度 + 黑名单 + 既有档位。

**职责边界（重要，避免第二份实现）**：
- **档位怎么算**：仍由 `tools/_transfer_risk.py` + `guard/permission.py` 决定 —— 本模块**不重算**，
  只调 T7 `preview_transfer` 拿 `facts["tier"]` / `facts["factors"]`（铁律 1：业务数字只来自工具）。
- **本模块新增的只有一件**：白名单的**专属额度**。它是 v2 才有的口径 ——
  「白名单且本笔在剩余专属额度内 → 免验证码；超出额度 → 按普通收款人处理」。

所以「白名单」在 v2 里不是一个布尔值，而是一条判断：

```
黑名单                    → reject（连确认卡都不出）
白名单 + 额度内           → allow（L1，免码）
白名单 + 超额度 / 未设额度 → need_otp（L2）
普通收款人                → need_otp（走既有档位，通常 L2）
超单笔/今日限额           → need_otp（既有档位自己会升）
```

**不判白名单有效期**（人类明确：有效期只做展示，不参与风控）。
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Mapping

from data import payee_dao
from tools import transfer

logger = logging.getLogger(__name__)

DECISION_ALLOW, DECISION_NEED_OTP, DECISION_REJECT = "allow", "need_otp", "reject"

REASON_BLACKLISTED = "blacklisted"
REASON_WITHIN_QUOTA = "whitelist_within_quota"
REASON_QUOTA_EXCEEDED = "whitelist_quota_exceeded"
REASON_NO_QUOTA = "whitelist_without_quota"
REASON_NON_WHITELIST = "non_whitelist"
REASON_PREVIEW_FAILED = "preview_failed"

TIER_L1, TIER_L2, TIER_L3 = "L1", "L2", "L3"


def month_prefix(now: datetime.datetime | None = None) -> str:
    """本月前缀（`YYYY-MM`）—— 与 `txn.ts` 的 ISO 前缀对齐，用于算专属额度的已用部分。"""
    moment = now or datetime.datetime.now()
    return moment.strftime("%Y-%m")


@dataclass(frozen=True)
class RiskDecision:
    """一次风控结论。`facts` 里的数字都来自工具/DAO，直接进确认卡（铁律 2）。"""

    decision: str
    tier: str
    reason: str
    facts: Mapping
    message: str = ""

    @property
    def needs_otp(self) -> bool:
        return self.decision == DECISION_NEED_OTP

    @property
    def rejected(self) -> bool:
        return self.decision == DECISION_REJECT


_TIER_ORDER = {TIER_L1: 0, TIER_L2: 1, TIER_L3: 2}


def _higher_tier(a: str, b: str) -> str:
    """取更严格（更高）的档位。"""
    return a if _TIER_ORDER.get(a, 0) >= _TIER_ORDER.get(b, 0) else b


def apply_quota(payee: Mapping, amount_cents: int, base_tier: str, *,
                month: str | None = None) -> tuple[str, str]:
    """**只做白名单「专属额度」这一层的档位覆盖**（不调 `preview_transfer`，调用方已调过）。

    返回 `(最终档位, 追加提示)`。
    - 非白名单 / 额度内 → 维持 `base_tier`（普通收款人由既有档位决定）；
    - 超额度 / 未设额度 → 至少升到 L2（要验证码），并给出提示。

    ⚠️ 只加"更严格"、不"放松"：night / velocity 等在 `base_tier` 里已经体现，
    本函数用 `_higher_tier` 保证绝不会把 L2 降回 L1。
    """
    if not payee or not payee.get("is_whitelist"):
        return base_tier, ""
    quota = payee.get("whitelist_quota")
    if quota is None:
        return _higher_tier(base_tier, TIER_L2), "该白名单收款人未设专属额度，转账需要验证码"
    remaining = payee_dao.remaining_quota_cents(dict(payee), month or month_prefix())
    if remaining is None or int(amount_cents) <= remaining:
        return base_tier, ""
    return _higher_tier(base_tier, TIER_L2), "已超出白名单专属额度，需要验证码"


def assess(payee: Mapping, amount_cents: int, *, month: str | None = None) -> RiskDecision:
    """对「给这个人转这么多钱」做风控：黑名单 → 额度 → 既有档位。

    `payee` 需要 `payee_id` / `name` / `is_whitelist` / `blacklist` / `whitelist_quota`
    （来自 T20 的 `facts.profiles`，见 `agent/payee_resolve.py`）。
    """
    if payee.get("blacklist"):
        reason = payee.get("blacklist_reason") or ""
        text = f"{payee.get('name')} 已在黑名单，不能转账或送礼。"
        if reason:
            text += f"原因：{reason}。"
        return RiskDecision(decision=DECISION_REJECT, tier=TIER_L3,
                            reason=REASON_BLACKLISTED, facts={"blacklist_reason": reason},
                            message=text)

    preview = transfer.preview_transfer(str(payee["payee_id"]), int(amount_cents))
    if not preview.ok:
        return RiskDecision(decision=DECISION_REJECT, tier=TIER_L3,
                            reason=REASON_PREVIEW_FAILED, facts={},
                            message=preview.message or "这笔转账没能通过预检")
    tier = str(preview.facts.get("tier") or TIER_L2)
    factors = list(preview.facts.get("factors") or [])
    base_facts = {"tier": tier, "factors": factors,
                  "payee_name": payee.get("name"), "amount": int(amount_cents),
                  "amount_yuan": f"{int(amount_cents) / 100:.2f}"}

    if not payee.get("is_whitelist"):
        return RiskDecision(decision=DECISION_NEED_OTP, tier=tier, reason=REASON_NON_WHITELIST,
                            facts=base_facts)

    remaining = payee_dao.remaining_quota_cents(dict(payee), month or month_prefix())
    quota = payee.get("whitelist_quota")
    facts = {**base_facts, "whitelist_quota": quota, "quota_remaining": remaining}
    if quota is None:
        return RiskDecision(decision=DECISION_NEED_OTP, tier=tier, reason=REASON_NO_QUOTA,
                            facts=facts)
    if remaining is not None and int(amount_cents) <= remaining:
        return RiskDecision(decision=DECISION_ALLOW, tier=TIER_L1, reason=REASON_WITHIN_QUOTA,
                            facts=facts)
    return RiskDecision(decision=DECISION_NEED_OTP, tier=tier, reason=REASON_QUOTA_EXCEEDED,
                        facts=facts)
