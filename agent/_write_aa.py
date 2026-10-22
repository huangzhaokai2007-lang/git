"""AA 收款族写描述符（T9）：**无凭证族**，L1，不落资金。

F15 卡新增（`agent/_write_common.py` 是核心契约类型，`agent/write_intents.py` 是注册表）。

**口径逐条对齐 F15 验收清单**：

| F15 验收点 | 本模块怎么满足 |
|---|---|
| 联系人去重且归属当前用户 | `_shape_aa` 逐个走 T6 `resolve_payee`（**owner 圈定**）→ 按 id 去重 |
| **确认前不发起请求** | `prepare` **不调** `create_aa_request`（那会写审计 + 生成 `request_id`），只用 `tools._aa_split` 纯算 |
| 分摊和余数规则可解释 | 与执行回执共用 `tools._aa_split` 的**同一份实现**，不自己再算 |
| 只说「发起请求」不说「对方已付款」 | `_card_aa` / `_plain_aa` 的措辞都写「发起 AA 收款」 |
| 用户可见数字来自工具事实包 | 卡面数字全部取自 `facts`，`write_flow` 的 `card_numbers_outside_facts` 守门 |

**两种缺口如实登记**：
- **无凭证 ⇒ 无工具层幂等**：T9 签名没有 `preview_token`/`confirm_ref`，重复保护只来自
  `write_flow._execute` 成功后的 `confirm_card.clear(session_id)` 一次性消费（同 T16 口径）。
- **不落资金**：T9 只写一条审计，不动 `balance`/`available` —— 故措辞严禁出现「已收款」。
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card, payee_clarify, payee_resolve
from agent._write_common import Prepared, ShapeResult, normalize_query, yuan_to_cents
from tools import _aa_split, transfer
from tools.schemas import ToolResult

AA_CN = "AA 收款"


def _as_list(raw: object) -> list[str]:
    """槽位容忍三种形态：列表 / 单个字符串 / 空 —— 分类器给的 `payee_ids` 未必规范。"""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    return [text] if text else []


def _shape_aa(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`payee_ids`（名单，可为姓名/手机号）→ **去重后**的 id 列表；`amount`（元）→ 整数分。

    逐个复用 T6 `resolve_payee`（F15 排期要求「同周先验 F17，现有联系人可提前使用」）：
    零命中或同名多个 → 记缺槽并**列出脱敏候选**（复用 F17 的追问文案），**绝不擅自选一个**。

    姓名在解析循环里**顺手收集**（`resolve_payee` 的候选自带 `name`）—— 不再按 id 反查：
    `dao.find_payee` 只按姓名/手机号/银行匹配，**不认 id**。
    """
    filled, missing = dict(slots), []
    picked: dict[str, str] = {}                     # id → name，顺带完成去重（T9 对重复收款人直接拒）
    clarify, unresolved = "", False
    for query in _as_list(filled.get("payee_ids")):
        # **与转账共用同一个解析关卡**（v2）：标签 / 同音 / 黑名单 / 同名追问一套口径。
        person, code, facts = payee_resolve.resolve_or_explain(normalize_query(query))
        if code:                                        # 黑名单：整笔 AA 直接拒（不当候选）
            return ShapeResult(filled=filled, error_code=code, message=str(facts.get("message") or ""))
        if person is None:
            unresolved = True
            clarify = clarify or payee_clarify.ask_for(facts)
        else:
            picked.setdefault(person["payee_id"], person["name"])
    if unresolved or not picked:
        missing.append("payee_ids")
    else:
        filled["payee_ids"] = list(picked)
        filled["aa_names"] = [picked[payee_id] for payee_id in picked]
        filled["aa_target"] = "、".join(filled["aa_names"])      # 审计与卡面用的可读主对象
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _prepare_aa(filled: dict) -> Prepared:
    """**只算不执行**：用 `tools._aa_split` 算事实包（人数/每人/余数），**不调 `create_aa_request`**。

    这样「确认与 OTP 前不发起请求」在编排层就被结构性保证 —— 真发起只在 `execute` 里。
    """
    ids = list(filled.get("payee_ids") or [])
    names = list(filled.get("aa_names") or [])
    if not ids or len(names) != len(ids):
        return Prepared(ok=False, error_code="INVALID_ARGUMENT", message="收款人名单不完整，请重新提供")
    payees = [{"id": pid, "name": name} for pid, name in zip(ids, names)]
    facts = _aa_split.aa_facts(payees, int(filled["amount_cents"]))
    #: v2 风控：AA **含非白名单人员 → 要验证码**（发起人验一次，不是每人一次）。
    #: 黑名单已在 `_shape_aa` 拦下；这里只判白名单与否。
    non_whitelist = []
    for pid in ids:
        person = payee_resolve.profile_of(pid)
        if person and not person.get("is_whitelist"):
            non_whitelist.append(str(person["name"]))
    if non_whitelist:
        facts["tier"] = "L2"
        facts["aa_note"] = f"含非白名单收款人（{'、'.join(non_whitelist)}），需要验证码"
    facts["target_name"] = filled["aa_target"]
    return Prepared(ok=True, facts=facts, credential="")


def _card_aa(intent: str, filled: dict, facts: Mapping, tier: str,
             requires_otp: bool) -> confirm_card.CardInput:
    """AA 确认卡：人数 / 总额 / 每人 / 余数**全部来自 facts**（与执行回执同一份口径）。"""
    note = confirm_card.risk_note([], AA_CN)
    if facts.get("aa_note"):
        note = "；".join(x for x in (note, str(facts["aa_note"])) if x)
    return confirm_card.CardInput(
        title="AA 收款确认卡", intent_cn=AA_CN,
        rows=[("收款人", f"{facts['target_name']}（{facts['payee_count']} 人）"),
              ("总金额", f"{facts['amount_yuan']} 元"),
              ("每人", f"{facts['per_person_amount_yuan']} 元"),
              ("余数（由你承担）", f"{facts['initiator_share_yuan']} 元")],
        tier=tier, requires_otp=requires_otp,
        risk_note=note)


def _plain_aa(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（AA 版降级回执）。"""
    return (f"请确认：向 {facts.get('target_name')}（{facts.get('payee_count')} 人）发起 AA 收款 "
            f"{facts.get('amount_yuan')} 元，每人 {facts.get('per_person_amount_yuan')} 元，"
            f"权限档 {tier}。")


def _execute_aa(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """T9 真发起：**无凭证**（T9 签名如此），故直接用槽位快照里的 id 列表与金额。"""
    del otp
    return transfer.create_aa_request(list(confirmation.filled.get("payee_ids") or []),
                                      int(confirmation.amount_cents))
