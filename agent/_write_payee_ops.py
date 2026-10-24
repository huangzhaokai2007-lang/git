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


def _resolve(slots: Mapping) -> tuple[dict, str | None, str, list[dict]]:
    """把「用户说的那个人」解析成 payee_id。返回 `(filled, 错误码, 文案, 同名候选)`。

    2026-10-24 修：同名时此前直接回「请补充收款人」，**丢了候选**（界面上没有解析卡可点）。
    现在把候选带出来，调用方放进 `ShapeResult.candidates` → 界面出解析卡。
    """
    filled = dict(slots)
    if not filled.get("payee_id"):
        query = _payee_query(filled.get("payee"))
        if not query:
            return filled, None, "", []
        person, code, facts = payee_resolve.resolve_or_explain(query, allow_blacklisted=True)
        if code and code != "INVALID_STATE":
            return filled, code, str(facts.get("message") or ""), []
        if person is None:
            candidates = [c for c in (facts.get("candidates") or []) if isinstance(c, dict)]
            if candidates:                                 # 同名/同音多命中 → 交给解析卡
                names = "、".join(f"{c['name']}（{c['phone']}）" for c in candidates)
                return filled, None, f"找到 {len(candidates)} 个同名收款人，请确认是哪一位：{names}", candidates
            return filled, None, "", []
        filled["payee_id"] = person["payee_id"]
    return filled, None, "", []


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
    filled, code, text, candidates = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    missing: list[str] = []
    if not filled.get("payee_id"):
        missing.append("payee")
    picked = candidates if not filled.get("payee_id") else []
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
    return ShapeResult(filled=filled, missing=missing, clarify=clarify, candidates=picked)


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
    filled, code, text, candidates = _resolve(slots)
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
    filled, code, text, candidates = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    missing = [] if filled.get("payee_id") else ["payee"]
    picked = [] if filled.get("payee_id") else candidates   # 同名 → 解析卡
    if "note" not in filled:
        missing.append("note")
    return ShapeResult(filled=filled, missing=missing, candidates=picked,
                       clarify=text or ("请说明备注内容（例如「老同学，别转错」）。"
                                        if "note" in missing else ""))


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


# ---------------- 改标签（L1）（2026-10-24 人类原文「添加收款人时增加标签字段」） ----------------

TAGS_TIER = "L1"


def _split_tags(raw: object) -> list[str]:
    """「妈妈、房东」/「妈妈,房东」/ 列表 → 干净的标签列表（去空、去重、保序）。"""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        text = str(raw or "")
        for sep in ("、", ",", "，", ";", "；", " "):
            text = text.replace(sep, ",")
        parts = text.split(",")
    out: list[str] = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in out:
            out.append(tag)
    return out


def _shape_tags(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：谁 + 标签（说了就改，没说就问）。"""
    filled, code, text, candidates = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    missing = [] if filled.get("payee_id") else ["payee"]
    picked = [] if filled.get("payee_id") else candidates   # 同名 → 解析卡
    if filled.get("tags") is None and filled.get("tag") is None:
        missing.append("tags")
    else:
        filled["tags"] = _split_tags(filled.get("tags") if filled.get("tags") is not None
                                     else filled.get("tag"))
    return ShapeResult(filled=filled, missing=missing, candidates=picked,
                       clarify=text or ("请说明标签（例如「妈妈、家人」）。"
                                        if "tags" in missing else ""))


def _prepare_tags(filled: dict) -> Prepared:
    row, code = _row_of(str(filled.get("payee_id") or ""))
    if row is None:
        return Prepared(ok=False, error_code=code or "NOT_FOUND", message="找不到该收款人")
    return Prepared(ok=True, facts={**row, "tier": TAGS_TIER}, credential="")


def _card_tags(intent: str, filled: dict, facts: Mapping, tier: str,
               requires_otp: bool) -> confirm_card.CardInput:
    current = "、".join(facts.get("tags") or []) or "（无）"
    return confirm_card.CardInput(
        title="修改标签确认卡", intent_cn="修改标签",
        rows=[("收款人", str(facts["name"])), ("当前标签", current),
              ("新标签", "、".join(filled.get("tags") or []) or "（清空）")],
        tier=tier, requires_otp=requires_otp, risk_note=confirm_card.risk_note([], "修改标签"))


def _plain_tags(filled: dict, facts: Mapping, tier: str) -> str:
    return (f"请确认：把 {facts.get('name')} 的标签改成"
            f"「{'、'.join(filled.get('tags') or []) or '清空'}」，权限档 {tier}。")


def _execute_tags(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    del otp
    return payee.set_payee_tags(str(confirmation.filled["payee_id"]),
                                list(confirmation.filled.get("tags") or []))


# ---------------- 记卡号（L1）（2026-10-24：用户实测「记一下卡号」应有入口） ----------------

CARD_NO_TIER = "L1"


def _shape_card_no(slots: Mapping) -> ShapeResult:
    filled, code, text, candidates = _resolve(slots)
    if code:
        return ShapeResult(filled=filled, error_code=code, message=text)
    missing = [] if filled.get("payee_id") else ["payee"]
    picked = [] if filled.get("payee_id") else candidates   # 同名 → 解析卡
    if not str(filled.get("card_no") or "").strip():
        missing.append("card_no")
    return ShapeResult(filled=filled, missing=missing, candidates=picked,
                       clarify=text or ("请说明卡号（只保存脱敏形态，如 **** **** **** 8888）。"
                                        if "card_no" in missing else ""))


def _prepare_card_no(filled: dict) -> Prepared:
    row, code = _row_of(str(filled.get("payee_id") or ""))
    if row is None:
        return Prepared(ok=False, error_code=code or "NOT_FOUND", message="找不到该收款人")
    #: 卡面要印脱敏卡号（含后四位数字）→ 必须先放进事实包（数字护栏要求）。
    digits = "".join(ch for ch in str(filled.get("card_no") or "") if ch.isdigit())
    facts = {**row, "tier": CARD_NO_TIER}
    if digits:
        facts["card_no_masked"] = f"**** **** **** {digits[-4:]}"
    return Prepared(ok=True, facts=facts, credential="")


def _card_card_no(intent: str, filled: dict, facts: Mapping, tier: str,
                  requires_otp: bool) -> confirm_card.CardInput:
    masked = str(facts.get("card_no_masked") or "（清除）")
    return confirm_card.CardInput(
        title="记录卡号确认卡", intent_cn="记录卡号",
        rows=[("收款人", str(facts["name"])), ("当前卡号", str(facts.get("card_no") or "（无）")),
              ("新卡号（脱敏）", masked), ("说明", "只保存后四位，完整卡号不入库")],
        tier=tier, requires_otp=requires_otp, risk_note=confirm_card.risk_note([], "记录卡号"))


def _plain_card_no(filled: dict, facts: Mapping, tier: str) -> str:
    return f"请确认：记录 {facts.get('name')} 的银行卡号（只存脱敏形态），权限档 {tier}。"


def _execute_card_no(confirmation: confirm_card.Confirmation, otp: str | None) -> object:
    del otp
    return payee.set_payee_card_no(str(confirmation.filled["payee_id"]),
                                   str(confirmation.filled.get("card_no") or ""))
