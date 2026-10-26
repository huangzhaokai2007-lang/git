"""收款人白名单族写描述符（T25 `set_payee_whitelist`）· `confirm_ref` 族 · **L2 + OTP**。

**为什么是 L2 而不是 L1**（人类已批）：白名单 = 转账时只做会话内确认、**不再要短信验证码**。
若「加收款人」（L1、不要 OTP）能顺带授予它，账户一旦被盗，攻击者只要把自己加进白名单，
就能**永久绕过 OTP**。所以「授予这个特权」必须单独验证身份。

**参照实现**：本族与 `agent/_write_subscription.py`（T11，同为 `confirm_ref` + L2）**逐项同构** ——
那一族已被实测证明「出卡 → 确认 → 进验证码阶段」链路正确（`inflight()` 能读到在途确认）。
"""

from __future__ import annotations

import datetime
from typing import Mapping

from agent import _relative_time, confirm_card, payee_clarify, payee_resolve
from agent._write_common import Prepared, ShapeResult, _code_of, yuan_to_cents
from agent._write_transfer import _payee_query                     # 同一份「回显串归一化」
from tools import payee, transfer


def _resolve_shape(slots: Mapping, *, allow_blacklisted: bool = False) -> ShapeResult:
    """SLOT_FILL：收款人（姓名/手机号/**标签/同音**）→ `payee_id`。

    v2：与转账**同一份解析关卡**（`payee_resolve.resolve_or_explain`）—— 标签、同音、
    黑名单一套口径（原先只走 T6 子串，「把房东加入白名单」会解析失败，实测缺陷）。
    `allow_blacklisted` 供「加黑/解除黑名单」这类**管理状态**的意图用：黑名单的人也要
    能被解析出来（转账路径里黑名单是 FORBIDDEN，这里不是）。
    """
    filled, missing, clarify, candidates = dict(slots), [], "", []
    if not filled.get("payee_id"):
        query = _payee_query(filled.get("payee"))
        person, code, facts = payee_resolve.resolve_or_explain(
            query, allow_blacklisted=allow_blacklisted)
        if code:
            return ShapeResult(filled=filled, error_code=code,
                               message=str(facts.get("message") or ""))
        if person is None:
            missing.append("payee")
            candidates = list(facts.get("candidates") or [])      # 同名多命中 → 界面出解析卡
            clarify = payee_clarify.ask_for(facts, ["payee"])
        else:
            filled["payee_id"] = person["payee_id"]
            filled["masked_phone"] = person["masked_phone"]
    return ShapeResult(filled=filled, missing=missing, clarify=clarify, candidates=candidates)


def _shape_whitelist(slots: Mapping) -> ShapeResult:
    """加白：统一解析关卡。**黑名单里的人也能直接加白**（置白时工具层会解除黑名单）。

    2026-10-24（人类原文「要为白名单人员设置有效时间与额度」）：可选 `quota`（元）/`expire`
    （日期或「月底」这类相对说法）两个槽位 —— 在本步归一化；缺了不报错
    （额度未设 = 一律要验证码；有效期空 = 长期有效）。
    """
    #: 2026-10-26（人类要求）：三种身份可自由切换 —— 黑名单 → 白名单一步到位。
    shaped = _resolve_shape(slots, allow_blacklisted=True)
    if shaped.error_code:                              # 其它硬拒绝：原样透传（不吞错误码）
        return shaped
    filled = dict(shaped.filled)
    if filled.get("quota") is not None and filled.get("quota_cents") is None:
        raw = str(filled["quota"]).strip()
        if raw:
            filled["quota_cents"] = yuan_to_cents(filled["quota"])
    if filled.get("expire"):
        expire = _iso_expire(filled["expire"])
        if expire is None:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT",
                               message="有效期看不懂，请用 2026-12-31 这样的日期（或说「月底」）")
        filled["expire"] = expire
    return ShapeResult(filled=filled, missing=shaped.missing, clarify=shaped.clarify,
                       candidates=shaped.candidates)


