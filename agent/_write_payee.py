"""收款人白名单族写描述符（T25 `set_payee_whitelist`）· `confirm_ref` 族 · **L2 + OTP**。

**为什么是 L2 而不是 L1**（人类已批）：白名单 = 转账时只做会话内确认、**不再要短信验证码**。
若「加收款人」（L1、不要 OTP）能顺带授予它，账户一旦被盗，攻击者只要把自己加进白名单，
就能**永久绕过 OTP**。所以「授予这个特权」必须单独验证身份。

**参照实现**：本族与 `agent/_write_subscription.py`（T11，同为 `confirm_ref` + L2）**逐项同构** ——
那一族已被实测证明「出卡 → 确认 → 进验证码阶段」链路正确（`inflight()` 能读到在途确认）。
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card, payee_clarify, payee_resolve
from agent._write_common import Prepared, ShapeResult, _code_of
from agent._write_transfer import _payee_query                     # 同一份「回显串归一化」
from tools import payee


def _resolve_shape(slots: Mapping, *, allow_blacklisted: bool = False) -> ShapeResult:
    """SLOT_FILL：收款人（姓名/手机号/**标签/同音**）→ `payee_id`。

    v2：与转账**同一份解析关卡**（`payee_resolve.resolve_or_explain`）—— 标签、同音、
    黑名单一套口径（原先只走 T6 子串，「把房东加入白名单」会解析失败，实测缺陷）。
    `allow_blacklisted` 供「加黑/解除黑名单」这类**管理状态**的意图用：黑名单的人也要
    能被解析出来（转账路径里黑名单是 FORBIDDEN，这里不是）。
    """
    filled, missing, clarify = dict(slots), [], ""
    if not filled.get("payee_id"):
        query = _payee_query(filled.get("payee"))
        person, code, facts = payee_resolve.resolve_or_explain(
            query, allow_blacklisted=allow_blacklisted)
        if code:
            return ShapeResult(filled=filled, error_code=code,
                               message=str(facts.get("message") or ""))
        if person is None:
            missing.append("payee")
            clarify = payee_clarify.ask_for(facts, ["payee"])
        else:
            filled["payee_id"] = person["payee_id"]
            filled["masked_phone"] = person["masked_phone"]
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _shape_whitelist(slots: Mapping) -> ShapeResult:
    """加白：统一解析关卡。**已黑名单的人 → FORBIDDEN**（黑白互斥，先解除再谈加白）。"""
    return _resolve_shape(slots)


def _prepare_whitelist(filled: dict) -> Prepared:
    """只读 T20 取那一笔 → 事实包 + 签发 `confirm_ref`（**本步不写库**，写库在 EXECUTE）。"""
    listed = payee.list_payees()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    row = next((i for i in (listed.facts.get("items") or [])
                if i.get("payee_id") == filled.get("payee_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该收款人")
    if row.get("is_whitelist"):
        return Prepared(ok=False, error_code="INVALID_STATE", message="该收款人已经在白名单里了")
    ref = payee.issue_confirm_ref(payee.WHITELIST_ACTION,
                                  payee.whitelist_target(str(row["payee_id"]), True))
    return Prepared(ok=True, facts={**row, "tier": payee.WHITELIST_TIER}, credential=ref)


def _card_whitelist(intent: str, filled: dict, facts: Mapping, tier: str,
                    requires_otp: bool) -> confirm_card.CardInput:
    """白名单确认卡：谁 + 什么变化 + **影响**（专属额度内免码 —— v2 口径，必须说清额度边界）。"""
    return confirm_card.CardInput(
        title="白名单确认卡", intent_cn="加入白名单",
        rows=[("收款人", str(facts["name"])),
              ("手机号", str(facts["masked_phone"])),
              ("变更", "加入白名单"),
              ("影响", "专属额度内转账免验证码（含深夜）；超额度仍需验证码")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "加入白名单"))


def _plain_whitelist(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。措辞与卡面一致：额度内免码、超额度要码。"""
    return (f"请确认：把 {facts.get('name')}（{facts.get('masked_phone')}）加入白名单，"
            f"权限档 {tier}。加入后在专属额度内给该收款人转账免验证码（超额度仍需验证码）。")


def _execute_whitelist(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：调 **T25** 落库（OTP 已由 `write_flow` 校验；工具层只认 `confirm_ref`）。"""
    del otp
    return payee.set_payee_whitelist(str(confirmation.target_id), True, confirmation.credential)


# ---------------- 加入黑名单（T28 · L1 确认卡、无 OTP） ----------------

#: 档位：加黑是「阻断」不是「授权」（禁止转账/送礼），风险低于加白；人类需求只要求
#: 「解除黑名单」要验证码（后续卡做），没要求「加黑」要验证码 → L1、不发 OTP。
BLACKLIST_TIER = "L1"

#: 与加白**同一套解析口径**（标签/同音/黑名单），不复制实现
#: 与加白同一套解析关卡，但**允许解析已黑名单的人**（重复拉黑由 prepare 报「已经在黑名单里」）
_shape_blacklist = lambda slots: _resolve_shape(slots, allow_blacklisted=True)


def _prepare_blacklist(filled: dict) -> Prepared:
    """只读 T20 取那一笔（**本步不写库**，写库在 EXECUTE）；已在黑名单 → `INVALID_STATE`。"""
    listed = payee.list_payees()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    row = next((i for i in (listed.facts.get("items") or [])
                if i.get("payee_id") == filled.get("payee_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该收款人")
    profile = (listed.facts.get("profiles") or {}).get(str(row["payee_id"])) or {}
    if profile.get("blacklist"):
        return Prepared(ok=False, error_code="INVALID_STATE", message="该收款人已经在黑名单里了")
    return Prepared(ok=True, facts={**row, "tier": BLACKLIST_TIER}, credential="")


def _card_blacklist(intent: str, filled: dict, facts: Mapping, tier: str,
                    requires_otp: bool) -> confirm_card.CardInput:
    """黑名单确认卡：谁 + 变更 + **影响**（之后转账/AA/送礼都会被拒，这句必须说清）。"""
    return confirm_card.CardInput(
        title="黑名单确认卡", intent_cn="加入黑名单",
        rows=[("收款人", str(facts["name"])),
              ("手机号", str(facts["masked_phone"])),
              ("变更", "加入黑名单"),
              ("影响", "之后给该收款人转账、AA、送礼都会被拒绝")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "加入黑名单"))


def _plain_blacklist(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    return (f"请确认：把 {facts.get('name')}（{facts.get('masked_phone')}）加入黑名单，"
            f"权限档 {tier}。之后将禁止给他转账或送礼。")


def _execute_blacklist(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：调 **T28** 落库（`blacklist=1` 且清 `is_whitelist`，互斥在工具层强制）。"""
    del otp
    return payee.set_payee_blacklist(str(confirmation.target_id), "")


# ---------------- 解除黑名单（T29 · L2 + OTP） ----------------

#: 与加白/加黑同一套解析（T6 按姓名/手机号子串匹配；**黑名单的人同样能被解析出来**，
#: 这正是解除黑名单需要的 —— 转账那条路径里黑名单是 FORBIDDEN，这里不是）。
#: 同一套解析关卡，**必须**允许解析已黑名单的人（否则「解除黑名单」永远解不到人）
_shape_unblacklist = lambda slots: _resolve_shape(slots, allow_blacklisted=True)


def _prepare_unblacklist(filled: dict) -> Prepared:
    """只读 T20 取那一笔（**不写库**）；**不在黑名单 → `INVALID_STATE`**；签发 `confirm_ref`。"""
    listed = payee.list_payees()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    row = next((i for i in (listed.facts.get("items") or [])
                if i.get("payee_id") == filled.get("payee_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该收款人")
    profile = (listed.facts.get("profiles") or {}).get(str(row["payee_id"])) or {}
    if not profile.get("blacklist"):
        return Prepared(ok=False, error_code="INVALID_STATE", message="该收款人不在黑名单里")
    ref = payee.issue_confirm_ref(payee.UNBLACKLIST_ACTION, f"{row['payee_id']}:unblacklist")
    return Prepared(ok=True, facts={**row, "tier": payee.UNBLACKLIST_TIER}, credential=ref)


def _card_unblacklist(intent: str, filled: dict, facts: Mapping, tier: str,
                      requires_otp: bool) -> confirm_card.CardInput:
    """解除黑名单确认卡：谁 + 变更 + **影响**（解除后按普通收款人处理）。"""
    return confirm_card.CardInput(
        title="解除黑名单确认卡", intent_cn="解除黑名单",
        rows=[("收款人", str(facts["name"])),
              ("手机号", str(facts["masked_phone"])),
              ("变更", "解除黑名单"),
              ("影响", "解除后恢复为普通收款人，转账/送礼按普通风控处理")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "解除黑名单"))


def _plain_unblacklist(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    return (f"请确认：把 {facts.get('name')}（{facts.get('masked_phone')}）移出黑名单，"
            f"权限档 {tier}。解除后按普通收款人处理。")


def _execute_unblacklist(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：调 **T29**（OTP 已由 `write_flow` 校验；工具层只认 `confirm_ref`）。"""
    del otp
    return payee.unset_payee_blacklist(str(confirmation.target_id), confirmation.credential)
