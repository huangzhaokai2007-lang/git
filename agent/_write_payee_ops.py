"""收款人的**信息维护**三个族（2026-10-24 追加，人类原文）：

| 意图 | 工具 | 档位 | 说明 |
|---|---|---|---|
| `payee_whitelist_edit`（修改白名单信息） | T36 有效期 + T46 专属额度 | **L2**（人类原文：修改白名单信息需要验证码） | 至少改一项；未改的保持原值 |
| `payee_whitelist_remove`（移除白名单） | T25 `set_payee_whitelist(…, False)` | **L1** | 移除后恢复为普通收款人 |
| `payee_note`（改备注） | T45 `set_payee_note` | **L1** | 纯说明文字，不参与风控 |

都从只读 T20 取那一笔（不直查库）；形状/事实包/卡面纯函数，执行才写库。
"""

from __future__ import annotations

import datetime
from typing import Mapping

from agent import _relative_time, confirm_card, payee_resolve
from agent._write_common import Prepared, ShapeResult, _code_of, yuan_to_cents
from agent._write_transfer import _payee_query
from tools import payee, transfer

EDIT_TIER, REMOVE_TIER, NOTE_TIER = "L2", "L1", "L1"
EDIT_CLARIFY = "请说明要改什么：专属额度（例如「额度改成 5000 元」）或有效期（例如「有效期到 2026-12-31」）。"


def _row_of(payee_id: str) -> tuple[dict | None, str | None]:
    """只读 T20 取那一笔（含白名单画像）。"""
    listed = payee.list_payees()
    if not listed.ok:
        return None, _code_of(listed)
    row = next((i for i in (listed.facts.get("items") or [])
                if i.get("payee_id") == payee_id), None)
    if row is None:
        return None, "NOT_FOUND"
    profiles = listed.facts.get("profiles") or {}
    return {**row, **(profiles.get(payee_id) or {})}, None


def _resolve(slots: Mapping) -> tuple[dict, str | None, str]:
    """把「用户说的那个人」解析成 payee_id，并取回那一行。返回 `(filled, 错误码, 文案)`。"""
    filled = dict(slots)
    if not filled.get("payee_id"):
        query = _payee_query(filled.get("payee"))
        if not query:
            return filled, None, ""
        person, code, facts = payee_resolve.resolve_or_explain(query, allow_blacklisted=True)
        if code and code != "INVALID_STATE":
            return filled, code, str(facts.get("message") or "")
        if person is None:
            return filled, None, "找不到这位收款人"
        filled["payee_id"] = person["payee_id"]
    return filled, None, ""


def _iso_day(raw: object) -> str | None:
    """有效期：ISO 日期优先，也接受「月底 / 明年 3 月」这类相对说法（复用全仓唯一实现）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        when = _relative_time.parse(text, transfer._now())
        return when[:10] if when else None


# ---------------- 修改白名单信息（额度 / 有效期，L2） ----------------

def _shape_edit(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：谁 + **改什么**（额度 / 有效期至少一项）。"""
    filled, code, text = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    missing: list[str] = []
    if not filled.get("payee_id"):
        missing.append("payee")
    row, row_code = _row_of(str(filled.get("payee_id") or ""))
    if row is not None and not row.get("is_whitelist"):
        return ShapeResult(filled=filled, error_code="INVALID_STATE",
                           message=f"{row['name']} 不在白名单里，谈不上修改白名单信息")
    if filled.get("quota") is not None:
        filled["quota_cents"] = yuan_to_cents(filled["quota"])
    expire = _iso_day(filled.get("expire")) if filled.get("expire") else None
    if expire:
        filled["expire"] = expire
    if "payee" not in missing and not (filled.get("quota_cents") is not None or expire):
        missing.append("quota")
    clarify = EDIT_CLARIFY if "quota" in missing else ""
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _prepare_edit(filled: dict) -> Prepared:
    """只读那一笔 + 现状（当前额度/有效期），供卡面「原值 → 新值」对照。"""
    row, code = _row_of(str(filled.get("payee_id") or ""))
    if row is None:
        return Prepared(ok=False, error_code=code or "NOT_FOUND", message="找不到该收款人")
    facts = {**row, "tier": EDIT_TIER,
             "new_quota": filled.get("quota_cents"), "new_expire": filled.get("expire")}
    return Prepared(ok=True, facts=facts, credential="")


def _card_edit(intent: str, filled: dict, facts: Mapping, tier: str,
               requires_otp: bool) -> confirm_card.CardInput:
    rows = [("收款人", str(facts["name"])), ("当前专属额度", _money_label(facts.get("whitelist_quota"))),
            ("当前有效期", str(facts.get("whitelist_expire") or "长期有效"))]
    if facts.get("new_quota") is not None:
        rows.append(("新的专属额度", _money_label(facts["new_quota"])))
    if facts.get("new_expire"):
        rows.append(("新的有效期", str(facts["new_expire"])))
    return confirm_card.CardInput(
        title="修改白名单信息确认卡", intent_cn="修改白名单信息", rows=rows, tier=tier,
        requires_otp=requires_otp, risk_note=confirm_card.risk_note([], "修改白名单信息"))