def _prepare_whitelist(filled: dict) -> Prepared:
    """只读 T20 取那一笔 → 事实包 + 签发 `confirm_ref`（**本步不写库**，写库在 EXECUTE）。

    可选 `expire` 槽位（「有效期到 2026-12-31」/「月底」）：**仅展示**，不参与风控（人类口径）。
    """
    listed = payee.list_payees()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    row = next((i for i in (listed.facts.get("items") or [])
                if i.get("payee_id") == filled.get("payee_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该收款人")
    if row.get("is_whitelist"):
        return Prepared(ok=False, error_code="INVALID_STATE", message="该收款人已经在白名单里了")
    facts = {**row, "tier": payee.WHITELIST_TIER}
    if filled.get("quota_cents") is not None:                 # 2026-10-24：加白时一并设专属额度
        facts["quota_cents"] = int(filled["quota_cents"])
    if filled.get("expire"):
        expire = _iso_expire(filled["expire"])
        if expire is None:
            return Prepared(ok=False, error_code="INVALID_ARGUMENT",
                            message="有效期看不懂，请用 2026-12-31 这样的日期（或说「月底」）")
        facts["expire"] = expire
    ref = payee.issue_confirm_ref(payee.WHITELIST_ACTION,
                                  payee.whitelist_target(str(row["payee_id"]), True))
    return Prepared(ok=True, facts=facts, credential=ref)


def _iso_expire(raw: object) -> str | None:
    """有效期：ISO 日期优先，也接受相对说法（月底/下个月5号）—— 归一成 `YYYY-MM-DD`。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        when = _relative_time.parse(text, transfer._now())
        return when[:10] if when else None


def _card_whitelist(intent: str, filled: dict, facts: Mapping, tier: str,
                    requires_otp: bool) -> confirm_card.CardInput:
    """白名单确认卡：谁 + 什么变化 + **影响**（专属额度内免码 —— v2 口径，必须说清额度边界）。"""
    return confirm_card.CardInput(
        title="白名单确认卡", intent_cn="加入白名单",
        rows=[("收款人", str(facts["name"])),
              ("手机号", str(facts["masked_phone"])),
              ("变更", "加入白名单"),
              ("影响", "白名单专属额度（每月）内转账免验证码（含深夜）；超额度仍需验证码"),
              *([("专属额度", f"{facts['quota_cents'] / 100:.2f} 元/月"
                             "（每月重置；有效期内、额度内免验证码）")]
                if facts.get("quota_cents") is not None else []),
              *([("有效期至", str(facts["expire"]))] if facts.get("expire") else [])],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "加入白名单"))


def _plain_whitelist(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。措辞与卡面一致：额度内免码、超额度要码。"""
    return (f"请确认：把 {facts.get('name')}（{facts.get('masked_phone')}）加入白名单，"
            f"权限档 {tier}。加入后在每月专属额度内给该收款人转账免验证码（超额度仍需验证码）。")


def _execute_whitelist(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：调 **T25** 落库；带有效期 → 再调 **T36** 记有效期（**仅展示**）。"""
    del otp
    result = payee.set_payee_whitelist(str(confirmation.target_id), True, confirmation.credential)
    expire = str(confirmation.filled.get("expire") or "")
    quota = confirmation.filled.get("quota_cents")
    notes: list[str] = []
    if result.ok and expire:                                  # 有效期是白名单属性，加白成功后再记
        marked = payee.set_whitelist_expire(str(confirmation.target_id), expire)
        if marked.ok:
            notes.append(f"有效期至 {expire}")
    if result.ok and quota is not None:                       # 专属额度（T46）
        marked = payee.set_whitelist_quota(str(confirmation.target_id), int(quota))
        if marked.ok:
            notes.append(f"专属额度 {int(quota) / 100:.2f} 元/月（每月重置）")
    if result.ok and notes:
        result = result.model_copy(update={"message": f"{result.message}（{'，'.join(notes)}）"})
    return result


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
    #: 2026-10-26（人类要求）：**三种身份可自由切换** —— 白名单也能直接拉黑。
    #: 工具层 T28 置 blacklist=1 时本来就会把 is_whitelist 清零（互斥），这里只把
    #: 「原本是白名单」带进事实包，让卡面能说清「白名单专属额度同时失效」。
    was_whitelist = bool(row.get("is_whitelist"))
    return Prepared(ok=True, facts={**row, "tier": BLACKLIST_TIER,
                                    "was_whitelist": was_whitelist}, credential="")


def _card_blacklist(intent: str, filled: dict, facts: Mapping, tier: str,
                    requires_otp: bool) -> confirm_card.CardInput:
    """黑名单确认卡：谁 + 变更 + **影响**（之后转账/AA/送礼都会被拒，这句必须说清）。

    2026-10-26：白名单也能直接拉黑（三态自由切换），必须明说**白名单特权同时失效**。
    """
    was_whitelist = bool(facts.get("was_whitelist") or facts.get("is_whitelist"))
    return confirm_card.CardInput(
        title="黑名单确认卡", intent_cn="加入黑名单",
        rows=[("收款人", str(facts["name"])),
              ("手机号", str(facts["masked_phone"])),
              ("变更", "加入黑名单" + ("并移出白名单" if was_whitelist else "")),
              ("影响", "之后给该收款人转账、AA、送礼都会被拒绝"
                      + ("；白名单专属额度同时失效" if was_whitelist else ""))],
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


# ---------------- 删除收款人（T30 · L1 确认卡、无 OTP） ----------------

#: 档位：删除收款人 = 移走一位**联系人**，不动钱、不授特权 → L1、无 OTP（与加黑同档）
DELETE_TIER = "L1"

#: 同一套解析关卡；**允许解析已拉黑的人**（删除对谁都允许）
_shape_delete = lambda slots: _resolve_shape(slots, allow_blacklisted=True)


def _prepare_delete(filled: dict) -> Prepared:
    """只读 T20 取那一笔（**不写库**）；白名单/黑名单现状要带进卡面讲清。"""
    listed = payee.list_payees()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    row = next((i for i in (listed.facts.get("items") or [])
                if i.get("payee_id") == filled.get("payee_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该收款人")
    profile = (listed.facts.get("profiles") or {}).get(str(row["payee_id"])) or {}
    kind = ("白名单" if row.get("is_whitelist")            # 状态在 `items` 里，profiles 只有黑名单/额度
            else "黑名单" if profile.get("blacklist") else "普通")
    return Prepared(ok=True, facts={**row, "kind": kind, "tier": DELETE_TIER}, credential="")


def _card_delete(intent: str, filled: dict, facts: Mapping, tier: str,
                 requires_otp: bool) -> confirm_card.CardInput:
    """删除确认卡：谁 + 现状 + **影响**（历史流水保留、需重新添加才能再转）。"""
    return confirm_card.CardInput(
        title="删除收款人确认卡", intent_cn="删除收款人",
        rows=[("收款人", str(facts["name"])),
              ("手机号", str(facts["masked_phone"])),
              ("现状", str(facts["kind"])),
              ("影响", "删除后无法再给他转账，需重新添加；历史流水保留、仍可查询")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "删除收款人"))


def _plain_delete(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    return (f"请确认：删除收款人 {facts.get('name')}（{facts.get('masked_phone')}，"
            f"{facts.get('kind')}），权限档 {tier}。删除后需重新添加才能再给他转账。")


def _execute_delete(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：调 **T30** 落库（只删联系人，不动历史流水）。"""
    del otp
    return payee.delete_payee(str(confirmation.target_id))
