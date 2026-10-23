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

import re
from typing import Mapping

from agent import confirm_card, payee_clarify, payee_resolve
from agent._write_common import Prepared, ShapeResult, normalize_query, yuan_to_cents
from tools import _aa_split, transfer
from tools.schemas import ToolResult

AA_CN = "AA 收款"

#: 「王五 100 元、赵六 200 元」→ (名字, 元数) 的确定性解析（不赌 LLM）
_AMOUNT_PAIR = re.compile(r"^(.+?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?$")


def _as_list(raw: object) -> list[str]:
    """槽位容忍三种形态：列表 / 单个字符串 / 空 —— 分类器给的 `payee_ids` 未必规范。"""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    return [text] if text else []


def _parse_per_person(raw: object, picked: dict[str, str]) -> tuple[list[int] | None, str | None]:
    """「王五 100 元、赵六 200 元」→ 按 `picked` 顺序的**整数分**列表；解析不了 → `(None, 原因)`。

    名字必须恰好是这次 AA 名单里的人（同名歧义在外面已解决，这里只做精确匹配）；
    金额用 `yuan_to_cents`（与转账同一份解析，禁浮点）。
    """
    text = str(raw or "").strip()
    if not text:
        return None, None
    name_to_cents: dict[str, int] = {}
    for seg in re.split(r"[、,，;；]", text):
        seg = seg.strip()
        if not seg:
            continue
        match = _AMOUNT_PAIR.match(seg)
        if match is None:
            return None, f"「{seg}」看不懂，请写成「王五 100 元、赵六 200 元」的样子"
        name = match.group(1).strip()
        if name not in picked.values():
            return None, f"「{name}」不在这次 AA 的名单里"
        cents = yuan_to_cents(match.group(2))
        if cents is None:
            return None, f"「{seg}」的金额看不懂"
        name_to_cents[name] = cents
    if set(name_to_cents) != set(picked.values()):
        lacking = [n for n in picked.values() if n not in name_to_cents]
        return None, f"还缺 {'、'.join(lacking)} 的金额"
    return [name_to_cents[name] for name in picked.values()], None


def _shape_aa(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`payee_ids`（名单，可为姓名/手机号/标签）→ **去重后**的 id 列表；`amount` → 整数分。

    逐个走统一解析关卡（标签 / 同音 / 黑名单一套口径）：零命中或同名多个 → 记缺槽并列出
    脱敏候选，**绝不擅自选一个**；黑名单命中 → 整笔 AA 直接拒（人类需求：黑名单不能收款）。

    可选 `amounts` 槽位（「王五 100 元、赵六 200 元」）：按人金额；没报总额时总额 = 合计；
    报了总额且与合计不符 → 直接报错。
    """
    filled, missing = dict(slots), []
    picked: dict[str, str] = {}                     # id → name，顺带完成去重（T9 对重复收款人直接拒）
    clarify, unresolved = "", False
    for query in _as_list(filled.get("payee_ids")):
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
    if picked and filled.get("per_person_cents") is None and filled.get("amounts"):
        per_person, err = _parse_per_person(filled["amounts"], picked)
        if err is not None:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT", message=err)
        filled["per_person_cents"] = per_person
        if filled.get("amount") is None and filled.get("amount_cents") is None:
            filled["amount_cents"] = sum(per_person)             # 没报总额 → 按人金额的合计
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    if filled.get("per_person_cents") and filled.get("amount_cents") is not None:
        total = sum(filled["per_person_cents"])
        if total != filled["amount_cents"]:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT",
                               message=f"每人金额合计 {total / 100:.2f} 元，与总金额 "
                                       f"{filled['amount_cents'] / 100:.2f} 元不符，请改一个")
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _prepare_aa(filled: dict) -> Prepared:
    """**只算不执行**：用 `tools._aa_split` 算事实包，**不调** `create_aa_request`。

    「确认前不发起请求」由此在编排层被结构性保证 —— 真发起只在 `execute` 里。
    AA 是**收款**方向（钱进来不是出去）→ L1、免验证码（人类需求：收款不用验证码）；
    黑名单拦截已在 `_shape_aa` 完成。
    """
    ids = list(filled.get("payee_ids") or [])
    names = list(filled.get("aa_names") or [])
    if not ids or len(names) != len(ids):
        return Prepared(ok=False, error_code="INVALID_ARGUMENT", message="收款人名单不完整，请重新提供")
    payees = [{"id": pid, "name": name} for pid, name in zip(ids, names)]
    facts = _aa_split.aa_facts(payees, int(filled["amount_cents"]),
                               per_person=filled.get("per_person_cents"))
    facts["target_name"] = filled["aa_target"]
    return Prepared(ok=True, facts=facts, credential="")


def _card_aa(intent: str, filled: dict, facts: Mapping, tier: str,
             requires_otp: bool) -> confirm_card.CardInput:
    """AA 确认卡：人数 / 总额 / 每人（一键均摊 或 按人明细）**全部来自 facts**。"""
    if facts.get("split_mode") == "custom":
        rows = [("收款人", f"{facts['target_name']}（{facts['payee_count']} 人）"),
                ("总金额", f"{facts['amount_yuan']} 元")]
        rows += [(f"　{line['name']}", f"{line['amount_yuan']} 元")
                 for line in facts["per_person_lines"]]
        rows.append(("分摊方式", "按人金额"))
    else:
        rows = [("收款人", f"{facts['target_name']}（{facts['payee_count']} 人）"),
                ("总金额", f"{facts['amount_yuan']} 元"),
                ("每人", f"{facts['per_person_amount_yuan']} 元（一键均摊）"),
                ("余数（由你承担）", f"{facts['initiator_share_yuan']} 元")]
    return confirm_card.CardInput(
        title="AA 收款确认卡", intent_cn=AA_CN, rows=rows,
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], AA_CN))


def _plain_aa(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（AA 版降级回执）。"""
    if facts.get("split_mode") == "custom":
        detail = "，".join(f"{line['name']} {line['amount_yuan']} 元"
                           for line in facts["per_person_lines"])
        return (f"请确认：向 {facts.get('target_name')}（{facts.get('payee_count')} 人）"
                f"发起 AA 收款 {facts.get('amount_yuan')} 元（{detail}），权限档 {tier}。")
    return (f"请确认：向 {facts.get('target_name')}（{facts.get('payee_count')} 人）发起 AA 收款 "
            f"{facts.get('amount_yuan')} 元，每人 {facts.get('per_person_amount_yuan')} 元（一键均摊），"
            f"权限档 {tier}。")


def _execute_aa(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """真发起：按人金额 → **T31**；一键均摊 → **T9**（签名冻结，按人金额走新工具）。"""
    del otp
    per_person = confirmation.filled.get("per_person_cents")
    if per_person:
        return _aa_split.create_aa_request_with_amounts(
            list(confirmation.filled.get("payee_ids") or []), list(per_person))
    return transfer.create_aa_request(list(confirmation.filled.get("payee_ids") or []),
                                      int(confirmation.amount_cents))