def _money_label(cents: object) -> str:
    return "未设（一律要验证码）" if cents in (None, 0) else f"{int(cents) / 100:.2f} 元"


def _plain_edit(filled: dict, facts: Mapping, tier: str) -> str:
    return (f"请确认：修改 {facts.get('name')} 的白名单信息"
            f"（额度 {_money_label(facts.get('new_quota'))}，有效期 {facts.get('new_expire') or '不变'}），"
            f"权限档 {tier}。")


def _execute_edit(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：先 T36（有效期，若给了）再 T46（额度，若给了）。"""
    del otp
    payee_id = str(confirmation.filled["payee_id"])
    expire = str(confirmation.filled.get("expire") or "")
    quota = confirmation.filled.get("quota_cents")
    result = None
    if expire:
        result = payee.set_whitelist_expire(payee_id, expire)
        if not result.ok:
            return result
    if quota is not None:
        result = payee.set_whitelist_quota(payee_id, int(quota))
        if not result.ok:
            return result
    return result


# ---------------- 移除白名单（L1） ----------------

def _shape_remove(slots: Mapping) -> ShapeResult:
    filled, code, text = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    if not filled.get("payee_id"):
        return ShapeResult(filled=filled, missing=["payee"],
                           clarify="请说明要移出白名单的是谁。")
    row, row_code = _row_of(str(filled["payee_id"]))
    if row is None:
        return ShapeResult(filled=filled, error_code=row_code or "NOT_FOUND", message="找不到该收款人")
    if not row.get("is_whitelist"):
        return ShapeResult(filled=filled, error_code="INVALID_STATE",
                           message=f"{row['name']} 本来就不在白名单里")
    return ShapeResult(filled=filled)


def _prepare_remove(filled: dict) -> Prepared:
    row, code = _row_of(str(filled.get("payee_id") or ""))
    if row is None:
        return Prepared(ok=False, error_code=code or "NOT_FOUND", message="找不到该收款人")
    return Prepared(ok=True, facts={**row, "tier": REMOVE_TIER}, credential="")


def _card_remove(intent: str, filled: dict, facts: Mapping, tier: str,
                 requires_otp: bool) -> confirm_card.CardInput:
    return confirm_card.CardInput(
        title="移出白名单确认卡", intent_cn="移出白名单",
        rows=[("收款人", str(facts["name"])), ("手机号", str(facts["masked_phone"])),
              ("变更", "移出白名单"), ("影响", "之后转账按普通收款人处理，需要验证码")],
        tier=tier, requires_otp=requires_otp, risk_note=confirm_card.risk_note([], "移出白名单"))


def _plain_remove(filled: dict, facts: Mapping, tier: str) -> str:
    return (f"请确认：把 {facts.get('name')} 移出白名单（之后转账需要验证码），权限档 {tier}。")


def _execute_remove(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    """EXECUTE：T25 `set_payee_whitelist(payee_id, False, confirm_ref)` —— 需先签发 ref。"""
    del otp
    payee_id = str(confirmation.filled["payee_id"])
    ref = payee.issue_confirm_ref(payee.WHITELIST_ACTION,
                                  payee.whitelist_target(payee_id, False))
    return payee.set_payee_whitelist(payee_id, False, ref)


# ---------------- 改备注（L1） ----------------

def _shape_note(slots: Mapping) -> ShapeResult:
    filled, code, text = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    missing = [] if filled.get("payee_id") else ["payee"]
    if "note" not in filled:
        missing.append("note")
    return ShapeResult(filled=filled, missing=missing,
                       clarify="请说明备注内容（例如「老同学，别转错」）。" if "note" in missing else "")


def _prepare_note(filled: dict) -> Prepared:
    row, code = _row_of(str(filled.get("payee_id") or ""))
    if row is None:
        return Prepared(ok=False, error_code=code or "NOT_FOUND", message="找不到该收款人")
    return Prepared(ok=True, facts={**row, "tier": NOTE_TIER}, credential="")


def _card_note(intent: str, filled: dict, facts: Mapping, tier: str,
               requires_otp: bool) -> confirm_card.CardInput:
    return confirm_card.CardInput(
        title="修改备注确认卡", intent_cn="修改备注",
        rows=[("收款人", str(facts["name"])),
              ("原备注", str(facts.get("note") or "（无）")),
              ("新备注", str(filled.get("note") or "（清除）"))],
        tier=tier, requires_otp=requires_otp, risk_note=confirm_card.risk_note([], "修改备注"))


def _plain_note(filled: dict, facts: Mapping, tier: str) -> str:
    return (f"请确认：把 {facts.get('name')} 的备注改成「{filled.get('note') or '（清除）'}」，"
            f"权限档 {tier}。")


def _execute_note(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    del otp
    return payee.set_payee_note(str(confirmation.filled["payee_id"]),
                                str(confirmation.filled.get("note") or ""))
